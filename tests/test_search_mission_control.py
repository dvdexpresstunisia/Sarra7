import unittest
from unittest.mock import patch

import actions.browser_control as browser_control_module
import actions.web_search as web_search_module
from core.mission_control import (
    MissionController,
    canonical_tool_call,
    deduplicate_tool_calls,
)


class _ToolCall:
    def __init__(self, call_id, name, args):
        self.id = call_id
        self.name = name
        self.args = args


class SearchMissionControlTests(unittest.TestCase):
    def setUp(self):
        web_search_module._search_cache.clear()
        browser_control_module._nav_cache.clear()

    def test_identical_search_returns_cached_results_once(self):
        observed = [
            {
                "title": "Verified Hackathon",
                "snippet": "Real event source",
                "url": "https://example.test/hackathon",
            }
        ]
        with patch.object(web_search_module, "_ddg_search", return_value=observed) as search:
            first = web_search_module.web_search({"query": "current hackathons with prizes"})
            second = web_search_module.web_search({"query": "  current   hackathons with prizes "})

        self.assertIn("Verified Hackathon", first)
        self.assertIn("[CACHED VERIFIED SEARCH RESULT]", second)
        self.assertIn("https://example.test/hackathon", second)
        search.assert_called_once()

    def test_failed_search_is_reported_and_not_retried(self):
        with patch.object(web_search_module, "_ddg_search", return_value=[]), \
             patch.object(web_search_module, "_gemini_search", side_effect=AssertionError("must not fabricate")):
            first = web_search_module.web_search({"query": "private current events"})
            second = web_search_module.web_search({"query": "private current events"})

        self.assertTrue(first.startswith("Search failed:"))
        self.assertIn("aucune source", first)
        self.assertIn("[CACHED VERIFIED SEARCH RESULT]", second)

    def test_browser_identical_search_does_not_open_second_window(self):
        with patch.object(browser_control_module, "_open_native",
                          return_value="Opened: https://www.google.com/search?q=hackathons") as open_native:
            first = browser_control_module.browser_control({
                "action": "search", "query": "hackathons",
            })
            second = browser_control_module.browser_control({
                "action": "search", "query": "  HACKATHONS  ",
            })

        self.assertTrue(first.startswith("Opened:"))
        self.assertEqual(first, second)
        open_native.assert_called_once()

    def test_research_never_generates_without_observed_sources(self):
        with patch.object(web_search_module, "_ddg_search", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "aucune source"):
                web_search_module._research("current hackathons")

    def test_duplicate_calls_in_one_batch_are_executed_once(self):
        calls = [
            _ToolCall("a", "web_search", {"query": "hackathons", "mode": "search"}),
            _ToolCall("b", "web_search", {"mode": "search", "query": "hackathons"}),
        ]
        unique, duplicates = deduplicate_tool_calls(calls)

        self.assertEqual([call.id for call in unique], ["a"])
        self.assertEqual(len(next(iter(duplicates.values()))), 1)

    def test_mission_completion_requires_observed_url(self):
        controller = MissionController()
        key = canonical_tool_call("web_search", {"query": "hackathons"})

        running = controller.record(key, "web_search", "No results found")
        failed = controller.record(key, "web_search", "Search failed: backend unavailable")
        completed = controller.record(
            key,
            "web_search",
            "Source: https://example.test/event",
        )

        self.assertEqual(running.state, "running")
        self.assertEqual(failed.state, "failed")
        self.assertEqual(completed.state, "completed")
        self.assertEqual(completed.sources, ("https://example.test/event",))


if __name__ == "__main__":
    unittest.main()
