import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main
import memory.memory_manager as memory


class _EmptyRegistry:
    def get_tool_declarations(self):
        return []


class GeminiMemoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.memory_path = Path(self.temp.name) / "memory" / "long_term.json"
        self.api_path = Path(self.temp.name) / "api_keys.json"
        self.api_path.write_text(json.dumps({"assistant_name": "Sarra", "user_name": "Karim"}), encoding="utf-8")
        self.memory_patch = patch.object(memory, "MEMORY_PATH", self.memory_path)
        self.api_patch = patch.object(main, "API_CONFIG_PATH", self.api_path)
        self.memory_patch.start()
        self.api_patch.start()

    def tearDown(self):
        self.api_patch.stop()
        self.memory_patch.stop()
        self.temp.cleanup()

    def test_restart_reaches_live_system_instruction(self):
        memory.update_memory({"identity": {"name": {"value": "Karim"}}})
        memory.save_conversation(["User: test avatar continuity", "Sarra: next test is authorization"], "Avatar continuity test")
        memory.update_project_state(
            "avatar_sarra",
            name="Avatar Sarra",
            state="integration testing",
            completed_tasks=["persistent memory audit"],
            next_step="restore context in Gemini Live",
            active=True,
        )

        jarvis = main.JarvisLive.__new__(main.JarvisLive)
        jarvis.ui = SimpleNamespace(write_log=lambda *_args: None)
        jarvis._action_registry = _EmptyRegistry()
        jarvis._plugin_registry = _EmptyRegistry()
        jarvis._enhanced_live = False
        jarvis._tuned_live = False
        jarvis._resume_handle = None

        config = jarvis._build_config()
        system_instruction = config.system_instruction

        self.assertIn("Avatar Sarra", system_instruction)
        self.assertIn("restore context in Gemini Live", system_instruction)
        self.assertTrue(jarvis._last_memory_context)
        self.assertIn("Avatar continuity test", system_instruction)
        self.assertIn("Karim a la priorité absolue", system_instruction)
        self.assertIn("Ne fais jamais de monologue spontané", system_instruction)
        self.assertIn("N'appelle pas deux fois le même outil", system_instruction)
        self.assertIn("Ne rapporte terminé que ce qui est vérifié", system_instruction)
        self.assertEqual(
            str(config.realtime_input_config.activity_handling),
            "ActivityHandling.NO_INTERRUPTION",
        )


if __name__ == "__main__":
    unittest.main()
