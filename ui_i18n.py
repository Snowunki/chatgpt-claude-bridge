"""UI translations only: conversation content and bridge protocol stay untouched."""
from __future__ import annotations

import json
import re
from pathlib import Path

LANGUAGES = {"tr": "Türkçe", "en": "English"}
EN = {
    "ChatGPT ↔ Claude köprüsü": "ChatGPT ↔ Claude Bridge",
    "Dil / Language": "Dil / Language",
    "Bağlan": "Connect", "Başlat": "Start", "Durdur": "Stop",
    "Sohbetler": "Conversations", "Görev": "Task", "Konuşma": "Conversation",
    "Kayıt": "Log", "Sonuç": "Result", "Proje klasörü": "Project folder",
    "Seç…": "Browse…", "Süre (dk)": "Time (min)", "Mesaj sınırı": "Message limit",
    "Hazır": "Ready", "Başlatılıyor…": "Starting…",
    "ChatGPT yanıtı bekleniyor": "Waiting for ChatGPT",
    "Claude yanıtı bekleniyor": "Waiting for Claude",
    "Durduruluyor…": "Stopping…", "Tamamlandı": "Completed",
    "Durduruldu": "Stopped", "Hata": "Error",
    "Chrome'da açık sohbet": "Conversation open in Chrome",
    "Yeni oturum · düşük token": "New session · low token use",
    "Claude proje dosyalarını düzenleyebilsin (araçlar açık)": "Allow Claude to edit project files (tools enabled)",
    "Claude'un çalışacağı proje klasörü": "Choose Claude's project folder",
    "  ·  araçlı": "  ·  tools enabled", "  ·  yalnızca metin": "  ·  text only",
    "Seçili:  ChatGPT → {gpt}    ·    Claude → {claude}{mode}": "Selected:  ChatGPT → {gpt}    ·    Claude → {claude}{mode}",
    "Eski sohbet seçildi: o sohbetin tüm geçmişi her turda yeniden gönderilir, daha fazla token kullanır.": "Existing session selected: its full history is sent again each turn, using more tokens.",
    "Araçlı mod: ChatGPT'nin mesajlarıyla Claude bu klasörde onay sormadan dosya okuyup düzenleyebilir; dosya yazan bazı Bash komutları da onaysız çalışabilir. Diğer komutlar ve MCP araçları yalnızca Claude izin ayarlarınızda izinliyse çalışır, reddedilenler Kayıt'ta görünür. Turlar uzun sürebilir ve daha çok token kullanır.": "Tool mode: ChatGPT's messages can instruct Claude to read and edit files in this folder without confirmation; some file-writing Bash commands may also run without confirmation. Other commands and MCP tools require permission in your Claude settings. Denied calls appear in the Log. Turns may take longer and use more tokens.",
    "Bağlı değil — Bağlan'a basın": "Not connected — click Connect",
    "Kontrol edilmedi": "Not checked", "Kontrol ediliyor…": "Checking…",
    "Claude CLI hazır": "Claude CLI ready", "Claude yanıt verdi": "Claude replied",
    "Bağlanıyor…": "Connecting…", "Chrome başlatılıyor…": "Starting Chrome…",
    "ChatGPT sekmesi aranıyor…": "Looking for a ChatGPT tab…",
    "Chrome'da ChatGPT'ye giriş yapın": "Log in to ChatGPT in Chrome",
    "Sohbet listesi yükleniyor…": "Loading conversations…",
    "Sohbet açılıyor, mesaj kutusu kontrol ediliyor…": "Opening conversation and checking the message box…",
    "Chrome kapalı — Bağlan'a basın": "Chrome is closed — click Connect",
    "Görev metni boş.": "Enter a task first.",
    "Süre ve mesaj sınırı sayı olmalı.": "Time and message limits must be numbers.",
    "Araçlı modda yeni oturum için var olan bir proje klasörü seçin.": "Choose an existing project folder for a new session in tool mode.",
    "Araçlı modda yeni oturum için var olan bir proje klasörü seçin (terminalde --claude-cwd).": "Choose an existing project folder for a new session in tool mode (use --claude-cwd in the terminal).",
    "Claude görünür bir yanıt üretmedi (yalnızca düşünme). ChatGPT'ye hiçbir şey iletilmedi.": "Claude produced no visible reply (only reasoning). Nothing was forwarded to ChatGPT.",
    "Claude görünür bir yanıt üretmedi (yalnızca düşünme). Araçlar kapalı olduğundan dosya düzenleme gerektiren bir görevi yapamamış olabilir; araçlı modu deneyin. ChatGPT'ye hiçbir şey iletilmedi.": "Claude produced no visible reply (only reasoning). Tools are disabled, so it may have been unable to perform a task that requires file edits; try tool mode. Nothing was forwarded to ChatGPT.",
    "Durdur istendi.": "Stop requested.", "Süre sınırı doldu.": "Time limit reached.",
    "Kullanıcı durdurdu; yeni mesaj gönderilmedi.": "Stopped by the user; no further messages were sent.",
    "Kullanıcı durdurdu; Claude çağrısı kesildi.": "Stopped by the user; the Claude call was cancelled.",
    "Bağlantı iptal edildi.": "Connection cancelled.",
    "Köprü Chrome'u başlatıldı.": "Bridge Chrome started.",
    "Açık köprü Chrome'una bağlanıldı (127.0.0.1:9222).": "Connected to bridge Chrome (127.0.0.1:9222).",
    "ChatGPT sekmesi açıldı.": "ChatGPT tab opened.",
    "Giriş bekleniyor: Chrome penceresinde ChatGPT'ye giriş yapın.": "Waiting for login: log in to ChatGPT in the Chrome window.",
    "ChatGPT girişi 15 dk içinde tamamlanmadı.": "ChatGPT login was not completed within 15 minutes.",
    "chrome.exe bulunamadı.": "chrome.exe was not found.",
    "claude komutu PATH'te bulunamadı.": "The claude command was not found on PATH.",
    "claude komutu bulunamadı (PATH).": "The claude command was not found on PATH.",
    "claude komutu 60 sn içinde yanıt vermedi.": "The claude command did not respond within 60 seconds.",
    "Claude CLI'da giriş yapılmamış. Terminalde 'claude' yazıp /login ile giriş yapın.": "Claude CLI is not logged in. Run 'claude' in a terminal and sign in with /login.",
    "Köprü profili Chrome'da 9222 portu olmadan açık. O Chrome penceresini kapatıp Bağlan'a yeniden basın.": "The bridge profile is open without port 9222. Close that Chrome window and click Connect again.",
    "Chrome açıldı ama 9222 portu 30 sn içinde hazır olmadı.": "Chrome opened, but port 9222 was not ready within 30 seconds.",
    "GPT yanıtı tamamlanmadı (zaman aşımı); hiçbir şey iletilmedi.": "The ChatGPT reply did not finish (timeout); nothing was forwarded.",
    "ChatGPT hâlâ yanıt üretiyor; gönderim yapılmadı.": "ChatGPT is still generating a reply; nothing was sent.",
    "Mesaj kutusunda kullanıcı taslağı var; üzerine yazılmadı.": "The message box contains a draft; it was not overwritten.",
    "Metin mesaj kutusuna doğru yerleştirilemedi; gönderilmedi.": "The text could not be inserted correctly; nothing was sent.",
    "Gönder düğmesi bulunamadı; gönderilmedi.": "The send button was not found; nothing was sent.",
    "Gönder düğmesi etkin değil; gönderilmedi.": "The send button is disabled; nothing was sent.",
    "Yeni kullanıcı mesajı gönderilen metinle eşleşmiyor.": "The new user message does not match the text sent.",
    "Kenar çubuğunda sohbet listesi bulunamadı (kenar çubuğu kapalı ya da giriş yapılmamış olabilir).": "No conversation list was found in the sidebar (it may be closed, or you may be logged out).",
}

# Anchored diagnostic templates preserve paths, titles and external error details.
# Never run this function on a task, conversation title or model response.
PATTERNS = [
    (r"Hazır · (.*)", "Ready · {0}"),
    (r"Mesaj sınırı \((\d+)\) doldu\.", "Message limit ({0}) reached."),
    (r"-> ChatGPT \((\d+) karakter\) gönderildi\.", "-> ChatGPT ({0} characters) sent."),
    (r"-> Claude \((\d+) karakter\)…", "-> Claude ({0} characters)…"),
    (r"<- Claude \((\d+) kelime\)\.", "<- Claude ({0} words)."),
    (r"Sohbet sekmesi: (.*)", "Conversation tab: {0}"),
    (r"Sohbetin klasörü bulunamadı: (.*)", "Session folder not found: {0}"),
    (r"Claude araçlı modda: (.*) klasöründe dosya düzenleyebilir\.", "Claude tool mode: can edit files in {0}."),
    (r"Claude'un izin verilmeyen araç çağrıları: (.*)", "Claude tool calls denied by permissions: {0}"),
    (r"Claude (\d+) sn içinde yanıt vermedi\.", "Claude did not reply within {0} seconds."),
    (r"Claude çıktısı okunamadı \(çıkış (.*?)\): (.*)", "Could not read Claude output (exit {0}): {1}"),
    (r"'(.*?)' içeren tam olarak bir sekme bekleniyordu, bulunan: (\d+)\n(.*?)\nFazla sekmeleri kapatın ya da --tab-match ile daraltın\.", "Expected exactly one tab containing '{0}', found: {1}\n{2}\nClose extra tabs or narrow the match with --tab-match."),
    (r"Gönderim (\d+) sn içinde sohbette doğrulanamadı\.", "The send could not be confirmed in the conversation within {0} seconds."),
    (r"GPT mesajı (.*) zaten işlenmişti; tekrar iletilmedi\.", "ChatGPT message {0} was already processed; it was not forwarded again."),
    (r"Tanınmayan ilk satır: (.*); hiçbir şey iletilmedi\.", "Unrecognized first line: {0}; nothing was forwarded."),
    (r"ChatGPT hazır: (.*) · (.*) · mesaj kutusu: (.*)", "ChatGPT ready: {0} · {1} · message box: {2}"),
    (r"İstenen sohbet açılamadı\. Beklenen: (.*) · Açık olan: (.*)", "Could not open the requested conversation. Expected: {0} · Open: {1}"),
    (r"Seçilen sohbet açık değil\. Beklenen: (.*) · Açık olan: (.*)", "The selected conversation is not open. Expected: {0} · Open: {1}"),
    (r"Yazılabilir mesaj kutusu (\d+) sn içinde bulunamadı \(sayfada (\d+) kutu var, görünür ve yazılabilir olan yok\)\. Sekme: (.*)", "No writable message box found within {0} seconds ({1} boxes on the page, none visible and writable). Tab: {2}"),
    (r"Tarayıcıya bağlanılamadı \((.*?)\)\. Köprü profiliyle Chrome'u başlattığınızdan emin olun\. Ayrıntı: (.*)", "Could not connect to the browser ({0}). Make sure Chrome is running with the bridge profile. Details: {1}"),
]
PREFIXES = {
    "Hata: ": "Error: ", "Claude hatası: ": "Claude error: ",
    "ChatGPT hatası: ": "ChatGPT error: ", "Tamamlanmadı: ": "Incomplete: ",
    "Giriş durumu okunamadı: ": "Could not read login status: ",
    "ChatGPT sohbet listesi alınamadı: ": "Could not load ChatGPT conversations: ",
    "Mesaj kutusu bulunamadı; gönderilmedi. ": "Message box not found; nothing was sent. ",
    "Gönder tıklaması belirsiz sonuçlandı: ": "The send click had an uncertain result: ",
    "Claude hata döndürdü: ": "Claude returned an error: ",
}


def translate(text: str, language: str, **values) -> str:
    template = EN.get(text, text) if language == "en" else text
    return template.format(**values) if values else template


def diagnostic(text: str, language: str) -> str:
    if language != "en" or not text:
        return text
    if text in EN:
        return EN[text]
    for prefix, english in PREFIXES.items():
        if text.startswith(prefix):
            return english + diagnostic(text[len(prefix):], language)
    for pattern, template in PATTERNS:
        match = re.fullmatch(pattern, text, flags=re.DOTALL)
        if match:
            return template.format(*match.groups())
    return text  # Keep unrecognized external diagnostics verbatim.


def load_language(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("language") if isinstance(data, dict) else None
        return value if isinstance(value, str) and value in LANGUAGES else "tr"
    except (OSError, ValueError):
        return "tr"


def save_language(path: Path, language: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"language": language}), encoding="utf-8")
    temp.replace(path)
