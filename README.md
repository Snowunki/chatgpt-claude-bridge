# ChatGPT ↔ Claude Code Bridge

A lightweight Windows desktop bridge that lets ChatGPT and Claude Code exchange messages, with live transcripts and session selection.

ChatGPT runs the task in a ChatGPT conversation you pick. When it wants Claude's help, it writes a message that starts with `TO_CLAUDE`. The bridge passes that message to the Claude Code CLI on your computer and posts Claude's reply back into the same ChatGPT conversation. When ChatGPT answers with `DONE`, the bridge shows the result and stops. You watch both sides live in one window.

**Requirements**

- Windows 10 or 11
- [Python 3.10+](https://www.python.org/downloads/windows/) (tick "Add python.exe to PATH" during setup)
- [Google Chrome](https://www.google.com/chrome/)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code/setup), logged in with your own account (run `claude` once and log in)
- A ChatGPT account

**Quick start**

1. Download the ZIP from the [latest release](../../releases/latest) and extract it, for example into `Documents`.
2. Double-click **`Start Bridge.cmd`**. The first start creates a private Python environment in `.venv` and installs dependencies; later starts open the window directly.
3. Click **Bağlan** (Connect). A separate Chrome window opens with its own profile. Log in to ChatGPT there the first time.
4. Pick the ChatGPT conversation and the Claude session, type the task, and click **Başlat** (Start).

> Use **Dil / Language** at the top of the window to switch between **Türkçe** and **English**. The change is immediate, including during a running task, and your preference is remembered.

---

## How it works

1. The bridge sends your task to ChatGPT, followed by a short protocol: ChatGPT leads the task, starts messages for Claude with `TO_CLAUDE`, and finishes with `DONE` plus the result.
2. If the first line of ChatGPT's reply is `TO_CLAUDE`, the rest is sent to `claude -p` (non-interactive). Claude's final answer goes back into the same ChatGPT conversation as "Claude yanıtı" (Claude's reply).
3. If the first line is `DONE`, the rest is the result. It is shown under **Sonuç** (Result), and the run ends.
4. If the first line is anything else, or the reply never finishes, the bridge forwards nothing and stops.

Claude's text is treated as data: the bridge never interprets it. Only ChatGPT's first line controls the flow.

Safety limits:

- A configurable time limit and message limit. Hitting one stops the run and reports it as incomplete.
- The same ChatGPT message is never forwarded twice.
- If it is unclear whether a send succeeded, the bridge stops instead of retrying.
- After you click **Durdur** (Stop), no further message is sent, and a running Claude call is cancelled.

## Choosing conversations

- **ChatGPT:** By default the bridge uses the conversation that is open in the bridge's Chrome window. The list shows the conversations in ChatGPT's sidebar. When you pick one, the bridge opens it and checks that the message box is writable. The status only shows **Hazır** (Ready) after that check passes.
- **Claude:** The default is **"Yeni oturum · düşük token"** (new session, low token use). You can also pick one of your existing local Claude Code sessions; they are read from `%USERPROFILE%\.claude\projects`. The bridge then continues that session with `claude -p --resume <id>` in the session's own folder, and the new turns are saved to that session.

The window shows both selected names before you start.

## Saving tokens

- **New session (default):** every Claude call is independent, with no history. Claude's reply is limited to about 120 words unless ChatGPT asks for more. If continuity is needed, ChatGPT is told to include a short status summary in its message.
- **Existing session:** Claude sees the whole history of that session, which is re-sent on every turn. With long sessions this uses noticeably more tokens; the window shows a warning when you pick one.

## Tool mode and permissions

Tool mode is **off by default**. In the default mode Claude Code runs with all tools disabled (`--tools ""`), no MCP servers, and none of your settings or hooks loaded, in an empty temporary folder. It can only answer with text.

If Claude should actually change files, for example to implement what ChatGPT plans, tick **"Claude proje dosyalarını düzenleyebilsin (araçlar açık)"** (let Claude edit project files):

- Claude runs with `--permission-mode acceptEdits` in the selected project folder. For a new session, choose the folder in the window; an existing session uses its own folder.
- File reads and edits in that folder happen **without asking**, and some file-writing shell commands may also run without asking.
- Other shell commands and MCP tools only run if your own Claude Code permission settings allow them. Denied calls are listed in the log box.
- Your Claude Code settings, hooks and MCP servers are loaded. `--dangerously-skip-permissions` is never used.
- One Claude call may take up to 30 minutes.

Only turn tool mode on for tasks and conversations you trust: ChatGPT's messages decide what Claude is asked to change.

## Window labels

The interface supports Turkish and English. Changing the language preserves your task,
selected conversations, project folder, limits, and running job. Conversation titles and
model responses remain in their original language; the language switch does not translate
messages or change the model protocol. External CLI/browser error details may remain in
their original language.

The language preference is stored locally in
`%LOCALAPPDATA%\chatgpt-claude-bridge\settings.json`. If it cannot be saved, the current
window still switches language. Turkish is the default on the first launch.

**Türkçe:** Pencerenin üstündeki **Dil / Language** alanından **Türkçe** veya **English**
seçin. Dil anında değişir ve sonraki açılışta hatırlanır. Görev metni, sohbet seçimi ve
devam eden işlem korunur; sohbet içerikleri çevrilmez.

| Label | Meaning |
|---|---|
| Bağlan | Connect |
| Başlat / Durdur | Start / Stop |
| Hazır | Ready |
| ChatGPT yanıtı bekleniyor | Waiting for ChatGPT |
| Claude yanıtı bekleniyor | Waiting for Claude |
| Tamamlandı / Durduruldu / Hata | Completed / Stopped / Error |
| Sohbetler · Görev · Konuşma · Kayıt | Conversations · Task · Conversation · Log |
| Sonuç | Result |
| Süre (dk) · Mesaj sınırı | Time limit (minutes) · Message limit |

## Command line

The window is optional. The same logic runs in a terminal; Chrome must already be running with the bridge profile (see below).

```powershell
.venv\Scripts\python.exe bridge.py --gpt-chat "My planning chat" "Your task"
.venv\Scripts\python.exe bridge.py --pick "Your task"          # choose both sides from lists
.venv\Scripts\python.exe bridge.py --list-gpt --list-claude    # only list conversations
```

Useful options: `--claude-session`, `--claude-tools`, `--claude-cwd`, `--max-minutes`, `--max-messages`, `--claude-words`. Run with `--help` for the full list. Exit codes are `0` for done, `2` when a limit was reached, and `3` when the run was stopped.

## Connection problems

| Symptom | What to do |
|---|---|
| **ChatGPT: Chrome'da ChatGPT'ye giriş yapın** (log in to ChatGPT in Chrome) | Log in inside the Chrome window the bridge opened. The bridge continues on its own. |
| "Köprü profili Chrome'da 9222 portu olmadan açık" (profile is open without port 9222) | The bridge profile is open in a normal Chrome window. Close that window, then click **Bağlan** again. |
| **Chrome kapalı** (Chrome is closed) | Click **Bağlan** again. Your conversation choices are kept. |
| "Yazılabilir mesaj kutusu … bulunamadı" (writable message box not found) | Make sure the selected conversation is open and not still loading, then select it again. If ChatGPT's page has changed, see the next section. |
| "Claude CLI'da giriş yapılmamış" (Claude CLI is not logged in) | Open a terminal, run `claude`, and log in with `/login`. |
| `claude -p` hangs, or `SELF_SIGNED_CERT_IN_CHAIN` | Antivirus HTTPS scanning re-signs certificates. The window sets `NODE_USE_SYSTEM_CA=1` so Claude Code trusts the Windows certificate store; for the command line, set it yourself: `$env:NODE_USE_SYSTEM_CA = "1"`. TLS verification stays on. |
| The window does not open | Run `"Start Bridge.cmd" --console` from a terminal to see error messages. |

The bridge's Chrome profile lives in `%LOCALAPPDATA%\chatgpt-bridge-profile`, separate from your normal browser. While it runs, Chrome's debugging port `127.0.0.1:9222` is open, so other programs on your computer could control that window. Close it when you are done.

## When ChatGPT's interface changes

The bridge drives the ChatGPT website through Chrome and finds the message box, send button and replies by their page structure. ChatGPT changes its interface from time to time, and that can break sending or reading replies. The bridge stops safely instead of guessing, but it may need an update. The selectors are at the top of the `ChatGPT` class in `bridge.py`.

## Privacy

- Everything runs on your computer. The bridge has no server and no telemetry.
- Messages go only to the ChatGPT conversation and the Claude Code CLI you selected, under your own accounts.
- No keys or passwords are stored by the bridge. Logins stay in the separate Chrome profile and in Claude Code's own configuration.

## Development

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests use fake ChatGPT and Claude replies and a local mock page; no account and no model calls are needed. The browser tests start a headless Chrome and are skipped if Chrome is not installed.

## Disclaimer

This is an independent project. It is not affiliated with or endorsed by OpenAI or Anthropic. Automating the ChatGPT website may be subject to OpenAI's terms of use; you are responsible for how you use this tool.

## License

[MIT](LICENSE)
