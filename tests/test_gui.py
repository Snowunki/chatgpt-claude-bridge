"""Pencere testleri: sahte ChatGPT/Claude ile (gerçek model çağrısı yok).

python -m unittest tests.test_gui -v
"""
import pathlib
import sys
import tempfile
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bridge  # noqa: E402
import bridge_gui  # noqa: E402
from bridge import Reply  # noqa: E402

CUR = ("Açık Sohbet", "https://chatgpt.com/c/acik")
CHATS = [CUR, ("Project Planning", "https://chatgpt.com/c/planning")]
SESSION = bridge.ClaudeSession("1234abcd-x", r"C:\projects\demo-game", "Engine setup", "", 0)


class FakeGpt:
    def __init__(self, replies, block=False):
        self.replies, self.block = list(replies), block
        self.sent, self.ids, self.cancel = [], set(), None

    def assistant_ids(self):
        return set(self.ids)

    def send(self, text):
        self.sent.append(text)

    def wait_reply(self, known, timeout):
        if self.block:  # durdurulana kadar yanıt gelmez
            while not self.cancel.is_set():
                time.sleep(0.05)
            return Reply("", "", False)
        time.sleep(0.2)
        r = self.replies.pop(0)
        self.ids.add(r.msg_id)
        return r

    def close(self):
        pass


class FakeBackend:
    def __init__(self, gpt, claude_out=("4",), fail=None, claude_fail=None, login_gate=None,
                 prepare_gate=None, prepare_fail=None):
        self.gpt, self.claude_out, self.fail = gpt, list(claude_out), fail
        self.claude_fail, self.login_gate = claude_fail, login_gate
        self.prepare_gate, self.prepare_fail, self.prepared = prepare_gate, prepare_fail, []
        self.claude_calls, self.opened, self.connects = [], [], 0
        self.alive = True

    def check_claude(self, log):
        if self.claude_fail:
            raise bridge.Stop(self.claude_fail)
        return {"path": r"C:\fake\claude.exe", "version": "9.9.9", "env": "NODE_USE_SYSTEM_CA=1"}

    def list_claude(self):
        return [SESSION]

    def connect_gpt(self, log, set_state, cancel):
        self.connects += 1
        set_state("connecting", "Bağlanıyor…")
        time.sleep(0.3)
        if self.login_gate is not None:
            set_state("login", "Chrome'da ChatGPT'ye giriş yapın")
            while not self.login_gate.is_set():
                time.sleep(0.05)
        self.alive = True
        return CUR, CHATS

    def cdp_alive(self):
        return self.alive

    def prepare_chat(self, url, log):
        self.prepared.append(url)
        if self.prepare_gate is not None:
            while not self.prepare_gate.is_set():
                time.sleep(0.05)
        if self.prepare_fail:
            raise bridge.Stop(self.prepare_fail)
        title = next((t for t, u in CHATS if u == url), CUR[0])
        return title, url or CUR[1], "ChatGPT ile çalış"

    def open_gpt(self, url, log, cancel):
        if self.fail:
            raise bridge.Stop(self.fail)
        self.opened.append(url)
        self.gpt.cancel = cancel
        return self.gpt

    def make_claude(self, session, cancel, tools=False, workdir=None, log=None):
        self.claude_mode = (tools, workdir)

        def run(msg):
            self.claude_calls.append((msg, session))
            time.sleep(0.2)
            return self.claude_out.pop(0)
        return run


class GuiTests(unittest.TestCase):
    def make_app(self, backend, connect=True):
        app = bridge_gui.BridgeApp(backend)
        app.withdraw()
        self.addCleanup(app.destroy)
        if connect:
            app.connect()
            self.pump(app, lambda: not app.connecting and not app.preparing)
        return app

    def pump(self, app, cond, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            app.update()
            if cond():
                return
            time.sleep(0.02)
        self.fail(f"koşul gerçekleşmedi; durum={app.run_state}, gpt={app.gpt_state}, "
                  f"claude={app.claude_state}")

    def state(self, w):
        return str(w["state"])

    def test_start_enabled_only_after_connect(self):
        app = self.make_app(FakeBackend(FakeGpt([])), connect=False)
        self.assertEqual(self.state(app.start_btn), "disabled")
        self.assertIn("Bağlı değil", app.gpt_ind_var.get())
        app.connect()
        self.pump(app, lambda: not app.connecting and not app.preparing)
        self.assertEqual(app.gpt_ind_var.get(), "ChatGPT: Hazır · Açık Sohbet")
        self.assertEqual(app.claude_ind_var.get(), "Claude: Claude CLI hazır")
        self.assertEqual(self.state(app.start_btn), "normal")

    def test_connect_twice_does_not_reconnect_while_busy(self):
        be = FakeBackend(FakeGpt([]))
        app = self.make_app(be, connect=False)
        app.connect()
        app.connect()                                   # ikinci basış yok sayılır
        self.assertEqual(self.state(app.connect_btn), "disabled")
        self.pump(app, lambda: not app.connecting)
        self.assertEqual(be.connects, 1)

    def test_login_prompt_then_auto_continue(self):
        gate = threading.Event()
        app = self.make_app(FakeBackend(FakeGpt([]), login_gate=gate), connect=False)
        app.connect()
        self.pump(app, lambda: app.gpt_state == "login")
        self.assertIn("Chrome'da ChatGPT'ye giriş yapın", app.gpt_ind_var.get())
        self.assertEqual(self.state(app.start_btn), "disabled")
        gate.set()                                      # kullanıcı giriş yaptı
        self.pump(app, lambda: app.gpt_state == "ready")
        self.pump(app, lambda: self.state(app.start_btn) == "normal")

    def test_claude_error_shown_on_its_side(self):
        app = self.make_app(FakeBackend(FakeGpt([]), claude_fail="Claude CLI'da giriş yapılmamış."))
        self.assertEqual(app.claude_state, "error")
        self.assertIn("Claude: Hata: Claude CLI'da giriş yapılmamış.", app.claude_ind_var.get())
        self.assertEqual(app.gpt_state, "ready")
        self.assertEqual(self.state(app.start_btn), "disabled")

    def test_chrome_closed_then_reconnect_keeps_selection(self):
        be = FakeBackend(FakeGpt([]))
        app = self.make_app(be)
        app.HEALTH_MS = 50
        app.gpt_box.current(1)
        app.claude_box.current(1)
        app._update_summary()
        be.alive = False                                # Chrome kapandı
        app.after_cancel(app._health_id)
        app._health_id = app.after(10, app._health)
        self.pump(app, lambda: app.gpt_state == "error")
        self.assertIn("Chrome kapalı", app.gpt_ind_var.get())
        self.assertEqual(self.state(app.start_btn), "disabled")
        app.connect()
        self.pump(app, lambda: not app.connecting and app.gpt_state == "ready")
        self.assertEqual(be.prepared[-1], "https://chatgpt.com/c/planning")
        self.assertEqual(app.gpt_box.get(), "Project Planning")   # seçim korundu
        self.assertEqual(app.claude_box.get(), bridge.describe(SESSION))
        self.assertEqual(self.state(app.start_btn), "normal")

    def test_ready_only_after_chat_and_box_verified(self):
        gate = threading.Event()
        be = FakeBackend(FakeGpt([]), prepare_gate=gate)
        app = self.make_app(be, connect=False)
        app.connect()
        self.pump(app, lambda: not app.connecting and app.preparing)
        self.assertEqual(app.gpt_state, "connecting")
        self.assertIn("mesaj kutusu kontrol ediliyor", app.gpt_ind_var.get())
        self.assertEqual(self.state(app.start_btn), "disabled")
        gate.set()
        self.pump(app, lambda: app.gpt_state == "ready")
        self.assertEqual(self.state(app.start_btn), "normal")

    def test_box_not_found_is_error_not_ready(self):
        msg = "Yazılabilir mesaj kutusu 20 sn içinde bulunamadı"
        app = self.make_app(FakeBackend(FakeGpt([]), prepare_fail=msg))
        self.assertEqual(app.gpt_state, "error")
        self.assertIn(msg, app.gpt_ind_var.get())
        self.assertEqual(self.state(app.start_btn), "disabled")

    def test_selecting_chat_opens_and_verifies_it(self):
        be = FakeBackend(FakeGpt([]))
        app = self.make_app(be)
        app.gpt_box.current(1)
        app.gpt_box.event_generate("<<ComboboxSelected>>")
        self.pump(app, lambda: be.prepared[-1] == "https://chatgpt.com/c/planning"
                  and app.gpt_state == "ready")
        self.assertEqual(app.gpt_ind_var.get(), "ChatGPT: Hazır · Project Planning")

    def test_lists_defaults_and_summary(self):
        app = self.make_app(FakeBackend(FakeGpt([])))
        self.assertEqual(app.gpt_box.get(), "Chrome'da açık sohbet: Açık Sohbet")
        self.assertEqual(app.claude_box.get(), bridge_gui.NEW_SESSION)
        self.assertEqual([t for t, _ in app.gpt_choices][1:], ["Project Planning"])
        app.gpt_box.current(1)
        app.claude_box.current(1)
        app._update_summary()
        self.assertIn("Project Planning", app.summary_var.get())
        self.assertIn("Engine setup", app.summary_var.get())
        self.assertIn("token", app.token_var.get())

    def test_two_messages_on_correct_sides_then_done(self):
        gpt = FakeGpt([Reply("a1", "TO_CLAUDE\n2+2 kaç?", True),
                       Reply("a2", "DONE\nCevap: 4", True)])
        be = FakeBackend(gpt)
        app = self.make_app(be)
        app.gpt_box.current(1)
        app._update_summary()
        app.task_text.insert("1.0", "Test görevi\nikinci satır")
        app.start()
        seen = set()
        self.pump(app, lambda: (seen.add(app.run_state), app.run_state == "done")[1])
        self.assertIn("gpt_wait", seen)
        self.assertIn("claude_wait", seen)
        self.assertEqual(app.status_var.get(), "Tamamlandı")
        self.assertEqual(app.bubbles, [("ChatGPT", "left", "2+2 kaç?"),
                                       ("Claude", "right", "4"),
                                       ("Sonuç", "center", "Cevap: 4")])
        self.assertEqual(app.claude_ind_var.get(), "Claude: Claude yanıt verdi")
        self.assertEqual(be.opened, ["https://chatgpt.com/c/planning"])
        self.assertEqual(be.claude_calls, [("2+2 kaç?", None)])  # Claude seçilmedi: geçmişsiz
        self.assertEqual(len(gpt.sent), 2)
        self.assertEqual(self.state(app.start_btn), "normal")
        self.assertEqual(self.state(app.stop_btn), "disabled")

    def test_stop_sends_nothing_more(self):
        gpt = FakeGpt([], block=True)
        app = self.make_app(FakeBackend(gpt))
        app.task_text.insert("1.0", "Görev")
        app.start()
        self.pump(app, lambda: app.run_state == "gpt_wait")
        self.assertEqual(self.state(app.connect_btn), "disabled")
        app.stop()
        self.pump(app, lambda: app.run_state == "stopped")
        self.assertEqual(app.status_var.get(), "Durduruldu")
        time.sleep(0.5)
        app.update()
        self.assertEqual(len(gpt.sent), 1)
        self.assertEqual(app.bubbles, [])

    def test_run_error_is_shown(self):
        msg = "Tarayıcıya bağlanılamadı (http://127.0.0.1:9222). ECONNREFUSED"
        app = self.make_app(FakeBackend(FakeGpt([]), fail=msg))
        app.task_text.insert("1.0", "Görev")
        app.start()
        self.pump(app, lambda: app.run_state == "error")
        self.assertEqual(app.status_var.get(), "Hata")
        self.assertEqual(app.error_var.get(), msg)
        self.assertEqual(app.bubbles, [])

    def test_window_stays_responsive_while_connecting(self):
        gate = threading.Event()
        app = self.make_app(FakeBackend(FakeGpt([]), login_gate=gate), connect=False)
        app.connect()
        t0, ticks = time.time(), 0
        while time.time() - t0 < 1:
            app.update()
            ticks += 1
            time.sleep(0.01)
        self.assertGreater(ticks, 30)
        gate.set()
        self.pump(app, lambda: not app.connecting)


class ToolsModeGuiTests(GuiTests.__bases__[0]):
    pump = GuiTests.pump
    make_app = GuiTests.make_app

    def test_tools_off_by_default_text_only(self):
        gpt = FakeGpt([Reply("a1", "DONE\nok", True)])
        be = FakeBackend(gpt)
        app = self.make_app(be)
        self.assertFalse(app.tools_var.get())
        self.assertIn("yalnızca metin", app.summary_var.get())
        self.assertEqual(str(app.folder_entry["state"]), "disabled")
        app.task_text.insert("1.0", "Görev")
        app.start()
        self.pump(app, lambda: app.run_state == "done")
        self.assertEqual(be.claude_mode, (False, None))

    def test_tools_new_session_requires_folder(self):
        be = FakeBackend(FakeGpt([]))
        app = self.make_app(be)
        app.tools_var.set(True)
        app._update_summary()
        self.assertIn("Araçlı mod", app.tools_warn_var.get())
        self.assertEqual(str(app.folder_entry["state"]), "normal")
        app.task_text.insert("1.0", "Görev")
        app.start()
        app.update()
        self.assertIn("proje klasörü seçin", app.error_var.get())
        self.assertIsNone(app.worker)                     # başlatılmadı

    def test_tools_with_folder_passes_mode_and_note(self):
        folder = tempfile.mkdtemp()
        gpt = FakeGpt([Reply("a1", "DONE\nok", True)])
        be = FakeBackend(gpt)
        app = self.make_app(be)
        app.tools_var.set(True)
        app.folder_var.set(folder)
        app._update_summary()
        self.assertIn("araçlı", app.summary_var.get())
        app.task_text.insert("1.0", "Görev")
        app.start()
        self.pump(app, lambda: app.run_state == "done")
        self.assertEqual(be.claude_mode, (True, folder))
        self.assertIn(f"Claude {folder} proje klasöründe dosya okuyup düzenleyebilir", gpt.sent[0])

    def test_tools_with_session_uses_session_folder(self):
        be = FakeBackend(FakeGpt([Reply("a1", "DONE\nok", True)]))
        app = self.make_app(be)
        app.tools_var.set(True)
        app.claude_box.current(1)
        app._update_summary()
        self.assertEqual(app.folder_var.get(), SESSION.cwd)
        self.assertEqual(str(app.folder_entry["state"]), "disabled")
        app.task_text.insert("1.0", "Görev")
        app.start()
        self.pump(app, lambda: app.run_state == "done")
        self.assertEqual(be.claude_mode, (True, SESSION.cwd))


class ChromeLaunchTests(unittest.TestCase):
    def make_backend(self):
        be = bridge_gui.Backend(profile=pathlib.Path(tempfile.mkdtemp()))
        self.launches = []
        self.up = threading.Event()

        def launch(exe):
            self.launches.append(exe)
            threading.Timer(0.5, self.up.set).start()   # port yarım sn sonra açılır

        be._launch = launch
        be.cdp_alive = self.up.is_set
        be.find_chrome = lambda: "chrome.exe"
        return be

    def test_single_launch_even_with_two_connects(self):
        be = self.make_backend()
        results = []
        ts = [threading.Thread(target=lambda: results.append(
            be.ensure_chrome(lambda k, t: None, None))) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(5)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(sorted(results), [False, True])
        self.assertFalse(be.ensure_chrome(lambda k, t: None, None))   # açıkken dokunmaz
        self.assertEqual(len(self.launches), 1)

    def test_profile_open_without_port_is_reported(self):
        be = self.make_backend()
        be._profile_in_use = lambda: True
        with self.assertRaises(bridge.Stop) as cm:
            be.ensure_chrome(lambda k, t: None, None)
        self.assertIn("9222 portu olmadan açık", str(cm.exception))
        self.assertEqual(self.launches, [])


if __name__ == "__main__":
    unittest.main()
