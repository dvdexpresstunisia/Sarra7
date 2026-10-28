import threading
import tempfile
import unittest
from unittest.mock import patch

from core import confirm
from core.action_loader import ActionRecord, ActionRegistry
from core.plugin_loader import PluginRecord, PluginRegistry
import actions.open_app as open_app_module
import actions.file_controller as file_controller_module
import actions.browser_control as browser_control_module
import actions.computer_control as computer_control_module
import actions.youtube_video as youtube_video_module


class ImmediateThread:
    def __init__(self, target, daemon=True, name=None):
        self.target = target

    def start(self):
        self.target()


class ActionAuthorizationTests(unittest.TestCase):
    def setUp(self):
        confirm.cancel("test reset")
        self.banners = []
        confirm.bind(
            show=lambda *args: self.banners.append(args),
            hide=lambda: None,
            log=lambda message: None,
        )
        confirm.clear_mission()
        confirm.clear_voice_replay()
        self.thread_patch = patch.object(confirm.threading, "Thread", ImmediateThread)
        self.thread_patch.start()

    def tearDown(self):
        self.thread_patch.stop()
        confirm.cancel("test cleanup")
        confirm.bind(show=lambda *_args: None, hide=lambda: None)

    def test_prepared_action_is_not_run_until_confirmed(self):
        calls = []
        registry = ActionRegistry({
            "open_app": ActionRecord(
                name="open_app", valid=True,
                handler=lambda parameters=None, **_: calls.append(parameters),
            )
        }, logger=lambda message: None)

        result = registry.run("open_app", {"app_name": "Chrome"})

        self.assertIn("CONFIRMATION_PENDING", result)
        self.assertEqual(calls, [])
        confirm.resolve(True)
        self.assertEqual(calls, [{"app_name": "Chrome"}])

    def test_confirmation_runs_only_exact_presented_action(self):
        calls = []
        record = lambda name: ActionRecord(
            name=name, valid=True,
            handler=lambda parameters=None, **_: calls.append((name, parameters)),
        )
        registry = ActionRegistry({
            "first": record("first"),
            "second": record("second"),
        }, logger=lambda message: None)

        registry.run("first", {"value": 1})
        confirm.resolve(True)

        self.assertEqual(calls, [("first", {"value": 1})])
        registry.run("second", {"value": 2})
        confirm.resolve(True)
        self.assertEqual(calls, [
            ("first", {"value": 1}),
            ("second", {"value": 2}),
        ])

    def test_cancellation_prevents_execution(self):
        calls = []
        registry = ActionRegistry({
            "delete_file": ActionRecord(
                name="delete_file", valid=True,
                handler=lambda parameters=None, **_: calls.append(parameters),
            )
        }, logger=lambda message: None)

        registry.run("delete_file", {"path": "D:\\safe\\target.txt"})
        confirm.resolve(False)
        self.assertEqual(calls, [])

    def test_space_cancellation_clears_pending_action(self):
        calls = []
        registry = ActionRegistry({
            "open_app": ActionRecord(
                name="open_app", valid=True,
                handler=lambda parameters=None, **_: calls.append(parameters),
            )
        }, logger=lambda message: None)

        registry.run("open_app", {"app_name": "Chrome"})
        self.assertTrue(confirm.cancel("Espace"))
        confirm.resolve(True)
        self.assertEqual(calls, [])

    def test_second_action_cannot_replace_first_pending_action(self):
        calls = []
        registry = ActionRegistry({
            "first": ActionRecord(name="first", valid=True,
                                  handler=lambda **_: calls.append("first")),
            "second": ActionRecord(name="second", valid=True,
                                   handler=lambda **_: calls.append("second")),
        }, logger=lambda message: None)

        registry.run("first", {})
        blocked = registry.run("second", {})

        self.assertIn("already waiting", blocked)
        confirm.resolve(True)
        self.assertEqual(calls, ["first"])

    def test_plugin_indirect_execution_uses_same_guard(self):
        calls = []

        def plugin(parameters, player=None, session_memory=None):
            nested_action = lambda: calls.append(parameters)
            nested_action()
            return "plugin done"

        registry = PluginRegistry({
            "nested_plugin": PluginRecord(name="nested_plugin", valid=True,
                                          run=plugin)
        }, logger=lambda message: None)
        with patch("core.plugin_loader.get_plugin_enabled", return_value=True):
            result = registry.run("nested_plugin", {"command": "indirect"})
            self.assertIn("CONFIRMATION_PENDING", result)
            self.assertEqual(calls, [])
            confirm.resolve(True)

        self.assertEqual(calls, [{"command": "indirect"}])

    def test_open_chrome_never_installs_when_not_confirmed(self):
        registry = ActionRegistry({
            "open_app": ActionRecord(
                name="open_app", valid=True,
                prepare=open_app_module.prepare_open_app,
                handler=open_app_module.open_app,
            )
        }, logger=lambda message: None)
        launcher_calls = []
        open_app_module._SYSTEM = "Windows"

        with patch.dict(open_app_module._OS_LAUNCHERS,
                        {"Windows": lambda name: launcher_calls.append(name) or True}), \
             patch.object(open_app_module, "_is_installed", return_value=True), \
             patch("actions.open_app.subprocess.Popen") as popen:
            result = registry.run("open_app", {"app_name": "Chrome"})
            self.assertIn("CONFIRMATION_PENDING", result)
            self.assertEqual(launcher_calls, [])
            popen.assert_not_called()
            confirm.resolve(False)
            popen.assert_not_called()
            self.assertEqual(launcher_calls, [])

    def test_desktop_report_write_runs_without_confirmation(self):
        confirm.begin_mission("Rédige un rapport à partir des résultats.")
        calls = []
        record = ActionRecord(
            name="file_controller", valid=True,
            requires_confirmation=file_controller_module._requires_confirmation,
            handler=lambda parameters=None, **_: calls.append(parameters),
        )
        registry = ActionRegistry({"file_controller": record}, logger=lambda message: None)

        result = registry.run("file_controller", {
            "action": "write", "path": "desktop", "name": "rapport.txt", "content": "test",
        })

        self.assertEqual(result, "Done.")
        self.assertEqual(calls[0]["name"], "rapport.txt")
        self.assertEqual(confirm.pending_title(), "")

    def test_desktop_report_overwrite_requires_confirmation(self):
        record = ActionRecord(
            name="file_controller", valid=True,
            requires_confirmation=file_controller_module._requires_confirmation,
            handler=lambda **_: "modified",
        )
        registry = ActionRegistry({"file_controller": record}, logger=lambda message: None)

        result = registry.run("file_controller", {
            "action": "write", "path": str(file_controller_module._get_desktop()),
            "name": "rapport.txt", "content": "new",
        })

        self.assertIn("CONFIRMATION_PENDING", result)
        self.assertEqual(confirm.pending_title(), "File Controller")
        confirm.cancel("test cleanup")

    def test_chrome_navigation_runs_without_confirmation(self):
        confirm.begin_mission("Cherche des hackathons dans Chrome.")
        calls = []
        record = ActionRecord(
            name="browser_control", valid=True,
            requires_confirmation=browser_control_module._requires_confirmation,
            handler=lambda parameters=None, **_: calls.append(parameters) or "Opened",
        )
        registry = ActionRegistry({"browser_control": record}, logger=lambda message: None)

        result = registry.run("browser_control", {
            "action": "go_to", "browser": "chrome", "url": "https://example.test",
        })

        self.assertEqual(result, "Opened")
        self.assertEqual(len(calls), 1)
        self.assertEqual(confirm.pending_title(), "")

    def test_explicit_chrome_search_is_mission_authorized(self):
        confirm.begin_mission("Cherche des hackathons avec des prix dans Chrome.")
        record = ActionRecord(
            name="browser_control", valid=True,
            requires_confirmation=browser_control_module._requires_confirmation,
            handler=lambda parameters=None, **_: parameters,
        )
        registry = ActionRegistry({"browser_control": record}, logger=lambda message: None)

        result = registry.run("browser_control", {
            "action": "search", "browser": "chrome", "query": "hackathons with prizes",
        })

        self.assertEqual(result["browser"], "chrome")
        self.assertEqual(confirm.pending_title(), "")

    def test_chrome_interactive_control_requires_confirmation(self):
        record = ActionRecord(
            name="browser_control", valid=True,
            requires_confirmation=browser_control_module._requires_confirmation,
            handler=lambda **_: "clicked",
        )
        registry = ActionRegistry({"browser_control": record}, logger=lambda message: None)

        result = registry.run("browser_control", {
            "action": "click", "browser": "chrome", "x": 10, "y": 20,
        })

        self.assertIn("CONFIRMATION_PENDING", result)
        self.assertEqual(confirm.pending_title(), "Browser Control")
        confirm.cancel("test cleanup")

    def test_copy_and_paste_run_without_confirmation(self):
        confirm.begin_mission("Copie ce texte puis colle-le dans la fenêtre active.")
        calls = []
        record = ActionRecord(
            name="computer_control", valid=True,
            requires_confirmation=computer_control_module._requires_confirmation,
            handler=lambda parameters=None, **_: calls.append(parameters) or "ok",
        )
        registry = ActionRegistry({"computer_control": record}, logger=lambda message: None)

        registry.run("computer_control", {"action": "copy"})
        result = registry.run("computer_control", {"action": "paste", "text": "texte Sarra"})

        self.assertEqual(result, "ok")
        self.assertEqual([call["action"] for call in calls], ["copy", "paste"])
        self.assertEqual(confirm.pending_title(), "")

    def test_copy_without_explicit_mission_requires_confirmation(self):
        record = ActionRecord(
            name="computer_control", valid=True,
            requires_confirmation=computer_control_module._requires_confirmation,
            handler=lambda **_: "copied",
        )
        registry = ActionRegistry({"computer_control": record}, logger=lambda message: None)

        result = registry.run("computer_control", {"action": "copy"})

        self.assertIn("CONFIRMATION_PENDING", result)
        confirm.cancel("test cleanup")

    def test_destructive_computer_control_still_requires_confirmation(self):
        record = ActionRecord(
            name="computer_control", valid=True,
            requires_confirmation=computer_control_module._requires_confirmation,
            handler=lambda **_: "unexpected",
        )
        registry = ActionRegistry({"computer_control": record}, logger=lambda message: None)

        result = registry.run("computer_control", {"action": "hotkey", "keys": "alt+f4"})

        self.assertIn("CONFIRMATION_PENDING", result)
        confirm.cancel("test cleanup")

    def test_non_chrome_navigation_still_requires_confirmation(self):
        record = ActionRecord(
            name="browser_control", valid=True,
            requires_confirmation=browser_control_module._requires_confirmation,
            handler=lambda **_: "unexpected",
        )
        registry = ActionRegistry({"browser_control": record}, logger=lambda message: None)

        result = registry.run("browser_control", {
            "action": "go_to", "browser": "firefox", "url": "https://example.test",
        })

        self.assertIn("CONFIRMATION_PENDING", result)
        confirm.cancel("test cleanup")

    def test_missing_chrome_is_reported_without_confirmation_or_install(self):
        registry = ActionRegistry({
            "open_app": ActionRecord(
                name="open_app", valid=True,
                prepare=open_app_module.prepare_open_app,
                handler=open_app_module.open_app,
            )
        }, logger=lambda message: None)

        with patch.object(open_app_module, "_is_installed", return_value=False), \
             patch("actions.open_app.subprocess.Popen") as popen:
            result = registry.run("open_app", {"app_name": "Chrome"})

        self.assertIn("does not appear to be installed", result)
        self.assertIn("separate explicit request", result)
        self.assertEqual(confirm.pending_title(), "")
        popen.assert_not_called()

    def test_voice_yes_confirms_the_pending_action_once(self):
        calls = []
        result = confirm.guard("open_app", {"app_name": "Chrome"},
                               "Open Chrome", "Launch Chrome",
                               lambda: calls.append("opened"))
        confirmation_id = confirm.pending_id()

        self.assertIn("CONFIRMATION_PENDING", result)
        self.assertEqual(confirm.state(), confirm.ConfirmationState.WAITING_CONFIRMATION)
        self.assertEqual(confirm.handle_voice_response("Oui, vas-y"),
                         confirm.VoiceDecision.CONFIRMED)
        self.assertEqual(calls, ["opened"])
        self.assertEqual(confirm.pending_id(), "")
        self.assertFalse(confirm.resolve(True, confirmation_id))

    def test_youtube_play_is_confirmed_by_one_word_yes(self):
        calls = []
        registry = ActionRegistry({
            "youtube_video": ActionRecord(
                name="youtube_video", valid=True,
                handler=lambda parameters=None, **_: calls.append(parameters),
            )
        }, logger=lambda message: None)

        result = registry.run("youtube_video", {
            "action": "play", "query": "science hackathon winners",
        })

        self.assertIn("CONFIRMATION_PENDING", result)
        self.assertEqual(calls, [])
        self.assertEqual(confirm.handle_voice_response("Oui"),
                         confirm.VoiceDecision.CONFIRMED)
        self.assertEqual(calls, [{"action": "play", "query": "science hackathon winners"}])
        self.assertEqual(confirm.handle_voice_response("Oui"),
                         confirm.VoiceDecision.NO_PENDING)

    def test_youtube_video_opens_in_chrome(self):
        with patch("actions.browser_control._open_native",
                   return_value="Opened in chrome") as open_native:
            youtube_video_module._open_url("https://www.youtube.com/watch?v=abcdefghijk")

        open_native.assert_called_once_with(
            "https://www.youtube.com/watch?v=abcdefghijk", "chrome"
        )

    def test_voice_refusal_rejects_without_execution(self):
        calls = []
        confirm.guard("send_message", {"text": "hello"}, "Send message", "hello",
                      lambda: calls.append("sent"))

        self.assertEqual(confirm.handle_voice_response("Non"),
                         confirm.VoiceDecision.REJECTED)
        self.assertEqual(confirm.state(), confirm.ConfirmationState.REJECTED)
        self.assertEqual(calls, [])

    def test_voice_cancel_cancels_without_execution(self):
        calls = []
        confirm.guard("delete_file", {"name": "x"}, "Delete file", "x",
                      lambda: calls.append("deleted"))

        self.assertEqual(confirm.handle_voice_response("Attends"),
                         confirm.VoiceDecision.CANCELLED)
        self.assertEqual(confirm.state(), confirm.ConfirmationState.CANCELLED)
        self.assertEqual(calls, [])

    def test_ambiguous_voice_requires_clarification(self):
        calls = []
        confirm.guard("send_message", {"text": "hello"}, "Send message", "hello",
                      lambda: calls.append("sent"))

        self.assertEqual(confirm.handle_voice_response("Peut-être tout à l'heure"),
                         confirm.VoiceDecision.AMBIGUOUS)
        self.assertTrue(confirm.clarification_required())
        self.assertEqual(confirm.state(), confirm.ConfirmationState.WAITING_CONFIRMATION)
        self.assertEqual(calls, [])
        self.assertEqual(confirm.handle_voice_response("D'accord"),
                         confirm.VoiceDecision.CONFIRMED)
        self.assertEqual(calls, ["sent"])

    def test_yes_without_pending_confirmation_authorizes_nothing(self):
        self.assertEqual(confirm.handle_voice_response("Oui"),
                         confirm.VoiceDecision.NO_PENDING)
        self.assertTrue(confirm.is_confirmation_phrase("Oui"))
        self.assertEqual(confirm.pending_id(), "")

    def test_confirmation_expires_without_running(self):
        calls = []
        with patch("core.confirm.time.monotonic", side_effect=[1.0, 100.0]):
            confirm.guard("shutdown", {}, "Shutdown", "Stop Sarra",
                          lambda: calls.append("shutdown"))
            self.assertEqual(confirm.pending_id(), "")

        self.assertEqual(confirm.state(), confirm.ConfirmationState.EXPIRED)
        self.assertEqual(calls, [])

    def test_hud_failure_clears_the_pending_state(self):
        def fail_show(*_args):
            raise RuntimeError("HUD unavailable")

        confirm.bind(show=fail_show, hide=lambda: None)
        result = confirm.guard("shutdown", {}, "Shutdown", "Stop Sarra", lambda: None)

        self.assertIn("HUD unavailable", result)
        self.assertEqual(confirm.state(), confirm.ConfirmationState.IDLE)
        self.assertEqual(confirm.pending_id(), "")

    def test_repeated_voice_confirmation_cannot_repeat_action(self):
        calls = []
        confirm.guard("open_app", {"app_name": "Chrome"}, "Open Chrome", "Launch",
                      lambda: calls.append("opened"))

        self.assertEqual(confirm.handle_voice_response("Je confirme"),
                         confirm.VoiceDecision.CONFIRMED)
        self.assertEqual(confirm.handle_voice_response("Oui"),
                         confirm.VoiceDecision.NO_PENDING)
        self.assertEqual(calls, ["opened"])

    def test_confirmation_id_cannot_confirm_a_different_action(self):
        calls = []
        confirm.guard("first", {"value": 1}, "First", "First action",
                      lambda: calls.append("first"))
        first_id = confirm.pending_id()
        self.assertTrue(confirm.resolve(True, first_id))
        confirm.guard("second", {"value": 2}, "Second", "Second action",
                      lambda: calls.append("second"))
        second_id = confirm.pending_id()

        self.assertNotEqual(first_id, second_id)
        self.assertFalse(confirm.resolve(True, first_id))
        self.assertEqual(calls, ["first"])
        self.assertTrue(confirm.resolve(True, second_id))
        self.assertEqual(calls, ["first", "second"])

    def test_mission_allows_multiple_scoped_search_and_report_steps(self):
        confirm.begin_mission(
            "Cherche des hackathons et concours avec des prix, puis rédige un rapport."
        )
        calls = []
        registry = ActionRegistry({
            "web_search": ActionRecord(
                name="web_search", valid=True,
                handler=lambda parameters=None, **_: calls.append(parameters),
            ),
            "file_controller": ActionRecord(
                name="file_controller", valid=True,
                requires_confirmation=file_controller_module._requires_confirmation,
                handler=lambda parameters=None, **_: calls.append(parameters),
            ),
        }, logger=lambda message: None)

        with tempfile.TemporaryDirectory() as directory:
            first = registry.run("web_search", {"query": "hackathons with prizes"})
            second = registry.run("web_search", {"query": "concours technologiques"})
            report = registry.run("file_controller", {
                "action": "create_file", "path": directory,
                "name": "rapport.txt", "content": "verified findings",
            })

        self.assertEqual((first, second, report), ("Done.", "Done.", "Done."))
        self.assertEqual(len(calls), 3)
        self.assertEqual(confirm.pending_title(), "")

    def test_french_mission_allows_matching_english_ai_trial_search(self):
        confirm.begin_mission(
            "Cherche des hackathons avec des prix et des IA en essais gratuits dans Chrome."
        )

        self.assertTrue(confirm.mission_authorizes(
            "web_search", {"query": "new AI tools with free trials"}
        ))
        self.assertTrue(confirm.mission_authorizes(
            "web_search", {"query": "hackathons with prizes innovation technology"}
        ))

    def test_unrelated_request_does_not_inherit_previous_mission(self):
        confirm.begin_mission("Cherche des hackathons et rédige un rapport.")
        self.assertTrue(confirm.mission_authorizes(
            "web_search", {"query": "hackathons avec prix"}
        ))

        confirm.begin_mission("Éteins l'ordinateur.")

        self.assertFalse(confirm.mission_authorizes(
            "web_search", {"query": "hackathons avec prix"}
        ))

    def test_voice_replay_token_only_blocks_exact_action(self):
        params = {"app_name": "Chrome"}
        confirm.guard("open_app", params, "Open Chrome", "Launch",
                      lambda: None)
        self.assertTrue(confirm.resolve(True, confirm.pending_id()))

        self.assertFalse(confirm.consume_voice_replay("send_message", {"text": "hi"}))
        self.assertTrue(confirm.consume_voice_replay("open_app", params))
        self.assertFalse(confirm.consume_voice_replay("open_app", params))

    def test_sensitive_action_still_requires_confirmation_inside_mission(self):
        confirm.begin_mission("Cherche des hackathons et rédige un rapport.")
        registry = ActionRegistry({
            "send_message": ActionRecord(
                name="send_message", valid=True,
                handler=lambda **_: "sent",
            )
        }, logger=lambda message: None)

        result = registry.run("send_message", {"text": "hello"})

        self.assertIn("CONFIRMATION_PENDING", result)
        self.assertEqual(confirm.pending_title(), "Send Message")


if __name__ == "__main__":
    unittest.main()
