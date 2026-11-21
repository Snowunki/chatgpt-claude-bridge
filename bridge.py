#!/usr/bin/env python3
"""ChatGPT <-> Claude Code minimal metin köprüsü.

ChatGPT tarafı: kullanıcının kendi açtığı, uzaktan hata ayıklama portu açık
ayrı bir Chrome profilindeki mevcut sohbet (Playwright, CDP ile bağlanır).
Claude tarafı: `claude -p`, tüm araçlar/MCP/ayarlar kapalı, boş bir klasörde.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

PROTOCOL = (
    "Görevi sen yönet ve tamamlanma ölçütünü ilk Claude mesajına ekle. "
    "Claude'a bir mesaj göndermek istediğinde ilk satıra TO_CLAUDE yaz. "
    "Altına Claude'un tek başına anlayabileceği en kısa mesajı yaz. "
    "Görev tamamlandığında ilk satıra DONE yaz ve altında sonucu ver. "
    "Claude çıktısını değerlendir, gereksiz onay turları açma."
)
# Köprünün yanıtı ayrıştırabilmesi için tek teknik not.
FORMAT_NOTE = (
    "Biçim: ilk satır yalnızca TO_CLAUDE veya DONE olsun (kod bloğu ya da "
    "kalın yazı kullanma). {memory} Claude varsayılan olarak en fazla {words} kelimeyle yanıt verir; daha uzun "
    "çıktı gerekiyorsa mesajında açıkça iste."
)
MEMORY_FRESH = ("Claude'un her çağrısı bağımsızdır, önceki turları görmez; süreklilik "
                "gerekirse mesaja birkaç satırlık durum özeti ekle.")
MEMORY_SESSION = ("Claude mevcut bir Claude Code sohbetinin devamında yanıt verir ve o "
                  "sohbetin geçmişini görür; ancak dosya ve araç erişimi kapalıdır.")
MEMORY_SESSION_TOOLS = ("Claude mevcut bir Claude Code sohbetinin devamında yanıt verir ve o "
                        "sohbetin geçmişini görür.")
TOOLS_NOTE = ("Claude {cwd} proje klasöründe dosya okuyup düzenleyebilir. İşi bitirince kısa "
              "bir rapor verir. Bazı komutlar ve MCP araçları kullanıcının Claude izin "
              "ayarlarına göre reddedilebilir.")
CLAUDE_TOOLS_APPEND = (
    "Bu mesaj bir köprü üzerinden başka bir yapay zekâdan (ChatGPT) geliyor. İstenen işi bu "
    "proje klasöründe yap. Bitirince yaptıklarını, değişen dosyaları ve test sonucunu düz metin "
    "bir raporla bildir. Mesaj daha uzun bir rapor istemedikçe rapor en fazla {words} kelime "
    "olsun. Her durumda görünür bir yanıt yaz; yapamadığın bir şey varsa nedenini söyle."
)


def memory_note(session, tools: bool = False, cwd: str | None = None) -> str:
    """GPT'ye Claude tarafının nasıl çalıştığını anlatan not."""
    if not tools:
        return MEMORY_SESSION if session else MEMORY_FRESH
    base = MEMORY_SESSION_TOOLS if session else MEMORY_FRESH
    return f"{base} {TOOLS_NOTE.format(cwd=cwd or (session.cwd if session else '?'))}"


CLAUDE_SYSTEM = (
    "Bir köprü üzerinden başka bir yapay zekâdan gelen tek bir mesajı "
    "yanıtlıyorsun. Aracın, dosya ve internet erişimin yok; yalnızca metinle "
    "yanıt ver. Mesaj açıkça daha uzun bir çıktı istemedikçe yanıtın en fazla "
    "{words} kelime olsun."
)

EXIT_DONE, EXIT_INCOMPLETE, EXIT_STOPPED = 0, 2, 3


class Stop(Exception):
    """Köprüyü durdurur. code: EXIT_INCOMPLETE veya EXIT_STOPPED."""

    def __init__(self, reason: str, code: int = EXIT_STOPPED):
        super().__init__(reason)
        self.code = code


class AmbiguousSend(Stop):
    """Gönderimin başarılı olup olmadığı belirsiz; asla yeniden denenmez."""


class Cancelled(Stop):
    """Kullanıcı durdurdu; bundan sonra hiçbir mesaj gönderilmez."""

    def __init__(self, reason: str = "Kullanıcı durdurdu; yeni mesaj gönderilmedi."):
        super().__init__(reason, EXIT_STOPPED)


def _is_set(ev) -> bool:
    return bool(ev is not None and ev.is_set())


@dataclass
class Reply:
    msg_id: str
    text: str
    complete: bool


class GptChannel(Protocol):
    def assistant_ids(self) -> set[str]: ...
    def send(self, text: str) -> None: ...
    def wait_reply(self, known_ids: set[str], timeout: float) -> Reply: ...


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def conv_id(url: str) -> str:
    """Sohbet kimliği: /c/<kimlik> (proje URL'lerinde de aynı); yoksa sorgusuz URL."""
    m = re.search(r"/c/([0-9A-Za-z-]+)", url or "")
    return m.group(1) if m else (url or "").split("#")[0].split("?")[0].rstrip("/")


def loose(s: str) -> str:
    """Yalnızca harf ve rakamlar: ChatGPT mesaj balonunda markdown'ı (` * _ # vb.)
    işleyip görünümü değiştirse de aynı mesaj tanınsın."""
    return "".join(ch for ch in s.casefold() if ch.isalnum())


def key(s: str) -> str:
    """Başlık eşleştirme anahtarı: büyük/küçük harf, aksan, boşluk ve tire yok sayılır."""
    s = unicodedata.normalize("NFKD", s.casefold())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", "", s.replace("ı", "i"))


# --------------------------------------------------------------------------
# ChatGPT (görünür tarayıcı, CDP)
# --------------------------------------------------------------------------
class ChatGPT:
    # Eski ve yeni (2026-11) ChatGPT arayüzü seçicileri birlikte.
    SEL_COMPOSER = ('#prompt-textarea, form [data-composer-markdown][contenteditable="true"], '
                    'form .ProseMirror[contenteditable="true"]')
    SEL_SEND = ('[data-testid="send-button"], button#composer-submit-button, '
                'form button[type="submit"][aria-label="Gönder"], '
                'form button[type="submit"][aria-label="Send"]')
    # Görünür mesaj kutusunun kendi formu içindeki gönder düğmesi.
    SEL_SEND_IN_FORM = ('[data-testid="send-button"], button#composer-submit-button, '
                        'button[type="submit"]')
    SEL_STOP = ('[data-testid="stop-button"], form button[aria-label*="durdur" i], '
                'form button[aria-label*="stop" i]')
    COMPOSER_TIMEOUT = 20  # sn: yazılabilir mesaj kutusunun hazır olması için
    SEL_ASSISTANT = '[data-message-author-role="assistant"], [data-response-body]'
    SEL_USER = '[data-message-author-role="user"], [data-user-message-bubble]'
    CONFIRM_TIMEOUT = 30  # sn: gönderimin sohbette görünmesi için
    cancel = None  # threading.Event: kurulursa bekleme kesilir, tıklama yapılmaz

    SEL_SIDEBAR_CHATS = 'nav a[href*="/c/"]'

    def __init__(self, cdp_url: str, tab_match: str, log: Callable[[str], None],
                 chat: str | None = None):
        from playwright.sync_api import sync_playwright

        self.log = log
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            self._pw.stop()
            raise Stop(f"Tarayıcıya bağlanılamadı ({cdp_url}). Köprü profiliyle "
                       f"Chrome'u başlattığınızdan emin olun. Ayrıntı: {e}")
        pages = [p for c in self._browser.contexts for p in c.pages if tab_match in p.url]
        if len(pages) > 1:
            chats = [p for p in pages if "/c/" in p.url]
            pages = chats if chats else pages
        if len(pages) != 1:
            urls = "\n  ".join(p.url for p in pages) or "(yok)"
            raise Stop(f"'{tab_match}' içeren tam olarak bir sekme bekleniyordu, "
                       f"bulunan: {len(pages)}\n  {urls}\nFazla sekmeleri kapatın "
                       f"ya da --tab-match ile daraltın.")
        self.page = pages[0]
        self.page.bring_to_front()
        if chat:
            self.open_chat(chat)
        log(f"Sohbet sekmesi: {self.page.url}")

    # -- sohbet seçimi ----------------------------------------------------
    def list_chats(self) -> list[tuple[str, str]]:
        """Kenar çubuğundaki sohbetler: [(başlık, tam URL)]."""
        try:
            self.page.locator(self.SEL_SIDEBAR_CHATS).first.wait_for(timeout=10000)
        except Exception:
            raise Stop("Kenar çubuğunda sohbet listesi bulunamadı (kenar çubuğu kapalı "
                       "ya da giriş yapılmamış olabilir).")
        items = self.page.eval_on_selector_all(
            self.SEL_SIDEBAR_CHATS,
            "els => els.map(a => [(a.innerText || a.title || '').trim(), a.href])")
        seen, out = set(), []
        for title, href in items:
            if href not in seen and title:
                seen.add(href)
                out.append((title.splitlines()[0], href))
        return out

    def current_chat(self) -> tuple[str, str]:
        """Sekmede şu an açık sohbet: (başlık, URL)."""
        return (self.page.title() or "(başlıksız)", self.page.url)

    def open_chat(self, chat: str) -> None:
        """chat: sohbet URL'si ya da kenar çubuğundaki başlığın bir parçası."""
        if "/c/" in chat:
            url = chat if chat.startswith("http") else "https://chatgpt.com" + chat
        else:
            chats = self.list_chats()
            exact = [c for c in chats if key(c[0]) == key(chat)]
            hits = exact or [c for c in chats if key(chat) in key(c[0])]
            if len(hits) != 1:
                names = "\n  ".join(t for t, _ in hits) or "(eşleşme yok)"
                raise Stop(f"'{chat}' için tam olarak bir sohbet bekleniyordu, bulunan: "
                           f"{len(hits)}\n  {names}\nDaha belirgin bir başlık ya da sohbet "
                           f"URL'si verin (kenar çubuğunda görünmüyorsa sohbeti elle açın).")
            url = hits[0][1]
        if self.page.url.split("#")[0] != url.split("#")[0]:
            self.page.goto(url, wait_until="domcontentloaded")
        self.wait_composer()
        if conv_id(self.page.url) != conv_id(url):
            raise Stop(f"İstenen sohbet açılamadı. Beklenen: {url} · Açık olan: {self.page.url}")
        try:
            # Geçmiş mesajların yüklenmesini bekle (eski mesajlar yeni sanılmasın).
            self.page.locator(self.SEL_ASSISTANT).first.wait_for(timeout=15000)
        except Exception:
            pass
        time.sleep(2)

    # -- mesaj kutusu -----------------------------------------------------
    def wait_composer(self, timeout: float | None = None):
        """Görünür VE yazılabilir mesaj kutusunu döndürür.

        Sayfada birden fazla mesaj kutusu olabilir (ör. gizli 'ChatGPT'ye sor' ile görünür
        'ChatGPT ile çalış'); yalnızca görünür olan kullanılır."""
        timeout = self.COMPOSER_TIMEOUT if timeout is None else timeout
        deadline = time.time() + timeout
        while True:
            loc = self.page.locator(self.SEL_COMPOSER).filter(visible=True)
            try:
                if loc.count() > 0:
                    box = loc.first
                    if box.evaluate("e => (e.isContentEditable || (e.tagName === 'TEXTAREA' "
                                    "&& !e.disabled && !e.readOnly)) && !e.closest('[inert]')"):
                        return box
            except Exception:
                pass  # sayfa yükleniyor/yeniden çiziliyor
            if time.time() >= deadline or _is_set(self.cancel):
                hidden = self.page.locator(self.SEL_COMPOSER).count()
                raise Stop(f"Yazılabilir mesaj kutusu {timeout:.0f} sn içinde bulunamadı "
                           f"(sayfada {hidden} kutu var, görünür ve yazılabilir olan yok). "
                           f"Sekme: {self.page.url}")
            time.sleep(0.3)

    def ensure_ready(self, expected_url: str | None = None) -> tuple[str, str, str]:
        """Doğru sohbet açık ve yazılabilir mesaj kutusu hazır mı? (başlık, URL, kutu adı)."""
        if expected_url and conv_id(self.page.url) != conv_id(expected_url):
            raise Stop(f"Seçilen sohbet açık değil. Beklenen: {expected_url} · "
                       f"Açık olan: {self.page.url}")
        box = self.wait_composer()
        label = box.get_attribute("aria-label") or ""
        return self.page.title() or "(başlıksız)", self.page.url, label

    def close(self) -> None:
        # CDP bağlantısını bırakır; kullanıcının tarayıcısını kapatmaz.
        try:
            self._pw.stop()
        except Exception:
            pass

    # -- okuma ------------------------------------------------------------
    def _messages(self, sel: str) -> list[tuple[str, str]]:
        return self.page.eval_on_selector_all(
            sel,
            """els => els.map((el, i) => {
                 const md = el.querySelector('.markdown') || el;
                 return [el.getAttribute('data-message-id') ||
                         el.getAttribute('data-response-body') || ('idx-' + i),
                         md.innerText || ''];
               })""",
        )

    def assistant_ids(self) -> set[str]:
        return {i for i, _ in self._messages(self.SEL_ASSISTANT)}

    def _generating(self) -> bool:
        return self.page.locator(self.SEL_STOP).filter(visible=True).count() > 0

    def _composer_text(self, box) -> str:
        if box.evaluate("e => e.tagName") == "TEXTAREA":
            return box.input_value()
        return box.inner_text()

    # -- gönderme ---------------------------------------------------------
    def send(self, text: str) -> None:
        try:
            box = self.wait_composer()
        except Stop as e:
            raise Stop(f"Mesaj kutusu bulunamadı; gönderilmedi. {e}")
        if self._generating():
            raise Stop("ChatGPT hâlâ yanıt üretiyor; gönderim yapılmadı.")
        if norm(self._composer_text(box)):
            raise Stop("Mesaj kutusunda kullanıcı taslağı var; üzerine yazılmadı.")

        # 1) Metni yerleştir (henüz hiçbir şey gönderilmedi; hata güvenli).
        if box.evaluate("e => e.tagName") == "TEXTAREA":
            box.fill(text)
        else:
            box.evaluate(
                """(el, t) => { el.focus(); const dt = new DataTransfer();
                     dt.setData('text/plain', t);
                     el.dispatchEvent(new ClipboardEvent('paste',
                       {clipboardData: dt, bubbles: true, cancelable: true})); }""",
                text,
            )
            time.sleep(0.5)
            if loose(self._composer_text(box)) != loose(text):
                box.click()
                self.page.keyboard.press("Control+A")
                self.page.keyboard.press("Delete")
                self.page.keyboard.insert_text(text)
                time.sleep(0.5)
        if loose(self._composer_text(box)) != loose(text):
            raise Stop("Metin mesaj kutusuna doğru yerleştirilemedi; gönderilmedi.")

        # 2) Gönder. Bu noktadan sonra her belirsizlik = dur, tekrar yok.
        prev = self._messages(self.SEL_USER)
        before, before_last = len(prev), (prev[-1][1] if prev else None)
        # Gönder düğmesi, metni yazdığımız kutunun kendi formunda aranır.
        form = box.locator("xpath=ancestor::form[1]")
        if form.count() > 0:
            btns = form.locator(self.SEL_SEND_IN_FORM).filter(visible=True)
        else:
            btns = self.page.locator(self.SEL_SEND).filter(visible=True)
        deadline = time.time() + 10
        while btns.count() == 0 and time.time() < deadline:
            time.sleep(0.3)
        if btns.count() == 0:
            raise Stop("Gönder düğmesi bulunamadı; gönderilmedi.")
        btn = btns.first
        if not btn.is_enabled():
            raise Stop("Gönder düğmesi etkin değil; gönderilmedi.")
        if _is_set(self.cancel):
            raise Cancelled()  # metin kutuda kalır ama gönderilmez
        try:
            btn.click(timeout=5000)
        except Exception as e:
            raise AmbiguousSend(f"Gönder tıklaması belirsiz sonuçlandı: {e}")

        # 3) Doğrula: yeni kullanıcı mesajı bizim metnimizle görünmeli.
        head = loose(text)[:80]
        deadline = time.time() + self.CONFIRM_TIMEOUT
        while time.time() < deadline:
            users = self._messages(self.SEL_USER)
            # Liste sanallaştırılmış olabilir: sayı artmasa da son mesaj değişir.
            if users and (len(users) > before or users[-1][1] != before_last):
                if head in loose(users[-1][1]):
                    return
                raise AmbiguousSend("Yeni kullanıcı mesajı gönderilen metinle eşleşmiyor.")
            time.sleep(0.5)
        raise AmbiguousSend(f"Gönderim {self.CONFIRM_TIMEOUT} sn içinde sohbette doğrulanamadı.")

    # -- yanıt bekleme ----------------------------------------------------
    def wait_reply(self, known_ids: set[str], timeout: float) -> Reply:
        deadline = time.time() + timeout
        last_text, stable, last = None, 0, None
        while time.time() < deadline and not _is_set(self.cancel):
            msgs = self._messages(self.SEL_ASSISTANT)
            # Yalnızca sohbetteki SON yanıt aday: kaydırmayla geri gelen eski mesajlar sayılmaz.
            new = msgs[-1:] if msgs and msgs[-1][0] not in known_ids else []
            if new and not self._generating():
                last = new[-1]
                if last[1].strip() and last[1] == last_text:
                    stable += 1
                    if stable >= 3:  # ~3 sn boyunca değişmedi ve üretim bitti
                        return Reply(last[0], last[1], True)
                else:
                    stable = 0
                last_text = last[1]
            else:
                stable, last_text = 0, None
            time.sleep(1)
        return Reply(last[0] if last else "", last[1] if last else "", False)


# --------------------------------------------------------------------------
# Claude Code CLI (etkileşimsiz, araçsız)
# --------------------------------------------------------------------------
@dataclass
class ClaudeSession:
    id: str
    cwd: str
    title: str
    first: str
    mtime: float


def list_claude_sessions(root: Path | None = None, limit: int = 20) -> list[ClaudeSession]:
    """~/.claude/projects altındaki Claude Code sohbetleri, en yeniden eskiye."""
    root = root or Path.home() / ".claude" / "projects"
    files = sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files[:limit]:
        cwd = title = first = ""
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not cwd and '"cwd"' in line or '"custom-title"' in line or (
                        not first and '"type":"user"' in line):
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    cwd = cwd or d.get("cwd") or ""
                    if d.get("type") == "custom-title":
                        title = d.get("customTitle") or title
                    if not first and d.get("type") == "user":
                        c = d.get("message", {}).get("content")
                        if isinstance(c, str):
                            first = c
        out.append(ClaudeSession(f.stem, cwd, title, " ".join(first.split())[:70],
                                 f.stat().st_mtime))
    return out


def find_claude_session(query: str, sessions: list[ClaudeSession]) -> ClaudeSession:
    """Kimlik (ya da başı), başlık ya da proje klasörü adına göre; en yenisi seçilir."""
    q = query.strip().lower()
    hits = [s for s in sessions if s.id.lower().startswith(q)]
    if not hits:
        hits = [s for s in sessions
                if key(query) and (key(query) in key(s.title) or
                                   key(query) in key(Path(s.cwd).name) or
                                   key(query) in key(s.first))]
    if not hits:
        raise Stop(f"'{query}' ile eşleşen Claude sohbeti yok. --list-claude ile bakın.")
    return hits[0]  # liste en yeniden eskiye sıralı


def describe(s: ClaudeSession) -> str:
    when = time.strftime("%d.%m %H:%M", time.localtime(s.mtime))
    return f"{s.id[:8]}  {when}  [{Path(s.cwd).name or '?'}]  {s.title or s.first or '(başlıksız)'}"


def make_claude_runner(claude_bin: str | None, model: str | None, words: int,
                       timeout: float, session: ClaudeSession | None = None,
                       cancel=None, tools: bool = False, workdir: str | None = None,
                       log: Callable[[str], None] | None = None) -> Callable[[str], str]:
    """tools=False: yalnızca metin (varsayılan). tools=True: proje klasöründe araçlı çalışma."""
    exe = claude_bin or shutil.which("claude")
    if not exe:
        raise Stop("claude komutu bulunamadı (PATH).")
    if session:
        # Seçilen sohbete devam: Claude Code oturumları proje klasörüne bağlıdır.
        if not Path(session.cwd).is_dir():
            raise Stop(f"Sohbetin klasörü bulunamadı: {session.cwd}")
        workdir = session.cwd
    elif tools:
        if not workdir or not Path(workdir).is_dir():
            raise Stop("Araçlı modda yeni oturum için var olan bir proje klasörü seçin "
                       "(terminalde --claude-cwd).")
    else:
        workdir = tempfile.mkdtemp(prefix="bridge-claude-")  # boş, proje dışı klasör
    state = {"sid": session.id if session else None}
    if tools:
        base = [
            exe, "-p",
            "--output-format", "json",
            # Dosya düzenlemeleri (ve dosya yazan bazı Bash komutları) bu klasörde onaysız;
            # diğer komutlar/MCP kullanıcının izin ayarlarına göre çalışır ya da reddedilir
            # (reddedilenler log'a yazılır).
            "--permission-mode", "acceptEdits",
            "--no-chrome",
            "--append-system-prompt", CLAUDE_TOOLS_APPEND.format(words=words),
        ]
    else:
        base = [
            exe, "-p",
            "--output-format", "json",
            "--tools", "",                       # tüm yerleşik araçlar kapalı
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',  # MCP yok
            "--setting-sources", "",             # kullanıcı/proje ayarları, hook'lar yok
            "--disable-slash-commands",          # skill yok
            "--no-chrome",
            "--system-prompt", CLAUDE_SYSTEM.format(words=words),
        ]
    if not session:
        base.append("--no-session-persistence")  # her çağrı bağımsız
    if model:
        base += ["--model", model]

    def run(message: str) -> str:
        cmd = base + (["--resume", state["sid"]] if state["sid"] else [])
        p = subprocess.Popen(cmd, cwd=workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline, inp = time.time() + timeout, message
        while True:
            try:
                stdout, stderr = p.communicate(inp, timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                inp = None  # girdi yalnızca ilk çağrıda verilir
                if _is_set(cancel) or time.time() > deadline:
                    p.kill()
                    p.communicate()
                    if _is_set(cancel):
                        raise Cancelled("Kullanıcı durdurdu; Claude çağrısı kesildi.")
                    raise Stop(f"Claude {timeout:.0f} sn içinde yanıt vermedi.")
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            raise Stop(f"Claude çıktısı okunamadı (çıkış {p.returncode}): "
                       f"{(stderr or stdout)[:500]}")
        if data.get("is_error") or p.returncode != 0:
            raise Stop(f"Claude hata döndürdü: {str(data.get('result'))[:500]}")
        if state["sid"] and data.get("session_id"):
            state["sid"] = data["session_id"]  # devam eden oturum kimliğini izle
        denied = [d.get("tool_name", "?") for d in (data.get("permission_denials") or [])
                  if isinstance(d, dict)]
        if denied and log:
            counts = {t: denied.count(t) for t in dict.fromkeys(denied)}
            log("Claude'un izin verilmeyen araç çağrıları: " +
                ", ".join(f"{t} ×{n}" for t, n in counts.items()))
        result = str(data.get("result") or "")
        if not result.strip():
            # Boş yanıt ChatGPT'ye iletilmez: GPT bunu "Claude cevap verdi" sanmasın.
            hint = ("" if tools else " Araçlar kapalı olduğundan dosya düzenleme "
                    "gerektiren bir görevi yapamamış olabilir; araçlı modu deneyin.")
            raise Stop("Claude görünür bir yanıt üretmedi (yalnızca düşünme)." + hint +
                       " ChatGPT'ye hiçbir şey iletilmedi.")
        return result

    return run


# --------------------------------------------------------------------------
# Köprü döngüsü
# --------------------------------------------------------------------------
def parse(text: str) -> tuple[str, str]:
    first, _, rest = text.strip().partition("\n")
    return first.strip(), rest.strip()


class Bridge:
    def __init__(self, gpt: GptChannel, claude: Callable[[str], str], *,
                 max_minutes: float, max_messages: int, reply_timeout: float,
                 max_claude_chars: int, log: Callable[[str], None],
                 on_event: Callable[[str, str], None] | None = None, cancel=None):
        self.gpt, self.claude, self.log = gpt, claude, log
        # on_event(tür, metin): "status" (gpt_wait|claude_wait), "gpt", "claude", "done"
        self.on_event = on_event or (lambda kind, text: None)
        self.cancel = cancel
        self.deadline = time.time() + max_minutes * 60
        self.max_messages = max_messages
        self.reply_timeout = reply_timeout
        self.max_claude_chars = max_claude_chars
        self.sent = 0
        self.processed: set[str] = set()   # işlenen (iletilen) GPT mesaj kimlikleri

    def _budget(self) -> None:
        if _is_set(self.cancel):
            raise Cancelled()
        if time.time() >= self.deadline:
            raise Stop("Süre sınırı doldu.", EXIT_INCOMPLETE)
        if self.sent >= self.max_messages:
            raise Stop(f"Mesaj sınırı ({self.max_messages}) doldu.", EXIT_INCOMPLETE)

    def _to_gpt(self, text: str) -> set[str]:
        self._budget()
        known = self.gpt.assistant_ids()
        self.sent += 1            # gönderimden ÖNCE say; send() asla yeniden denenmez
        self.gpt.send(text)
        self.log(f"-> ChatGPT ({len(text)} karakter) gönderildi.")
        self.on_event("status", "gpt_wait")
        return known

    def run(self, task: str, words: int, memory: str = MEMORY_FRESH) -> str:
        note = FORMAT_NOTE.format(words=words, memory=memory)
        first_msg = f"{task}\n\n---\nProtokol:\n{PROTOCOL}\n{note}"
        known = self._to_gpt(first_msg)
        while True:
            remaining = self.deadline - time.time()
            if remaining <= 0:
                raise Stop("Süre sınırı doldu.", EXIT_INCOMPLETE)
            reply = self.gpt.wait_reply(known, min(self.reply_timeout, remaining))
            if _is_set(self.cancel):
                raise Cancelled()
            if not reply.complete:
                raise Stop("GPT yanıtı tamamlanmadı (zaman aşımı); hiçbir şey iletilmedi.")
            if reply.msg_id in self.processed:
                raise Stop(f"GPT mesajı {reply.msg_id} zaten işlenmişti; tekrar iletilmedi.")
            self.processed.add(reply.msg_id)

            kind, body = parse(reply.text)
            if kind == "DONE":
                self.on_event("done", body)
                return body
            if kind != "TO_CLAUDE" or not body:
                raise Stop(f"Tanınmayan ilk satır: {kind[:80]!r}; hiçbir şey iletilmedi.")

            self._budget()
            self.sent += 1
            self.on_event("gpt", body)
            self.on_event("status", "claude_wait")
            self.log(f"-> Claude ({len(body)} karakter)…")
            out = self.claude(body)
            if len(out) > self.max_claude_chars:
                out = out[: self.max_claude_chars] + "\n[köprü: yanıt kısaltıldı]"
            self.log(f"<- Claude ({len(out.split())} kelime).")
            self.on_event("claude", out)
            # Claude metni veri: yorumlanmaz, sadece sınırlayıcılarla taşınır.
            known = self._to_gpt(f"Claude yanıtı:\n<<<\n{out}\n>>>")


def _choose(title: str, rows: list[str], zero: str) -> int:
    """Numaralı liste gösterir; 0 = varsayılan. Seçilen indeks (-1 = varsayılan)."""
    print(f"\n{title}\n  0) {zero}", file=sys.stderr)
    for i, r in enumerate(rows, 1):
        print(f"  {i}) {r}", file=sys.stderr)
    while True:
        ans = input("Seçim numarası: ").strip() or "0"
        if ans.isdigit() and 0 <= int(ans) <= len(rows):
            return int(ans) - 1
        print("Geçersiz numara.", file=sys.stderr)


def pick_claude(sessions: list[ClaudeSession]) -> ClaudeSession | None:
    i = _choose("Claude Code sohbeti:", [describe(s) for s in sessions],
                "Yeni (her tur bağımsız, geçmişsiz)")
    return sessions[i] if i >= 0 else None


def pick_gpt(chats: list[tuple[str, str]]) -> str | None:
    i = _choose("ChatGPT sohbeti:", [t for t, _ in chats], "Sekmede şu an açık olan")
    return chats[i][1] if i >= 0 else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ChatGPT <-> Claude Code metin köprüsü")
    ap.add_argument("task", nargs="?", help="Görevi başlatan kullanıcı mesajı")
    ap.add_argument("--task-file", help="Görev metnini dosyadan oku (UTF-8)")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222", help="Chrome CDP adresi")
    ap.add_argument("--tab-match", default="chatgpt.com", help="Sohbet sekmesi URL parçası")
    ap.add_argument("--max-minutes", type=float, default=30)
    ap.add_argument("--max-messages", type=int, default=30,
                    help="Toplam iletilen mesaj (ChatGPT + Claude) üst sınırı")
    ap.add_argument("--reply-timeout", type=float, default=600, help="Tek GPT yanıtı için sn")
    ap.add_argument("--claude-timeout", type=float, default=None,
                    help="Tek Claude çağrısı için sn (varsayılan 300; araçlı modda 1800)")
    ap.add_argument("--claude-tools", action="store_true",
                    help="Claude proje klasöründe dosya okuyup düzenleyebilsin (araçlar açık)")
    ap.add_argument("--claude-cwd", help="Araçlı modda yeni oturum için proje klasörü")
    ap.add_argument("--claude-words", type=int, default=120)
    ap.add_argument("--claude-model", default=None)
    ap.add_argument("--claude-bin", default=None)
    ap.add_argument("--max-claude-chars", type=int, default=20000)
    ap.add_argument("--gpt-chat", help="ChatGPT sohbeti: başlığın bir parçası ya da URL "
                                       "(verilmezse açık sekmedeki sohbet)")
    ap.add_argument("--claude-session", help="Claude Code sohbeti: kimlik, başlık ya da proje "
                                             "klasörü adı (verilmezse her tur bağımsız çağrı)")
    ap.add_argument("--pick", action="store_true", help="İki tarafın sohbetini listeden seç")
    ap.add_argument("--list-claude", action="store_true", help="Claude sohbetlerini listele")
    ap.add_argument("--list-gpt", action="store_true", help="ChatGPT sohbetlerini listele")
    a = ap.parse_args(argv)

    def log(m: str) -> None:
        print(f"[köprü {time.strftime('%H:%M:%S')}] {m}", file=sys.stderr, flush=True)

    if a.list_claude or a.list_gpt:
        try:
            if a.list_claude:
                print("Claude Code sohbetleri (en yeni üstte):")
                for s in list_claude_sessions():
                    print("  " + describe(s))
            if a.list_gpt:
                g = ChatGPT(a.cdp, a.tab_match, log)
                try:
                    print("ChatGPT sohbetleri (kenar çubuğu):")
                    for t, u in g.list_chats():
                        print(f"  {t}  {u}")
                finally:
                    g.close()
            return 0
        except Stop as e:
            log(f"DURDURULDU: {e}")
            return e.code

    if a.task_file:
        with open(a.task_file, encoding="utf-8") as f:
            task = f.read().strip()
    else:
        task = (a.task or "").strip()
    if not task:
        ap.error("görev metni gerekli (argüman ya da --task-file)")

    gpt = None
    try:
        session = None
        if a.pick:
            session = pick_claude(list_claude_sessions(limit=15))
        elif a.claude_session:
            session = find_claude_session(a.claude_session, list_claude_sessions(limit=200))
        if session:
            log(f"Claude sohbeti: {describe(session)}")
            log("Bu sohbet köprü çalışırken Claude uygulamasında açık kullanılmasın.")
        else:
            log("Claude: her tur bağımsız, geçmişsiz çağrı.")
        timeout = a.claude_timeout or (1800 if a.claude_tools else 300)
        claude = make_claude_runner(a.claude_bin, a.claude_model, a.claude_words, timeout,
                                    session, tools=a.claude_tools, workdir=a.claude_cwd, log=log)
        cwd = session.cwd if session else a.claude_cwd
        if a.claude_tools:
            log(f"Claude araçlı modda: {cwd} klasöründe dosya düzenleyebilir.")

        gpt = ChatGPT(a.cdp, a.tab_match, log, chat=None if a.pick else a.gpt_chat)
        if a.pick:
            url = pick_gpt(gpt.list_chats())
            if url:
                gpt.open_chat(url)
            log(f"ChatGPT sohbeti: {gpt.page.url}")
        bridge = Bridge(gpt, claude, max_minutes=a.max_minutes, max_messages=a.max_messages,
                        reply_timeout=a.reply_timeout, max_claude_chars=a.max_claude_chars, log=log)
        result = bridge.run(task, a.claude_words, memory_note(session, a.claude_tools, cwd))
        print(result)
        return EXIT_DONE
    except Stop as e:
        status = "TAMAMLANMADI" if e.code == EXIT_INCOMPLETE else "DURDURULDU"
        log(f"{status}: {e}")
        return e.code
    except KeyboardInterrupt:
        log("DURDURULDU: kullanıcı iptal etti.")
        return EXIT_STOPPED
    finally:
        if gpt:
            gpt.close()


if __name__ == "__main__":
    sys.exit(main())
