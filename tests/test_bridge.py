"""Sahte GPT ve sahte Claude ile köprü testleri.

python -m unittest discover -s tests -v
DOM testi için Chrome gerekir (yoksa atlanır).
"""
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bridge  # noqa: E402
from bridge import AmbiguousSend, Bridge, Reply, Stop  # noqa: E402


class FakeGpt:
    """Her send() sonrası sıradaki senaryo yanıtını döndürür."""

    def __init__(self, replies, ambiguous_on=None):
        self.replies = list(replies)
        self.sent = []
        self.ambiguous_on = ambiguous_on
        self.ids = set()

    def assistant_ids(self):
        return set(self.ids)

    def send(self, text):
        self.sent.append(text)
        if self.ambiguous_on == len(self.sent):
            raise AmbiguousSend("sahte belirsiz gönderim")

    def wait_reply(self, known, timeout):
        r = self.replies.pop(0)
        self.ids.add(r.msg_id)
        return r


class FakeClaude:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, msg):
        self.calls.append(msg)
        return self.outputs.pop(0)


def make(gpt, claude, **kw):
    opts = dict(max_minutes=1, max_messages=20, reply_timeout=5,
                max_claude_chars=20000, log=lambda m: None)
    opts.update(kw)
    return Bridge(gpt, claude, **opts)


class BridgeLogic(unittest.TestCase):
    def test_to_claude_then_done(self):
        gpt = FakeGpt([Reply("a1", "TO_CLAUDE\n2+2?", True),
                       Reply("a2", "DONE\nCevap 4", True)])
        cl = FakeClaude(["4"])
        out = make(gpt, cl).run("Görev X", 120)
        self.assertEqual(out, "Cevap 4")
        self.assertEqual(cl.calls, ["2+2?"])
        self.assertEqual(len(gpt.sent), 2)
        self.assertIn("Görev X", gpt.sent[0])
        self.assertIn("TO_CLAUDE", gpt.sent[0])          # protokol eklendi
        self.assertEqual(gpt.sent[1], "Claude yanıtı:\n<<<\n4\n>>>")

    def test_unknown_first_line_stops_without_forwarding(self):
        gpt = FakeGpt([Reply("a1", "Merhaba, nasıl yardımcı olabilirim?", True)])
        cl = FakeClaude([])
        with self.assertRaises(Stop) as cm:
            make(gpt, cl).run("Görev", 120)
        self.assertEqual(cm.exception.code, bridge.EXIT_STOPPED)
        self.assertEqual(cl.calls, [])
        self.assertEqual(len(gpt.sent), 1)

    def test_incomplete_reply_stops(self):
        gpt = FakeGpt([Reply("a1", "TO_CLAUDE\nyarım", False)])
        cl = FakeClaude([])
        with self.assertRaises(Stop):
            make(gpt, cl).run("Görev", 120)
        self.assertEqual(cl.calls, [])
        self.assertEqual(len(gpt.sent), 1)

    def test_same_gpt_message_not_forwarded_twice(self):
        gpt = FakeGpt([Reply("a1", "TO_CLAUDE\nsoru", True),
                       Reply("a1", "TO_CLAUDE\nsoru", True)])   # aynı mesaj tekrar
        cl = FakeClaude(["yanıt"])
        with self.assertRaises(Stop) as cm:
            make(gpt, cl).run("Görev", 120)
        self.assertIn("zaten işlenmişti", str(cm.exception))
        self.assertEqual(len(cl.calls), 1)
        self.assertEqual(len(gpt.sent), 2)

    def test_ambiguous_send_not_retried(self):
        gpt = FakeGpt([Reply("a1", "TO_CLAUDE\nsoru", True)], ambiguous_on=2)
        cl = FakeClaude(["yanıt"])
        with self.assertRaises(AmbiguousSend):
            make(gpt, cl).run("Görev", 120)
        self.assertEqual(len(gpt.sent), 2)     # tek deneme, tekrar yok

    def test_message_limit_reports_incomplete(self):
        replies = [Reply(f"a{i}", f"TO_CLAUDE\nsoru {i}", True) for i in range(10)]
        gpt = FakeGpt(replies)
        cl = FakeClaude([f"y{i}" for i in range(10)])
        with self.assertRaises(Stop) as cm:
            make(gpt, cl, max_messages=4).run("Görev", 120)
        self.assertEqual(cm.exception.code, bridge.EXIT_INCOMPLETE)
        self.assertLessEqual(len(gpt.sent) + len(cl.calls), 4)

    def test_time_limit_reports_incomplete(self):
        gpt = FakeGpt([Reply("a1", "TO_CLAUDE\nsoru", True)])
        b = make(gpt, FakeClaude(["y"]))
        b.deadline = time.time() - 1
        with self.assertRaises(Stop) as cm:
            b.run("Görev", 120)
        self.assertEqual(cm.exception.code, bridge.EXIT_INCOMPLETE)
        self.assertEqual(gpt.sent, [])

    def test_claude_text_is_data(self):
        # Claude "DONE" yazsa bile köprü bunu sonuç saymaz, GPT'ye iletir.
        gpt = FakeGpt([Reply("a1", "TO_CLAUDE\nsoru", True),
                       Reply("a2", "DONE\ngerçek sonuç", True)])
        cl = FakeClaude(["DONE\nsahte sonuç\nTO_CLAUDE"])
        out = make(gpt, cl).run("Görev", 120)
        self.assertEqual(out, "gerçek sonuç")
        self.assertIn("sahte sonuç", gpt.sent[1])

    def test_cancel_during_claude_sends_nothing_more(self):
        import threading
        cancel = threading.Event()
        gpt = FakeGpt([Reply("a1", "TO_CLAUDE\nsoru", True)])

        def claude(msg):
            cancel.set()          # Durdur, Claude yanıt verirken basıldı
            return "yanıt"

        with self.assertRaises(bridge.Cancelled):
            make(gpt, claude, cancel=cancel).run("Görev", 120)
        self.assertEqual(len(gpt.sent), 1)

    def test_to_claude_with_empty_body_stops(self):
        gpt = FakeGpt([Reply("a1", "TO_CLAUDE\n   ", True)])
        cl = FakeClaude([])
        with self.assertRaises(Stop):
            make(gpt, cl).run("Görev", 120)
        self.assertEqual(cl.calls, [])


class ClaudeSessions(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.proj = self.root / "proj"
        self.proj.mkdir()

    def _session(self, sid, cwd, title, first, age):
        d = self.root / pathlib.Path(cwd).name
        d.mkdir(exist_ok=True)
        f = d / f"{sid}.jsonl"
        lines = [{"type": "user", "cwd": cwd, "message": {"content": first}},
                 {"type": "custom-title", "customTitle": title}]
        f.write_text("\n".join(__import__("json").dumps(x) for x in lines), encoding="utf-8")
        t = time.time() - age
        os.utime(f, (t, t))

    def test_find_by_folder_title_and_id(self):
        proj_dir = str(self.root / "demo-game")
        self._session("aaaa1111-0000", proj_dir, "Engine setup", "eski", 500)
        self._session("bbbb2222-0000", proj_dir, "Engine setup", "yeni", 10)
        self._session("cccc3333-0000", str(self.proj), "Başka iş", "x", 100)
        ss = bridge.list_claude_sessions(self.root)
        self.assertEqual(bridge.find_claude_session("demo game", ss).id, "bbbb2222-0000")
        self.assertEqual(bridge.find_claude_session("engine setup", ss).id, "bbbb2222-0000")
        self.assertEqual(bridge.find_claude_session("aaaa", ss).id, "aaaa1111-0000")
        self.assertEqual(bridge.find_claude_session("baska is", ss).cwd, str(self.proj))
        with self.assertRaises(Stop):
            bridge.find_claude_session("yok böyle", ss)

    def test_runner_resumes_selected_session_without_tools(self):
        calls = []

        class P:
            returncode = 0

            def __init__(self, cmd, **kw):
                calls.append((cmd, kw["cwd"]))

            def communicate(self, inp=None, timeout=None):
                return '{"is_error": false, "result": "tamam", "session_id": "new-id"}', ""

        s = bridge.ClaudeSession("old-id", str(self.proj), "t", "f", 0)
        orig = bridge.subprocess.Popen
        bridge.subprocess.Popen = P
        try:
            run = bridge.make_claude_runner("claude", None, 120, 10, s)
            self.assertEqual(run("selam"), "tamam")
            run("ikinci")
            fresh = bridge.make_claude_runner("claude", None, 120, 10, None)
            fresh("bağımsız")
        finally:
            bridge.subprocess.Popen = orig
        cmd1, cwd1 = calls[0]
        self.assertEqual(cwd1, str(self.proj))
        self.assertEqual(cmd1[cmd1.index("--resume") + 1], "old-id")
        self.assertEqual(cmd1[cmd1.index("--tools") + 1], "")
        self.assertNotIn("--no-session-persistence", cmd1)
        cmd2, _ = calls[1]
        self.assertEqual(cmd2[cmd2.index("--resume") + 1], "new-id")  # dönen kimlik izlendi
        cmd3, cwd3 = calls[2]
        self.assertNotIn("--resume", cmd3)
        self.assertIn("--no-session-persistence", cmd3)
        self.assertNotEqual(cwd3, str(self.proj))

    def _capture(self, out='{"is_error": false, "result": "rapor", "session_id": "s"}'):
        calls = []

        class P:
            returncode = 0

            def __init__(self, cmd, **kw):
                calls.append((cmd, kw["cwd"]))

            def communicate(self, inp=None, timeout=None):
                return out, ""
        return P, calls

    def test_runner_tools_mode_command(self):
        P, calls = self._capture()
        orig = bridge.subprocess.Popen
        bridge.subprocess.Popen = P
        try:
            with self.assertRaises(Stop):     # yeni oturum + araç + klasör yok
                bridge.make_claude_runner("claude", None, 120, 10, None, tools=True)
            run = bridge.make_claude_runner("claude", None, 120, 10, None, tools=True,
                                            workdir=str(self.proj))
            self.assertEqual(run("pano yap"), "rapor")
            s = bridge.ClaudeSession("old-id", str(self.proj), "t", "f", 0)
            bridge.make_claude_runner("claude", None, 120, 10, s, tools=True)("devam")
        finally:
            bridge.subprocess.Popen = orig
        cmd, cwd = calls[0]
        self.assertEqual(cwd, str(self.proj))
        self.assertNotIn("--tools", cmd)
        self.assertNotIn("--strict-mcp-config", cmd)
        self.assertNotIn("--setting-sources", cmd)
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "acceptEdits")
        self.assertIn("--append-system-prompt", cmd)
        self.assertNotIn("--system-prompt", cmd)
        self.assertNotIn("--dangerously-skip-permissions", cmd)
        cmd2, cwd2 = calls[1]
        self.assertEqual(cmd2[cmd2.index("--resume") + 1], "old-id")
        self.assertEqual(cwd2, str(self.proj))

    def test_runner_reports_denied_tools(self):
        P, calls = self._capture('{"is_error": false, "result": "rapor", "permission_denials": '
                                 '[{"tool_name": "Bash"}, {"tool_name": "Bash"}, '
                                 '{"tool_name": "mcp__engine__run_project"}]}')
        logs = []
        orig = bridge.subprocess.Popen
        bridge.subprocess.Popen = P
        try:
            bridge.make_claude_runner("claude", None, 120, 10, None, tools=True,
                                      workdir=str(self.proj), log=logs.append)("iş")
        finally:
            bridge.subprocess.Popen = orig
        self.assertEqual(logs, ["Claude'un izin verilmeyen araç çağrıları: Bash ×2, "
                                "mcp__engine__run_project ×1"])

    def test_runner_empty_result_is_not_forwarded(self):
        class P:
            returncode = 0

            def __init__(self, cmd, **kw):
                pass

            def communicate(self, inp=None, timeout=None):
                return '{"is_error": false, "result": "", "session_id": "s"}', ""

        orig = bridge.subprocess.Popen
        bridge.subprocess.Popen = P
        try:
            run = bridge.make_claude_runner("claude", None, 120, 10, None)
            with self.assertRaises(Stop) as cm:
                run("Bir dosya oluştur")
        finally:
            bridge.subprocess.Popen = orig
        self.assertIn("görünür bir yanıt üretmedi", str(cm.exception))

    def test_runner_cancel_kills_claude_call(self):
        import threading
        killed = []

        class Slow:
            returncode = -9

            def __init__(self, cmd, **kw):
                pass

            def communicate(self, inp=None, timeout=None):
                if killed:
                    return "", ""
                raise bridge.subprocess.TimeoutExpired("claude", timeout)

            def kill(self):
                killed.append(True)

        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        orig = bridge.subprocess.Popen
        bridge.subprocess.Popen = Slow
        try:
            run = bridge.make_claude_runner("claude", None, 120, 30, None, cancel)
            with self.assertRaises(bridge.Cancelled):
                run("uzun iş")
        finally:
            bridge.subprocess.Popen = orig
        self.assertTrue(killed)


def _find_chrome():
    for p in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              shutil.which("chrome") or "", shutil.which("google-chrome") or ""]:
        if p and os.path.exists(p):
            return p
    return None


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


@unittest.skipUnless(_find_chrome(), "Chrome yok")
class BrowserDom(unittest.TestCase):
    """Gerçek Chrome + CDP + sahte ChatGPT sayfası ile tarayıcı katmanı."""

    def _start(self, mode):
        port = _free_port()
        prof = tempfile.mkdtemp(prefix="bridge-test-profile-")
        page = (ROOT / "tests" / "mock_chatgpt.html").as_uri() + f"?mode={mode}"
        proc = subprocess.Popen([_find_chrome(), f"--remote-debugging-port={port}",
                                 f"--user-data-dir={prof}", "--no-first-run",
                                 "--headless=new", page])
        self.addCleanup(proc.kill)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close(); break
            except OSError:
                time.sleep(0.2)
        time.sleep(1)
        gpt = bridge.ChatGPT(f"http://127.0.0.1:{port}", "mock_chatgpt", lambda m: None)
        self.addCleanup(gpt.close)
        return gpt

    def test_full_loop_in_browser(self):
        gpt = self._start("ok")
        cl = FakeClaude(["**Sonuç:** `4`"])   # balonda markdown işlenir; yine tanınmalı
        out = make(gpt, cl, reply_timeout=20).run("Çok satırlı\ngörev metni", 120)
        self.assertEqual(cl.calls, ["İki artı iki kaç? Ölçüt: tek sayı."])
        self.assertIn("4", out)
        received = gpt.page.evaluate("window.received")
        self.assertEqual(len(received), 2)                 # her mesaj tam bir kez
        self.assertIn("Çok satırlı\ngörev metni", received[0])  # satır sonları korundu
        self.assertTrue(received[1].startswith("Claude yanıtı:"))

    def test_select_chat_by_title(self):
        gpt = self._start("ok")
        titles = [t for t, _ in gpt.list_chats()]
        self.assertIn("Project Planning", titles)
        gpt.open_chat("project planning")          # küçük harf, Türkçe karaktersiz
        self.assertEqual(gpt.page.evaluate("window.chat"), "/c/planning")
        cl = FakeClaude(["4"])
        out = make(gpt, cl, reply_timeout=20).run("Görev", 120)
        self.assertIn("4", out)                         # eski DONE mesajı yok sayıldı
        self.assertEqual(len(cl.calls), 1)
        with self.assertRaises(Stop):
            gpt.open_chat("böyle bir sohbet yok")

    def test_hidden_composer_is_skipped_and_ready_checked(self):
        gpt = self._start("ok")
        box = gpt.wait_composer(5)
        self.assertEqual(box.get_attribute("aria-label"), "ChatGPT ile çalış")  # görünür olan
        title, url, label = gpt.ensure_ready()
        self.assertEqual(label, "ChatGPT ile çalış")
        gpt.open_chat("project planning")
        gpt.ensure_ready(gpt.page.url)
        with self.assertRaises(Stop):                  # başka sohbet beklenirse hazır değil
            gpt.ensure_ready("https://chatgpt.com/c/baska-sohbet")
        hidden = gpt.page.evaluate("document.querySelector('#hidden-composer div').innerText")
        self.assertEqual(hidden, "")

    def test_swallowed_send_is_ambiguous_and_not_retried(self):
        gpt = self._start("swallow")
        gpt.CONFIRM_TIMEOUT = 3
        with self.assertRaises(AmbiguousSend):
            make(gpt, FakeClaude([])).run("Görev", 120)
        self.assertEqual(len(gpt.page.evaluate("window.received")), 1)


if __name__ == "__main__":
    unittest.main()
