import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ui


class FirstStartupConfigTests(unittest.TestCase):
    def test_first_startup_avatar_is_85_percent_and_stays_centered(self):
        geometry = ui.QRect(100, 200, 520, 620)

        scaled = ui._first_startup_avatar_geometry(geometry)

        self.assertEqual((scaled.width(), scaled.height()), (442, 527))
        self.assertEqual(scaled.center(), geometry.center())

    def test_avatar_window_geometry_smaller_than_vrm_view_uses_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            geometry_file = Path(temp_dir) / "avatar_window.json"
            geometry_file.write_text(
                json.dumps({"x": 162, "y": 591, "width": 260, "height": 260}),
                encoding="utf-8",
            )
            with (
                patch.object(ui, "AVATAR_WINDOW_FILE", geometry_file),
                patch.object(
                    ui, "AVATAR_WINDOW_DEFAULT_FILE",
                    Path(temp_dir) / "missing-default.json",
                ),
            ):
                geometry = ui._load_avatar_window_geometry()

        self.assertEqual(
            (geometry.width(), geometry.height()),
            ui._AVATAR_WIN_DEFAULT,
        )

    def test_setup_saves_requested_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "config"
            config_file = config_dir / "api_keys.json"
            window = SimpleNamespace(
                _overlay=None,
                _apply_state=lambda _state: None,
                _assistant_name="",
                _log=SimpleNamespace(append_log=lambda _message: None),
                _setup_return_to_avatar_only=False,
                _finish_setup_transition=lambda: None,
            )

            with (
                patch.object(ui, "CONFIG_DIR", config_dir),
                patch.object(ui, "API_FILE", config_file),
                patch.object(ui, "_read_full_config", return_value={"assistant_name": "Sarra"}),
            ):
                ui.MainWindow._on_setup_done(window, "test-key", "windows")

            config = json.loads(config_file.read_text(encoding="utf-8"))

        self.assertEqual(config["voice_name"], "Aoede")
        self.assertEqual(config["assistant_name"], "Sarra")
        self.assertEqual(config["user_name"], "")
        self.assertEqual(config["ui_color"], "#00d4ff")
        self.assertEqual(window._assistant_name, "Sarra")

    def test_setup_preserves_existing_preferences(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "config"
            config_file = config_dir / "api_keys.json"
            window = SimpleNamespace(
                _overlay=None,
                _apply_state=lambda _state: None,
                _assistant_name="",
                _log=SimpleNamespace(append_log=lambda _message: None),
                _setup_return_to_avatar_only=False,
                _finish_setup_transition=lambda: None,
            )
            existing_config = {
                "assistant_name": "Sarra",
                "voice_name": "Kore",
                "theme": "dark",
                "user_name": "Ari",
                "ui_color": "#123456",
            }

            with (
                patch.object(ui, "CONFIG_DIR", config_dir),
                patch.object(ui, "API_FILE", config_file),
                patch.object(ui, "_read_full_config", return_value=existing_config),
            ):
                ui.MainWindow._on_setup_done(window, "replacement-key", "windows")

            config = json.loads(config_file.read_text(encoding="utf-8"))

        self.assertEqual(config["gemini_api_key"], "replacement-key")
        self.assertEqual(config["os_system"], "windows")
        self.assertEqual(config["voice_name"], "Kore")
        self.assertEqual(config["theme"], "dark")
        self.assertEqual(config["user_name"], "Ari")
        self.assertEqual(config["ui_color"], "#123456")

    def test_startup_config_requires_key_and_supported_os(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "api_keys.json"
            with patch.object(ui, "API_FILE", config_file):
                config_file.write_text(
                    json.dumps({"gemini_api_key": "test-key", "os_system": "freebsd"}),
                    encoding="utf-8",
                )
                self.assertFalse(ui.MainWindow._check_config(SimpleNamespace()))

                config_file.write_text(
                    json.dumps({"gemini_api_key": "test-key", "os_system": "linux"}),
                    encoding="utf-8",
                )
                self.assertTrue(ui.MainWindow._check_config(SimpleNamespace()))

                config_file.write_text(
                    json.dumps({"gemini_api_key": "  ", "os_system": "linux"}),
                    encoding="utf-8",
                )
                self.assertFalse(ui.MainWindow._check_config(SimpleNamespace()))

    def test_initialization_keeps_the_normal_avatar_only_mode(self):
        overlay = SimpleNamespace(hidden=False, hide=lambda: setattr(overlay, "hidden", True))
        mode_changes = []
        window = SimpleNamespace(
            _overlay=overlay,
            _first_setup=True,
            _setup_return_to_avatar_only=True,
            _avatar_only=True,
        )
        def set_avatar_only(enabled):
            mode_changes.append(enabled)
            window._avatar_only = enabled
        window._set_avatar_only = set_avatar_only

        ui.MainWindow._finish_setup_transition(window)

        self.assertTrue(overlay.hidden)
        self.assertIsNone(window._overlay)
        self.assertFalse(window._first_setup)
        self.assertTrue(window._avatar_only)
        self.assertEqual(mode_changes, [])

    def test_avatar_mode_shortcut_toggles_both_directions(self):
        window = SimpleNamespace(_avatar_only=True)
        window._set_avatar_only = lambda enabled: setattr(window, "_avatar_only", enabled)

        ui.MainWindow._toggle_avatar_only(window)
        self.assertFalse(window._avatar_only)

        ui.MainWindow._toggle_avatar_only(window)
        self.assertTrue(window._avatar_only)


if __name__ == "__main__":
    unittest.main()
