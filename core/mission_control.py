from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Iterable

_URL_RE = re.compile(r"https?://[^\s)>\]`]+", re.IGNORECASE)
_SEARCH_TOOLS = {"web_search"}


def canonical_tool_call(name: str, args: dict) -> str:
    return json.dumps([name, args], sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def deduplicate_tool_calls(calls: Iterable[object]) -> tuple[list[object], dict[str, list[object]]]:
    """Return unique calls and duplicate ids, preserving Gemini's call order."""
    unique: list[object] = []
    duplicates: dict[str, list[object]] = {}
    seen: dict[str, object] = {}
    for call in calls:
        key = canonical_tool_call(call.name, dict(call.args or {}))
        if key in seen:
            duplicates.setdefault(key, []).append(call)
            continue
        seen[key] = call
        unique.append(call)
    return unique, duplicates


@dataclass
class MissionResult:
    state: str
    sources: tuple[str, ...]
    result: str


class MissionController:
    """Tracks observed tool evidence so reports cannot claim unverified completion."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._missions: dict[str, MissionResult] = {}

    def record(self, key: str, tool_name: str, result: str) -> MissionResult:
        text = str(result)
        failed = text.lower().startswith("search failed:") or "aucune source" in text.lower()
        sources = tuple(dict.fromkeys(_URL_RE.findall(text))) if tool_name in _SEARCH_TOOLS else ()
        state = "failed" if failed else ("completed" if sources else "running")
        mission = MissionResult(state, sources, text)
        with self._lock:
            previous = self._missions.get(key)
            if previous and previous.state == "completed":
                all_sources = tuple(dict.fromkeys(previous.sources + sources))
                mission = MissionResult("completed", all_sources, text)
            self._missions[key] = mission
        return mission

    def snapshot(self, key: str) -> MissionResult | None:
        with self._lock:
            current = self._missions.get(key)
            if current is None:
                return None
            return MissionResult(current.state, current.sources, current.result)
