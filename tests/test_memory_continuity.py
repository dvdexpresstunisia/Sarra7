import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import memory.memory_manager as memory


class MemoryContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "long_term.json"
        self.patch_path = patch.object(memory, "MEMORY_PATH", self.path)
        self.patch_path.start()
        memory._memory_load_error = None

    def tearDown(self):
        self.patch_path.stop()
        self.temp.cleanup()

    def test_memory_paths_separate_development_from_frozen_storage(self):
        with patch.object(memory, "MEMORY_PATH", Path("temp") / "memory" / "long_term.json"):
            with patch.object(memory.sys, "frozen", False, create=True):
                active, source = memory.get_persistent_memory_paths()
            self.assertEqual(active, memory.MEMORY_PATH)
            self.assertIsNone(source)

    def test_existing_data_is_migrated_and_preserved(self):
        original = {
            "identity": {"name": {"value": "Karim", "updated": "2026-10-28"}},
            "preferences": {}, "projects": {}, "relationships": {}, "wishes": {},
            "notes": {}, "sessions": [{"date": "2026-10-27", "summary": "Historic summary"}],
        }
        self.path.write_text(json.dumps(original), encoding="utf-8")

        loaded = memory.load_memory()

        self.assertEqual(loaded["identity"]["name"]["value"], "Karim")
        self.assertEqual(loaded["sessions"][0]["summary"], "Historic summary")
        self.assertEqual(loaded["schema_version"], 2)
        memory.save_memory(loaded)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["identity"], original["identity"])

    def test_project_context_survives_without_classic_history(self):
        memory.update_project_state(
            "avatar_sarra", name="Avatar Sarra", state="audit",
            decisions=["use only local memory"], next_step="restore Live context",
        )
        project = memory.format_startup_context(memory.load_memory())
        prompt = memory.format_memory_for_prompt(memory.load_memory())

        self.assertIn("Avatar Sarra", project)
        self.assertIn("restore Live context", project)
        self.assertIn("use only local memory", prompt)
        self.assertIn("restore Live context", prompt)

    def test_frozen_store_reconciles_newer_source_without_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "memory" / "long_term.json"
            active = root / "exe" / "Sarra" / "memory" / "long_term.json"
            source.parent.mkdir(parents=True)
            active.parent.mkdir(parents=True)
            source.write_text(json.dumps({
                "identity": {}, "preferences": {}, "projects": {},
                "relationships": {}, "wishes": {}, "notes": {
                    "source_unique": {"value": "source fact", "updated": "2026-10-28"}
                }, "sessions": [], "conversation_history": [{
                    "id": "source-conversation", "turns": ["User: remember source"]
                }], "project_state": {
                    "active_project": "avatar_sarra",
                    "projects": {"avatar_sarra": {"name": "Avatar Sarra",
                                                   "next_step": "restore source project"}},
                }, "archive": {},
            }), encoding="utf-8")
            active.write_text(json.dumps({
                "identity": {}, "preferences": {}, "projects": {},
                "relationships": {}, "wishes": {}, "notes": {
                    "packaged_unique": {"value": "packaged fact", "updated": "2026-10-27"}
                }, "sessions": [], "conversation_history": [{
                    "id": "packaged-conversation", "turns": ["User: remember packaged"]
                }], "project_state": {
                    "active_project": "older_project",
                    "projects": {"older_project": {"name": "Older Project",
                                                   "next_step": "older step"}},
                }, "archive": {},
            }), encoding="utf-8")
            newer = 1_800_000_000
            os.utime(source, (newer, newer))
            os.utime(active, (newer - 100, newer - 100))

            with patch.object(memory, "MEMORY_PATH", active), patch.object(memory.sys, "frozen", True, create=True):
                restored = memory.load_memory()

            self.assertEqual(restored["project_state"]["active_project"], "avatar_sarra")
            self.assertIn("source_unique", restored["notes"])
            self.assertIn("packaged_unique", restored["notes"])
            ids = {item["id"] for item in restored["conversation_history"]}
            self.assertEqual(ids, {"source-conversation", "packaged-conversation"})
            backups = list(active.parent.glob("long_term.pre-migration-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertIn("packaged-conversation", backups[0].read_text(encoding="utf-8"))

    def test_frozen_store_does_not_overwrite_newer_packaged_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "memory" / "long_term.json"
            active = root / "exe" / "Sarra" / "memory" / "long_term.json"
            source.parent.mkdir(parents=True)
            active.parent.mkdir(parents=True)
            source.write_text(json.dumps(memory._empty_memory() | {
                "notes": {"winner": {"value": "old source", "updated": "2026-10-27"}}
            }), encoding="utf-8")
            active.write_text(json.dumps(memory._empty_memory() | {
                "notes": {"winner": {"value": "new packaged", "updated": "2026-10-28"}}
            }), encoding="utf-8")
            os.utime(source, (1_700_000_000, 1_700_000_000))
            os.utime(active, (1_800_000_000, 1_800_000_000))

            with patch.object(memory, "MEMORY_PATH", active), patch.object(memory.sys, "frozen", True, create=True):
                restored = memory.load_memory()

            self.assertEqual(restored["notes"]["winner"]["value"], "new packaged")

    def test_conversation_and_project_survive_reloads(self):
        entry = memory.save_conversation(["User: continue Sarra", "Sarra: We will test it."], "Test continuity")
        memory.update_project_state(
            "avatar_sarra", name="Avatar Sarra", state="tests en cours",
            completed_tasks=["audit memory"], in_progress_tasks=["persistent memory"],
            pending_tasks=["avatar tests"], problems=["voice interruption"],
            decisions=["only Space interrupts"], next_step="run mocked tests",
        )

        restored = memory.load_memory()
        context = memory.format_startup_context(restored)

        self.assertEqual(memory.save_conversation(["User: continue Sarra", "Sarra: We will test it."], "duplicate")["id"], entry["id"])
        self.assertIn("Avatar Sarra", context)
        self.assertIn("audit memory", context)
        self.assertIn("run mocked tests", context)
        self.assertIn("only Space interrupts", context)
        self.assertIn("Test continuity", context)

    def test_session_summary_is_not_consumed_and_is_deduplicated(self):
        memory.save_session_summary("We discussed memory continuity", "French")
        memory.save_session_summary("We discussed memory continuity", "French")

        self.assertEqual(len(memory.get_recent_sessions()), 1)
        self.assertEqual(memory.pop_last_session()["summary"], "We discussed memory continuity")
        self.assertEqual(memory.pop_last_session()["summary"], "We discussed memory continuity")

    def test_explicit_correction_and_deletion_are_supported(self):
        memory.update_project_state("avatar_sarra", next_step="old step")
        memory.update_project_state("avatar_sarra", next_step="corrected step")
        conversation = memory.save_conversation(["User: remember this", "Sarra: done"])

        self.assertTrue(memory.forget_conversation(conversation["id"]))
        self.assertEqual(memory.load_memory()["conversation_history"], [])
        self.assertTrue(memory.forget_project("avatar_sarra"))
        self.assertEqual(memory.load_memory()["project_state"]["projects"], {})

    def test_action_lifecycle_never_implies_completion(self):
        memory.update_project_state("avatar_sarra", active=True)
        memory.record_action_outcome("avatar_sarra", "run avatar test", "proposed", "test plan")
        state = memory.record_action_outcome("avatar_sarra", "run avatar test", "validated", "test plan")
        self.assertEqual(state["actions"][0]["status"], "validated")

        memory.record_action_outcome("avatar_sarra", "run avatar test", "executed", "test plan")
        memory.record_action_outcome("avatar_sarra", "run avatar test", "completed", "test plan")
        state = memory.load_memory()["project_state"]["projects"]["avatar_sarra"]
        self.assertEqual(state["actions"][0]["status"], "completed")

    def test_missing_memory_is_empty_and_corrupt_memory_is_quarantined(self):
        self.assertFalse(self.path.exists())
        self.assertEqual(memory.load_memory()["identity"], {})

        original = '{"identity": "corrupt"'
        self.path.write_text(original, encoding="utf-8")
        loaded = memory.load_memory()

        self.assertEqual(loaded["identity"], {})
        self.assertIsNotNone(memory.get_memory_load_error())
        quarantined = list(self.path.parent.glob("long_term.corrupt-*.json"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_text(encoding="utf-8"), original)

    def test_atomic_write_leaves_no_temp_file_and_archives_overflow(self):
        memory.save_conversation(["User: checkpoint", "Sarra: accepted"])
        self.assertEqual(list(self.path.parent.glob(".long_term.json.*.tmp")), [])

        with patch.object(memory, "MEMORY_MAX_CHARS", 120):
            memory.update_memory({"notes": {
                "old_entry": {"value": "old context is preserved"},
                "new_entry": {"value": "new context is preserved"},
            }})

        stored = memory.load_memory()
        archived = stored["archive"]["entries"]
        self.assertTrue(archived)
        self.assertIn("notes/old_entry", [entry.get("archived_from", "") for entry in archived])

    def test_secret_like_content_is_redacted_before_persistence(self):
        memory.save_conversation(["User: api_key=TOP_SECRET should not stay", "Sarra: understood"])
        stored = memory.load_memory()["conversation_history"][0]
        self.assertNotIn("TOP_SECRET", json.dumps(stored))
        self.assertIn("[REDACTED]", json.dumps(stored))


if __name__ == "__main__":
    unittest.main()
