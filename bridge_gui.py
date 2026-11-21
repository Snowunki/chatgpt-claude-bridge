#!/usr/bin/env python3
"""ChatGPT <-> Claude Code köprüsü için küçük Tkinter penceresi.

Tüm iş bridge.py'deki mantıkla yapılır; pencere ek model çağrısı yapmaz.
Çalıştırma:  python bridge_gui.py
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
import urllib.request
from pathlib import Path
from tkinter import ttk
from urllib.parse import urlparse

import bridge

OPEN_TAB = "Chrome'da açık sohbet"
NEW_SESSION = "Yeni oturum · düşük token"
TOKEN_NOTE = ("Eski sohbet seçildi: o sohbetin tüm geçmişi her turda yeniden gönderilir, "
              "daha fazla token kullanır.")
TOOLS_WARN = ("Araçlı mod: ChatGPT'nin mesajlarıyla Claude bu klasörde onay sormadan dosya "
              "okuyup düzenleyebilir; dosya yazan bazı Bash komutları da onaysız çalışabilir. "
              "Diğer komutlar ve MCP araçları yalnızca Claude izin ayarlarınızda izinliyse "
              "çalışır, reddedilenler Kayıt'ta görünür. Turlar uzun sürebilir ve daha çok "
              "token kullanır.")
PROFILE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "chatgpt-bridge-profile"
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

STATUS = {  # çalışma durumu: (metin, arka plan rengi)
    "ready": ("Hazır", "#e7e9ee"),
    "starting": ("Başlatılıyor…", "#e7e9ee"),
    "gpt_wait": ("ChatGPT yanıtı bekleniyor", "#dbe8ff"),
    "claude_wait": ("Claude yanıtı bekleniyor", "#f6e3d3"),
    "stopping": ("Durduruluyor…", "#fff1c2"),
    "done": ("Tamamlandı", "#d4f0d9"),
    "stopped": ("Durduruldu", "#fff1c2"),
    "error": ("Hata", "#f8d2d2"),
}
SIDE_BG = {  # bağlantı göstergeleri
    "idle": "#e7e9ee", "connecting": "#dbe8ff", "launching": "#dbe8ff",
    "checking": "#dbe8ff", "login": "#fff1c2", "ready": "#d4f0d9",
    "replied": "#c2ebcb", "error": "#f8d2d2",
}
GPT_BG, CLAUDE_BG, RESULT_BG = "#eef2f7", "#fbeee3", "#e3f4e7"


def short(e: Exception | str, n: int = 160) -> str:
    s = " ".join(str(e).split())
    return s if len(s) <= n else s[: n - 1] + "…"


class Backend:
    """Gerçek bağlantılar (bridge.py + köprü Chrome'u). Testlerde sahtesiyle değiştirilir."""

    def __init__(self, cdp: str = "http://127.0.0.1:9222", tab_match: str = "chatgpt.com",
                 profile: Path = PROFILE_DIR):
        self.cdp, self.tab_match, self.profile = cdp, tab_match, Path(profile)
        self.port = urlparse(cdp).port or 9222
        self._lock = threading.Lock()  # aynı anda tek Chrome başlatma
        self.chrome_proc = None

    # ---------------------------------------------------------- Chrome / CDP
    def _cdp_json(self, path: str, method: str = "GET", timeout: float = 1.5):
        req = urllib.request.Request(self.cdp + path, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def cdp_alive(self) -> bool:
        try:
            self._cdp_json("/json/version")
            return True
        except Exception:
            return False

    def _profile_in_use(self) -> bool:
        """Köprü profili başka bir Chrome süreci tarafından kilitli mi?"""
        lf = self.profile / "lockfile"
        if not lf.exists():
            return False
        try:
            with open(lf, "a"):
                return False
        except OSError:
            return True

    def find_chrome(self) -> str | None:
        for p in CHROME_PATHS:
            if p and os.path.exists(p):
                return p
        return shutil.which("chrome")

    def _launch(self, exe: str):
        return subprocess.Popen(
            [exe, f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profile}",
             "--no-first-run", "--no-default-browser-check", "https://chatgpt.com/"],
            creationflags=(getattr(subprocess, "DETACHED_PROCESS", 0)
                           | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True)

    def ensure_chrome(self, set_state, cancel) -> bool:
        """Port açıksa dokunmaz; kapalıysa köprü profiliyle bir kez Chrome açar."""
        with self._lock:
            if self.cdp_alive():
                return False
            if self._profile_in_use():
                raise bridge.Stop("Köprü profili Chrome'da 9222 portu olmadan açık. O Chrome "
                                  "penceresini kapatıp Bağlan'a yeniden basın.")
            exe = self.find_chrome()
            if not exe:
                raise bridge.Stop("chrome.exe bulunamadı.")
            set_state("launching", "Chrome başlatılıyor…")
            self.chrome_proc = self._launch(exe)
            deadline = time.time() + 30
            while time.time() < deadline:
                if bridge._is_set(cancel):
                    raise bridge.Cancelled("Bağlantı iptal edildi.")
                if self.cdp_alive():
                    return True
                time.sleep(0.5)
            raise bridge.Stop("Chrome açıldı ama 9222 portu 30 sn içinde hazır olmadı.")

    def _tabs(self):
        pages = [t for t in self._cdp_json("/json/list") if t.get("type") == "page"]
        gpt = [t for t in pages if self.tab_match in t.get("url", "")]
        auth = [t for t in pages if "openai.com" in urlparse(t.get("url", "")).netloc]
        return gpt, auth

    def _login_state(self) -> str:
        """'ok' | 'login' | 'loading' — DOM'a bakar, model çağrısı yapmaz."""
        g = bridge.ChatGPT(self.cdp, self.tab_match, lambda m: None)
        try:
            return g.page.evaluate(
                """(sel) => {
                  const vis = e => e && e.offsetParent !== null;
                  const login = [...document.querySelectorAll('button, a')].some(b => vis(b) && (
                      b.matches('[data-testid="login-button"]') ||
                      /^(log in|giriş yap|oturum aç)$/i.test((b.innerText || '').trim())));
                  if (login) return 'login';
                  const box = [...document.querySelectorAll(sel)].some(
                      e => vis(e) && (e.isContentEditable || e.tagName === 'TEXTAREA'));
                  return box ? 'ok' : 'loading';
                }""", bridge.ChatGPT.SEL_COMPOSER)
        finally:
            g.close()

    def connect_gpt(self, log, set_state, cancel):
        set_state("connecting", "Bağlanıyor…")
        if self.ensure_chrome(set_state, cancel):
            log("Köprü Chrome'u başlatıldı.")
        else:
            log("Açık köprü Chrome'una bağlanıldı (127.0.0.1:9222).")
        set_state("connecting", "ChatGPT sekmesi aranıyor…")
        deadline, asked, opened = time.time() + 900, False, False
        while True:
            if bridge._is_set(cancel):
                raise bridge.Cancelled("Bağlantı iptal edildi.")
            gpt, auth = self._tabs()
            if not gpt and not auth and not opened:
                self._cdp_json("/json/new?https://chatgpt.com/", method="PUT")
                log("ChatGPT sekmesi açıldı.")
                opened = True
                time.sleep(3)
                continue
            state = self._login_state() if gpt else "login"
            if state == "ok":
                break
            if state == "login":
                set_state("login", "Chrome'da ChatGPT'ye giriş yapın")
                if not asked:
                    log("Giriş bekleniyor: Chrome penceresinde ChatGPT'ye giriş yapın.")
                    asked = True
            if time.time() > deadline:
                raise bridge.Stop("ChatGPT girişi 15 dk içinde tamamlanmadı.")
            time.sleep(2 if state == "loading" else 3)
        set_state("connecting", "Sohbet listesi yükleniyor…")
        return self.list_gpt(log)

    def prepare_chat(self, chat_url, log):
        """Seçilen sohbeti açar; doğru sohbet ve yazılabilir mesaj kutusu yoksa Stop."""
        g = bridge.ChatGPT(self.cdp, self.tab_match, log, chat=chat_url)
        try:
            return g.ensure_ready(chat_url)
        finally:
            g.close()

    def list_gpt(self, log):
        g = bridge.ChatGPT(self.cdp, self.tab_match, log)
        try:
            current = g.current_chat()
            try:
                chats = g.list_chats()
            except bridge.Stop as e:
                log(f"ChatGPT sohbet listesi alınamadı: {e}")
                chats = []
            return current, chats
        finally:
            g.close()

    # --------------------------------------------------------------- Claude
    def check_claude(self, log) -> dict:
        """Köprünün kullanacağı CLI'ı ve girişini denetler; model çağrısı yapmaz."""
        exe = shutil.which("claude")  # make_claude_runner da aynı yolu kullanır
        if not exe:
            raise bridge.Stop("claude komutu PATH'te bulunamadı.")
        run = dict(capture_output=True, text=True, encoding="utf-8", timeout=60,
                   creationflags=NO_WINDOW)
        try:
            ver = subprocess.run([exe, "--version"], **run)
            auth = subprocess.run([exe, "auth", "status"], **run)
        except subprocess.TimeoutExpired:
            raise bridge.Stop("claude komutu 60 sn içinde yanıt vermedi.")
        try:
            logged = bool(json.loads(auth.stdout).get("loggedIn"))
        except Exception:
            raise bridge.Stop(f"Giriş durumu okunamadı: {short(auth.stderr or auth.stdout)}")
        if not logged:
            raise bridge.Stop("Claude CLI'da giriş yapılmamış. Terminalde 'claude' yazıp "
                              "/login ile giriş yapın.")
        return {"path": exe, "version": ver.stdout.strip(),
                "env": f"NODE_USE_SYSTEM_CA={os.environ.get('NODE_USE_SYSTEM_CA', '(yok)')}"}

    def list_claude(self):
        return bridge.list_claude_sessions(limit=20)

    # --------------------------------------------------------------- çalışma
    def open_gpt(self, chat_url, log, cancel):
        g = bridge.ChatGPT(self.cdp, self.tab_match, log, chat=chat_url)
        g.cancel = cancel
        return g

    def make_claude(self, session, cancel, tools=False, workdir=None, log=None):
        return bridge.make_claude_runner(None, None, 120, 1800 if tools else 300, session,
                                         cancel, tools=tools, workdir=workdir, log=log)


class BridgeApp(tk.Tk):
    HEALTH_MS = 5000

    def __init__(self, backend=None, max_minutes: float = 30, max_messages: int = 30):
        super().__init__()
        self.backend = backend or Backend()
        self.title("ChatGPT ↔ Claude köprüsü")
        self.geometry("820x800")
        self.minsize(560, 560)
        self.events: queue.Queue = queue.Queue()
        self.cancel: threading.Event | None = None
        self.connect_cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.connecting = False
        self.preparing = False
        self.gpt_connected = False
        self._prep_seq = 0
        self.run_state = "ready"
        self.gpt_state = self.claude_state = "idle"
        self.bubbles: list[tuple[str, str, str]] = []  # (gönderen, taraf, metin) — testler için
        self.result_text: str | None = None
        self.open_tab_url: str | None = None
        self.gpt_choices: list[tuple[str, str | None]] = [(OPEN_TAB, None)]
        self.claude_choices: list[tuple[str, bridge.ClaudeSession | None]] = [(NEW_SESSION, None)]
        self._build(max_minutes, max_messages)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_id = self.after(100, self._poll)
        self._health_id = self.after(self.HEALTH_MS, self._health)

    def destroy(self):
        for aid in (self._poll_id, self._health_id):
            try:
                self.after_cancel(aid)
            except Exception:
                pass
        super().destroy()

    # ------------------------------------------------------------------ UI
    def _build(self, max_minutes, max_messages):
        self.option_add("*Font", ("Segoe UI", 10))
        top = tk.Frame(self)
        top.pack(fill="x", padx=10, pady=(8, 4))
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)
        self.gpt_ind_var, self.claude_ind_var = tk.StringVar(), tk.StringVar()
        self.gpt_ind = tk.Label(top, textvariable=self.gpt_ind_var, anchor="w", padx=8, pady=6,
                                justify="left", wraplength=330)
        self.gpt_ind.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.claude_ind = tk.Label(top, textvariable=self.claude_ind_var, anchor="w", padx=8,
                                   pady=6, justify="left", wraplength=330)
        self.claude_ind.grid(row=0, column=1, sticky="nsew", padx=4)
        self.connect_btn = ttk.Button(top, text="Bağlan", command=self.connect)
        self.connect_btn.grid(row=0, column=2, sticky="ns", padx=(4, 0))

        self.status_var = tk.StringVar()
        self.status = tk.Label(self, textvariable=self.status_var, font=("Segoe UI", 12, "bold"),
                               anchor="w", padx=12, pady=8)
        self.status.pack(fill="x", padx=10)
        self.error_var = tk.StringVar()
        self.error = tk.Label(self, textvariable=self.error_var, fg="#a40000", anchor="w",
                              justify="left", padx=12, wraplength=780)
        self.error.pack(fill="x")

        sel = ttk.LabelFrame(self, text="Sohbetler", padding=8)
        sel.pack(fill="x", padx=10, pady=(4, 6))
        sel.columnconfigure(1, weight=1)
        ttk.Label(sel, text="ChatGPT").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.gpt_box = ttk.Combobox(sel, state="readonly")
        self.gpt_box.grid(row=0, column=1, sticky="ew")
        ttk.Label(sel, text="Claude").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
        self.claude_box = ttk.Combobox(sel, state="readonly")
        self.claude_box.grid(row=1, column=1, sticky="ew", pady=(6, 0))
        self.token_var = tk.StringVar()
        ttk.Label(sel, textvariable=self.token_var, foreground="#9a5b00",
                  wraplength=700).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.summary_var = tk.StringVar()
        ttk.Label(sel, textvariable=self.summary_var, font=("Segoe UI", 10, "bold"),
                  wraplength=760).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.tools_var = tk.BooleanVar(value=False)
        self.tools_chk = ttk.Checkbutton(sel, text="Claude proje dosyalarını düzenleyebilsin "
                                                   "(araçlar açık)", variable=self.tools_var,
                                         command=self._update_summary)
        self.tools_chk.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        frow = ttk.Frame(sel)
        frow.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(frow, text="Proje klasörü").pack(side="left", padx=(0, 8))
        self.folder_var = tk.StringVar()
        self.folder_entry = ttk.Entry(frow, textvariable=self.folder_var)
        self.folder_entry.pack(side="left", fill="x", expand=True)
        self.folder_btn = ttk.Button(frow, text="Seç…", command=self._pick_folder)
        self.folder_btn.pack(side="left", padx=(6, 0))
        self.tools_warn_var = tk.StringVar()
        ttk.Label(sel, textvariable=self.tools_warn_var, foreground="#a40000",
                  wraplength=760).grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.gpt_box.bind("<<ComboboxSelected>>",
                          lambda e: (self._update_summary(), self._prepare_selected()))
        self.claude_box.bind("<<ComboboxSelected>>", lambda e: self._update_summary())
        self._fill_boxes()

        task = ttk.LabelFrame(self, text="Görev", padding=8)
        task.pack(fill="x", padx=10)
        self.task_text = tk.Text(task, height=4, wrap="word", relief="solid", borderwidth=1)
        self.task_text.pack(fill="x")
        row = ttk.Frame(task)
        row.pack(fill="x", pady=(6, 0))
        self.start_btn = ttk.Button(row, text="Başlat", command=self.start, state="disabled")
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(row, text="Durdur", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Label(row, text="Süre (dk)").pack(side="left", padx=(16, 4))
        self.minutes_var = tk.StringVar(value=str(max_minutes))
        ttk.Spinbox(row, from_=1, to=600, width=5, textvariable=self.minutes_var).pack(side="left")
        ttk.Label(row, text="Mesaj sınırı").pack(side="left", padx=(12, 4))
        self.messages_var = tk.StringVar(value=str(max_messages))
        ttk.Spinbox(row, from_=1, to=500, width=5, textvariable=self.messages_var).pack(side="left")

        conv = ttk.LabelFrame(self, text="Konuşma", padding=4)
        conv.pack(fill="both", expand=True, padx=10, pady=6)
        self.canvas = tk.Canvas(conv, highlightthickness=0, bg="white")
        sb = ttk.Scrollbar(conv, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg="white")
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

        logf = ttk.LabelFrame(self, text="Kayıt", padding=4)
        logf.pack(side="bottom", fill="x", padx=10, pady=(0, 8), before=conv)  # hep görünür
        self.log_text = tk.Text(logf, height=5, wrap="word", state="disabled",
                                font=("Consolas", 9), relief="flat", bg="#f6f6f6")
        self.log_text.pack(fill="x")
        self._set_status("ready")
        self._set_side("gpt", "idle", "Bağlı değil — Bağlan'a basın")
        self._set_side("claude", "idle", "Kontrol edilmedi")

    def _on_canvas_resize(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)
        for lbl in getattr(self, "_wrap_labels", []):
            lbl.configure(wraplength=max(200, int(e.width * lbl._ratio)))

    def _on_wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120), "units")

    # ------------------------------------------------------------ seçimler
    def _selected(self):
        gi, ci = self.gpt_box.current(), self.claude_box.current()
        gpt = self.gpt_choices[gi][1] if 0 <= gi < len(self.gpt_choices) else None
        if gpt is None:
            gpt = self.open_tab_url  # "açık sekme" seçiliyse onun adresi
        ses = self.claude_choices[ci][1] if 0 <= ci < len(self.claude_choices) else None
        return gpt, (ses.id if ses else None)

    def _fill_boxes(self, keep=None):
        gpt_key, ses_key = keep or (None, None)
        self.gpt_box["values"] = [t for t, _ in self.gpt_choices]
        self.claude_box["values"] = [t for t, _ in self.claude_choices]
        gi = next((i for i, (_, u) in enumerate(self.gpt_choices)
                   if u and gpt_key and u.split("#")[0] == gpt_key.split("#")[0]), 0)
        ci = next((i for i, (_, s) in enumerate(self.claude_choices)
                   if s and ses_key and s.id == ses_key), 0)
        self.gpt_box.current(gi)
        self.claude_box.current(ci)
        self._update_summary()

    def _update_summary(self):
        gi, ci = max(self.gpt_box.current(), 0), max(self.claude_box.current(), 0)
        gpt_name = self.gpt_choices[gi][0]
        session = self.claude_choices[ci][1]
        claude_name = self.claude_choices[ci][0] if session is None else (
            session.title or session.first or session.id[:8])
        tools = self.tools_var.get() if hasattr(self, "tools_var") else False
        mode = "  ·  araçlı" if tools else "  ·  yalnızca metin"
        self.summary_var.set(f"Seçili:  ChatGPT → {gpt_name}    ·    Claude → {claude_name}{mode}")
        self.token_var.set(TOKEN_NOTE if session else "")
        if not hasattr(self, "folder_entry"):
            return
        if session:  # mevcut oturum kendi klasöründe çalışır
            self.folder_var.set(session.cwd)
        elif self.folder_var.get() and getattr(self, "_folder_from_session", False):
            self.folder_var.set("")
        self._folder_from_session = bool(session)
        self.tools_warn_var.set(TOOLS_WARN if tools else "")
        self._update_buttons()

    def _pick_folder(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(parent=self, title="Claude'un çalışacağı proje klasörü",
                                    initialdir=self.folder_var.get() or str(Path.home()))
        if d:
            self.folder_var.set(os.path.normpath(d))

    # -------------------------------------------------------------- durum
    def _set_status(self, key: str):
        self.run_state = key
        text, bg = STATUS[key]
        self.status_var.set(text)
        self.status.configure(bg=bg)

    def _set_side(self, side: str, key: str, text: str):
        if side == "gpt":
            self.gpt_state = key
            self.gpt_ind_var.set(f"ChatGPT: {text}")
            self.gpt_ind.configure(bg=SIDE_BG[key])
        else:
            self.claude_state = key
            self.claude_ind_var.set(f"Claude: {text}")
            self.claude_ind.configure(bg=SIDE_BG[key])
        self._update_buttons()

    def _running(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def _update_buttons(self):
        if not hasattr(self, "start_btn"):
            return  # pencere henüz kuruluyor
        ready = self.gpt_state == "ready" and self.claude_state in ("ready", "replied")
        idle = not self._running() and not self.connecting
        self.start_btn.configure(state="normal" if ready and idle else "disabled")
        self.connect_btn.configure(state="normal" if idle else "disabled")
        box = "readonly" if not self._running() else "disabled"
        self.gpt_box.configure(state=box)
        self.claude_box.configure(state=box)
        if hasattr(self, "folder_entry"):
            ci = max(self.claude_box.current(), 0)
            has_session = self.claude_choices[ci][1] is not None
            free = not self._running()
            self.tools_chk.configure(state="normal" if free else "disabled")
            folder = "normal" if free and self.tools_var.get() and not has_session else "disabled"
            self.folder_entry.configure(state=folder)
            self.folder_btn.configure(state=folder)

    def _log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{time.strftime('%H:%M:%S')}  {msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _bubble(self, sender: str, text: str, side: str, bg: str, ratio: float = 0.68):
        self.bubbles.append((sender, side, text))
        row = tk.Frame(self.inner, bg="white")
        row.pack(fill="x", padx=8, pady=5)
        anchor = "w" if side == "left" else ("e" if side == "right" else "center")
        tk.Label(row, text=sender, bg="white", fg="#555",
                 font=("Segoe UI", 9, "bold")).pack(anchor=anchor)
        body = tk.Label(row, text=text, bg=bg, justify="left", anchor="w", padx=10, pady=7,
                        wraplength=max(200, int(self.canvas.winfo_width() * ratio)))
        body._ratio = ratio
        self._wrap_labels = getattr(self, "_wrap_labels", []) + [body]
        body.pack(anchor=anchor, fill="x" if side == "center" else None)
        self.after_idle(lambda: self.canvas.yview_moveto(1.0))

    # ------------------------------------------------------------- bağlan
    def connect(self):
        if self.connecting or self._running():
            return  # ikinci bağlanma / ikinci Chrome yok
        self.connecting = True
        self.gpt_connected = False
        self.error_var.set("")
        self._update_buttons()
        cancel = self.connect_cancel
        put = self.events.put
        log = lambda m: put(("log", m))  # noqa: E731

        def work():
            put(("side", ("claude", "checking", "Kontrol ediliyor…")))
            try:
                info = self.backend.check_claude(log)
                log(f"Claude CLI: {info['path']} · {info['version']} · {info['env']}")
                put(("claude_list", self.backend.list_claude()))
                put(("side", ("claude", "ready", "Claude CLI hazır")))
            except Exception as e:
                log(f"Claude hatası: {e}")
                put(("side", ("claude", "error", f"Hata: {short(e)}")))
            try:
                current, chats = self.backend.connect_gpt(
                    log, lambda k, t: put(("side", ("gpt", k, t))), cancel)
                # "Hazır" yalnızca seçili sohbet açılıp mesaj kutusu doğrulanınca gösterilir.
                put(("gpt_list", (current, chats)))
            except bridge.Cancelled:
                pass
            except Exception as e:
                log(f"ChatGPT hatası: {e}")
                put(("side", ("gpt", "error", f"Hata: {short(e)}")))
            put(("connect_done", None))

        threading.Thread(target=work, daemon=True).start()

    def _prepare_selected(self):
        """Seçili sohbeti Chrome'da açar ve yazılabilir mesaj kutusunu doğrular."""
        if not self.gpt_connected or self._running():
            return
        self._prep_seq += 1
        seq = self._prep_seq
        self.preparing = True
        url = self.gpt_choices[max(self.gpt_box.current(), 0)][1]  # None = açık sekme
        self._set_side("gpt", "connecting", "Sohbet açılıyor, mesaj kutusu kontrol ediliyor…")
        put = self.events.put

        def work():
            try:
                title, cur, label = self.backend.prepare_chat(url, lambda m: put(("log", m)))
                put(("prepared", (seq, title, cur, label)))
            except Exception as e:
                put(("prepare_error", (seq, str(e))))

        threading.Thread(target=work, daemon=True).start()

    def _health(self):
        """Bağlıyken Chrome kapanırsa göstergeyi düşürür (Bağlan ile toparlanır)."""
        if self.gpt_state == "ready" and not self.connecting and not self._running():
            def check():
                if not self.backend.cdp_alive():
                    self.events.put(("side", ("gpt", "error",
                                              "Hata: Chrome kapalı — Bağlan'a basın")))
            threading.Thread(target=check, daemon=True).start()
        self._health_id = self.after(self.HEALTH_MS, self._health)

    # -------------------------------------------------------------- çalışma
    def start(self):
        if self._running() or self.connecting or str(self.start_btn["state"]) == "disabled":
            return
        task = self.task_text.get("1.0", "end").strip()
        if not task:
            self.error_var.set("Görev metni boş.")
            return
        try:
            minutes, messages = float(self.minutes_var.get()), int(self.messages_var.get())
        except ValueError:
            self.error_var.set("Süre ve mesaj sınırı sayı olmalı.")
            return
        gpt_url = self.gpt_choices[max(self.gpt_box.current(), 0)][1]
        session = self.claude_choices[max(self.claude_box.current(), 0)][1]
        tools = self.tools_var.get()
        cwd = session.cwd if session else (self.folder_var.get().strip() or None)
        if tools and not session and not (cwd and os.path.isdir(cwd)):
            self.error_var.set("Araçlı modda yeni oturum için var olan bir proje klasörü seçin.")
            return
        for child in self.inner.winfo_children():
            child.destroy()
        self.bubbles, self.result_text, self._wrap_labels = [], None, []
        self.error_var.set("")
        self._set_status("starting")
        self._log(self.summary_var.get())
        self.stop_btn.configure(state="normal")
        self.cancel = threading.Event()
        self.worker = threading.Thread(
            target=self._run,
            args=(task, gpt_url, session, minutes, messages, self.cancel, tools, cwd),
            daemon=True)
        self.worker.start()
        self._update_buttons()

    def _run(self, task, gpt_url, session, minutes, messages, cancel, tools=False, cwd=None):
        put = self.events.put
        gpt = None
        try:
            claude = self.backend.make_claude(session, cancel, tools, cwd,
                                              lambda m: put(("log", m)))
            if tools:
                put(("log", f"Claude araçlı modda: {cwd} klasöründe dosya düzenleyebilir."))
            gpt = self.backend.open_gpt(gpt_url, lambda m: put(("log", m)), cancel)
            if cancel.is_set():
                raise bridge.Cancelled()
            b = bridge.Bridge(gpt, claude, max_minutes=minutes, max_messages=messages,
                              reply_timeout=600, max_claude_chars=20000,
                              log=lambda m: put(("log", m)),
                              on_event=lambda k, t: put((k, t)), cancel=cancel)
            b.run(task, 120, bridge.memory_note(session, tools, cwd))
            put(("final", ("done", "")))
        except bridge.Cancelled as e:
            put(("final", ("stopped", str(e))))
        except bridge.Stop as e:
            if e.code == bridge.EXIT_INCOMPLETE:
                put(("final", ("stopped", f"Tamamlanmadı: {e}")))
            else:
                put(("final", ("error", str(e))))
        except Exception as e:  # beklenmeyen hata: gerçek mesajı göster
            put(("final", ("error", f"{type(e).__name__}: {e}")))
        finally:
            if gpt is not None:
                try:
                    gpt.close()
                except Exception:
                    pass

    def stop(self):
        if self.cancel is not None and not self.cancel.is_set():
            self.cancel.set()  # bu andan sonra köprü hiçbir mesaj göndermez
            self.stop_btn.configure(state="disabled")
            self._set_status("stopping")
            self._log("Durdur istendi.")

    def _finish(self, key: str, msg: str):
        self.worker = None
        self._set_status(key)
        if msg:
            if key == "error":
                self.error_var.set(msg)
            self._log(msg)
        self.stop_btn.configure(state="disabled")
        self._update_buttons()
        if key == "error" and self.gpt_state == "ready":
            def check():  # hata Chrome kapandığı için mi?
                if not self.backend.cdp_alive():
                    self.events.put(("side", ("gpt", "error",
                                              "Hata: Chrome kapalı — Bağlan'a basın")))
            threading.Thread(target=check, daemon=True).start()

    # ------------------------------------------------------------- olaylar
    def _poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                self._handle(kind, data)
        except queue.Empty:
            pass
        self._poll_id = self.after(100, self._poll)

    def _handle(self, kind, data):
        stopping = self.cancel is not None and self.cancel.is_set()
        if kind == "status":
            if not stopping:
                self._set_status(data)
        elif kind == "gpt":
            self._bubble("ChatGPT", data, "left", GPT_BG)
        elif kind == "claude":
            self._bubble("Claude", data, "right", CLAUDE_BG)
            self._set_side("claude", "replied", "Claude yanıt verdi")
        elif kind == "done":
            self.result_text = data
            self._bubble("Sonuç", data, "center", RESULT_BG, ratio=0.9)
        elif kind == "final":
            self._finish(*data)
        elif kind == "log":
            self._log(data)
        elif kind == "side":
            self._set_side(*data)
        elif kind == "claude_list":
            keep = self._selected()
            self.claude_choices = [(NEW_SESSION, None)] + [(bridge.describe(s), s) for s in data]
            self._fill_boxes(keep)
        elif kind == "gpt_list":
            keep = self._selected()
            (cur_title, cur_url), chats = data
            self.open_tab_url = cur_url
            self.gpt_choices = [(f"{OPEN_TAB}: {cur_title}", None)] + [
                (t, u) for t, u in chats if u.split("#")[0] != cur_url.split("#")[0]]
            self._fill_boxes(keep)
            self.gpt_connected = True
            self._prepare_selected()
        elif kind == "prepared":
            seq, title, cur, label = data
            if seq == self._prep_seq:
                self.preparing = False
                self._log(f"ChatGPT hazır: {title} · {cur} · mesaj kutusu: {label or '?'}")
                self._set_side("gpt", "ready", f"Hazır · {title}")
        elif kind == "prepare_error":
            seq, msg = data
            if seq == self._prep_seq:
                self.preparing = False
                self._log(f"ChatGPT hatası: {msg}")
                self._set_side("gpt", "error", f"Hata: {short(msg)}")
        elif kind == "connect_done":
            self.connecting = False
            self._update_buttons()

    def _on_close(self):
        self.connect_cancel.set()
        if self.cancel is not None:
            self.cancel.set()
        self.destroy()


def main():
    # HTTPS trafiğini tarayan güvenlik yazılımları için claude.exe Windows
    # sertifika deposunu kullansın (TLS doğrulaması açık kalır).
    os.environ.setdefault("NODE_USE_SYSTEM_CA", "1")
    BridgeApp().mainloop()


if __name__ == "__main__":
    main()
