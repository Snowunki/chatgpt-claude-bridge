"""Language switching must not change tasks, sessions, or model responses."""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import bridge_gui
import ui_i18n
from bridge import Reply
import test_gui
from test_gui import FakeBackend, FakeGpt, SESSION


class TranslationTests(unittest.TestCase):
    def test_diagnostics_preserve_user_values(self):
        self.assertEqual(ui_i18n.diagnostic("Hazır · Görev {raw}", "en"),
                         "Ready · Görev {raw}")
        self.assertEqual(ui_i18n.diagnostic("Hata: Süre sınırı doldu.", "en"),
                         "Error: Time limit reached.")
        self.assertEqual(ui_i18n.diagnostic("Unknown {external} error", "en"),
                         "Unknown {external} error")

    def test_preferences_and_invalid_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "settings.json"
            self.assertEqual(ui_i18n.load_language(path), "tr")
            for language in ("en", "tr"):
                ui_i18n.save_language(path, language)
                self.assertEqual(ui_i18n.load_language(path), language)
            for invalid in ('{', '[]', '{"language":"other"}', '{"language":[]}'):
                path.write_text(invalid, encoding="utf-8")
                self.assertEqual(ui_i18n.load_language(path), "tr")

    def test_ui_warnings_have_translations(self):
        for value in (bridge_gui.TOKEN_NOTE, bridge_gui.TOOLS_WARN,
                      bridge_gui.OPEN_TAB, bridge_gui.NEW_SESSION):
            self.assertNotEqual(ui_i18n.translate(value, "en"), value)
        for value, _ in bridge_gui.STATUS.values():
            self.assertNotEqual(ui_i18n.translate(value, "en"), value)


class LanguageGuiTests(unittest.TestCase):
    pump = test_gui.GuiTests.pump
    make_app = test_gui.GuiTests.make_app

    def test_switch_preserves_selections_inputs_and_transcript(self):
        app = self.make_app(FakeBackend(FakeGpt([])))
        app.gpt_box.current(1)
        app.claude_box.current(1)
        app.tools_var.set(True)
        app._update_summary()
        app.task_text.insert("1.0", "Görev {unchanged} / Task")
        app.minutes_var.set("47")
        app.messages_var.set("19")
        app._handle("done", "Sonuç {model output}")
        app._set_text(app.error_var, "Görev metni boş.")
        app._log("Durdur istendi.")
        selected = app._selected()
        for language in ("en", "tr", "en"):
            app.set_language(language)
            app.update()
            self.assertEqual(app._selected(), selected)
            self.assertEqual(app.task_text.get("1.0", "end-1c"), "Görev {unchanged} / Task")
            self.assertEqual(app.folder_var.get(), SESSION.cwd)
            self.assertEqual(app.minutes_var.get(), "47")
            self.assertEqual(app.messages_var.get(), "19")
            self.assertTrue(app.tools_var.get())
            self.assertEqual(app.result_text, "Sonuç {model output}")
            self.assertEqual(app.bubbles[-1][2], "Sonuç {model output}")
        self.assertEqual(app.connect_btn.cget("text"), "Connect")
        self.assertEqual(app.start_btn.cget("text"), "Start")
        self.assertEqual(app.error_var.get(), "Enter a task first.")
        self.assertIn("Stop requested.", app.log_text.get("1.0", "end"))
        self.assertIn("Engine setup", app.summary_var.get())
        self.assertIn("Tool mode:", app.tools_warn_var.get())
        self.assertIn("full history", app.token_var.get())
        headings = [w.cget("text") for w, _ in app._translated_widgets if w.winfo_exists()]
        self.assertIn("Result", headings)

    def test_switch_while_waiting_does_not_restart_or_send(self):
        gpt = FakeGpt([], block=True)
        be = FakeBackend(gpt)
        app = self.make_app(be)
        app.task_text.insert("1.0", "Görev")
        app.start()
        self.pump(app, lambda: app.run_state == "gpt_wait")
        worker = app.worker
        sent = list(gpt.sent)
        app.language_var.set("English")
        app.language_box.event_generate("<<ComboboxSelected>>")
        app.update()
        self.assertEqual(app.language, "en")
        self.assertIs(app.worker, worker)
        self.assertEqual(gpt.sent, sent)
        self.assertEqual(app.status_var.get(), "Waiting for ChatGPT")
        self.assertEqual(str(app.start_btn["state"]), "disabled")
        app.stop()
        self.pump(app, lambda: app.run_state == "stopped")
        self.assertEqual(app.status_var.get(), "Stopped")

    def test_english_startup_validation_and_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "settings.json"
            app = bridge_gui.BridgeApp(FakeBackend(FakeGpt([])), language="en", settings_path=path)
            app.withdraw()
            self.addCleanup(app.destroy)
            self.assertEqual(app.title(), "ChatGPT ↔ Claude Bridge")
            self.assertEqual(app.gpt_box.get(), "Conversation open in Chrome")
            self.assertEqual(app.claude_box.get(), "New session · low token use")
            app.connect()
            self.pump(app, lambda: not app.connecting and not app.preparing)
            app.start()
            self.assertEqual(app.error_var.get(), "Enter a task first.")
            app.set_language("tr")
            self.assertEqual(ui_i18n.load_language(path), "tr")
            self.assertEqual(app.error_var.get(), "Görev metni boş.")
            app.set_language("en")
            self.assertEqual(ui_i18n.load_language(path), "en")


if __name__ == "__main__":
    unittest.main()
