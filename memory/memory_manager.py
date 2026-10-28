import json
import re
import os
import tempfile
from datetime import datetime
from threading import Lock
from pathlib import Path
import sys


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
MEMORY_PATH      = BASE_DIR / "memory" / "long_term.json"
_lock            = Lock()


def get_persistent_memory_paths() -> tuple[Path, Path | None]:
    """Return the active store and, for frozen builds, its development source.

    Frozen builds write beside the executable.  Keeping the source checkout
    separate would make the two stores drift forever.  When a source checkout
    is demonstrably adjacent to a frozen layout, load_memory() explicitly
    reconciles it with the executable store instead of silently overwriting
    either side.
    """
    active = MEMORY_PATH
    if not getattr(sys, "frozen", False):
        return active, None
    source_root = active.parent.parent.parent.parent
    source = source_root / "memory" / "long_term.json"
    if source.resolve() == active.resolve():
        return active, None
    return active, source if source.is_file() else None
MAX_VALUE_LENGTH = 380

# ── Why there are two very different numbers here ────────────────────────────
#
# There used to be one: MEMORY_MAX_CHARS = 2200, applied to the whole store. It
# was a *storage* limit, and it existed only because the entire memory was
# pasted into the system prompt on every connect — so growing the memory grew
# every single request. When it filled, _trim_to_limit() deleted the oldest
# entries and printed one line to a console nobody reads. A memory described as
# "deeply remembers projects, preferences and personal context" was in practice
# two pages long, and quietly forgot your sister's name after a few weeks.
#
# Storage and prompt budget are now separate concerns:
#
#   MEMORY_MAX_CHARS  — a runaway guard, not a feature limit. Nothing normal
#                       reaches it; a bug writing in a loop does.
#   PROMPT_CORE_CHARS — what actually rides in the system prompt every session.
#                       Smaller than the old whole-memory dump, so sessions
#                       start *faster* than before, not slower.
#
# Everything above the core stays on disk and is fetched on demand by the
# recall_memory tool — see search_memory() and format_memory_for_prompt().
MEMORY_MAX_CHARS  = 200_000
PROMPT_CORE_CHARS = 900
PROMPT_INDEX_CHARS = 420
# Most entries any one category may contribute to the core block, so a person
# with forty stored preferences still gets their sister into the prompt.
PROMPT_MAX_PER_CATEGORY = 6

def _empty_memory() -> dict:
    return {
        "schema_version": 2,
        "identity":      {},
        "preferences":   {},
        "projects":      {},
        "relationships": {},
        "wishes":        {},
        "notes":         {},
        "sessions":      [],
        "conversation_history": [],
        "project_state": {
            "active_project": None,
            "projects": {},
        },
        "archive": {
            "entries": [],
            "sessions": [],
            "conversations": [],
        },
    }


class MemoryLoadError(RuntimeError):
    pass


_memory_load_error: MemoryLoadError | None = None


def get_memory_load_error() -> MemoryLoadError | None:
    return _memory_load_error


def _quarantine_corrupt_memory(error: Exception) -> None:
    """Preserve unreadable bytes instead of ever replacing them silently."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    quarantine = MEMORY_PATH.with_name(f"{MEMORY_PATH.stem}.corrupt-{stamp}{MEMORY_PATH.suffix}")
    try:
        # os.replace would destroy the corrupt source; copy2 preserves both.
        import shutil
        shutil.copy2(MEMORY_PATH, quarantine)
        print(f"[Memory] CORRUPT DATA PRESERVED at {quarantine}: {error}")
        return
    except Exception as copy_error:
        print(f"[Memory] CRITICAL: corrupt memory could not be quarantined: {copy_error}")


def _normalise_memory(data: dict) -> dict:
    base = _empty_memory()
    for key in ("identity", "preferences", "projects", "relationships", "wishes", "notes"):
        value = data.get(key)
        if not isinstance(value, dict):
            raise MemoryLoadError(f"memory section '{key}' is not an object")
        base[key] = value
    for key in ("sessions", "conversation_history"):
        value = data.get(key, [])
        if not isinstance(value, list):
            raise MemoryLoadError(f"memory section '{key}' is not a list")
        base[key] = value
    project_state = data.get("project_state", {})
    if not isinstance(project_state, dict):
        raise MemoryLoadError("memory section 'project_state' is not an object")
    base["project_state"] = {
        "active_project": project_state.get("active_project"),
        "projects": project_state.get("projects", {}) if isinstance(project_state.get("projects", {}), dict) else {},
    }
    archive = data.get("archive", {})
    if archive:
        if not isinstance(archive, dict):
            raise MemoryLoadError("memory section 'archive' is not an object")
        base["archive"] = {
            "entries": archive.get("entries", []) if isinstance(archive.get("entries", []), list) else [],
            "sessions": archive.get("sessions", []) if isinstance(archive.get("sessions", []), list) else [],
            "conversations": archive.get("conversations", []) if isinstance(archive.get("conversations", []), list) else [],
        }
    # Preserve unknown forward-compatible sections rather than silently discarding them.
    for key, value in data.items():
        if key not in base:
            base[key] = value
    base["schema_version"] = 2
    return base


def _entry_updated(entry: object) -> str:
    if isinstance(entry, dict):
        return str(entry.get("updated", "") or "")
    return ""


def _merge_entries(primary: dict, secondary: dict) -> dict:
    """Merge named entries, preferring the freshest entry and retaining both.

    Entry timestamps from the two stores are compared before the file is
    modified.  Untouched old entries are therefore preserved, while a unique
    key from either side is never silently discarded.
    """
    merged = {}
    for key in secondary:
        merged[key] = secondary[key]
    for key, value in primary.items():
        if key not in merged:
            merged[key] = value
            continue
        old = merged[key]
        if _entry_updated(value) >= _entry_updated(old):
            merged[key] = value
    return merged


def _merge_lists(primary: list, secondary: list, key_fields: tuple[str, ...]) -> list:
    merged = list(primary)
    seen = {json.dumps(item, sort_keys=True, ensure_ascii=False)
            for item in merged if isinstance(item, (dict, list))}
    for item in secondary:
        marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if marker not in seen:
            merged.append(item)
            seen.add(marker)
    return merged


def _merge_project_state(primary: dict, secondary: dict) -> dict:
    primary_projects = primary.get("projects", {}) if isinstance(primary, dict) else {}
    secondary_projects = secondary.get("projects", {}) if isinstance(secondary, dict) else {}
    projects = {}
    all_names = set(secondary_projects) | set(primary_projects)
    for name in all_names:
        if name not in primary_projects:
            projects[name] = secondary_projects[name]
            continue
        if name not in secondary_projects:
            projects[name] = primary_projects[name]
            continue
        one = primary_projects[name]
        two = secondary_projects[name]
        if not isinstance(one, dict):
            projects[name] = one
            continue
        if not isinstance(two, dict):
            projects[name] = two
            continue
        projects[name] = dict(two)
        projects[name].update(one)

    preferred = primary if isinstance(primary, dict) else secondary
    return {"active_project": preferred.get("active_project") if isinstance(preferred, dict) else None,
            "projects": projects}


def _merge_memory(primary: dict, secondary: dict) -> dict:
    merged = dict(secondary)
    merged.update(primary)
    for section in ("identity", "preferences", "projects", "relationships", "wishes", "notes"):
        one = primary.get(section, {}) if isinstance(primary.get(section, {}), dict) else {}
        two = secondary.get(section, {}) if isinstance(secondary.get(section, {}), dict) else {}
        merged[section] = _merge_entries(one, two)
    merged["sessions"] = _merge_lists(
        primary.get("sessions", []) if isinstance(primary.get("sessions", []), list) else [],
        secondary.get("sessions", []) if isinstance(secondary.get("sessions", []), list) else [],
        ("id",),
    )
    merged["conversation_history"] = _merge_lists(
        primary.get("conversation_history", []) if isinstance(primary.get("conversation_history", []), list) else [],
        secondary.get("conversation_history", []) if isinstance(secondary.get("conversation_history", []), list) else [],
        ("id",),
    )
    merged["project_state"] = _merge_project_state(
        primary.get("project_state", {}), secondary.get("project_state", {})
    )
    primary_archive = primary.get("archive", {}) if isinstance(primary.get("archive", {}), dict) else {}
    secondary_archive = secondary.get("archive", {}) if isinstance(secondary.get("archive", {}), dict) else {}
    merged["archive"] = {
        name: _merge_lists(
            primary_archive.get(name, []) if isinstance(primary_archive.get(name, []), list) else [],
            secondary_archive.get(name, []) if isinstance(secondary_archive.get(name, []), list) else [],
            ("id",),
        )
        for name in ("entries", "sessions", "conversations")
    }
    return merged


def _reconcile_frozen_memory() -> bool:
    """Merge the executable store with an adjacent source store exactly once.

    The executable store is authoritative only for an entry that is at least
    as recent as the source entry.  Unique data from both files remains.  A
    migration backup is written before the merged store is atomically saved.
    """
    active, source = get_persistent_memory_paths()
    if source is None:
        return False
    try:
        if not active.exists():
            source_data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(source_data, dict):
                return False
            active.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(active, _normalise_memory(source_data))
            print(f"[Memory] Migration complete: created executable store from source; path={active}")
            return True
        active_data = json.loads(active.read_text(encoding="utf-8"))
        source_data = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(active_data, dict) or not isinstance(source_data, dict):
            return False
        active_is_newer = active.stat().st_mtime_ns >= source.stat().st_mtime_ns
        primary, secondary = (active_data, source_data) if active_is_newer else (source_data, active_data)
        merged = _merge_memory(primary, secondary)
        merged = _normalise_memory(merged)
        if merged == active_data:
            return False
        import shutil
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = active.with_name(f"{active.stem}.pre-migration-{stamp}{active.suffix}")
        if not backup.exists():
            shutil.copy2(active, backup)
        _atomic_write_json(active, merged)
        print(f"[Memory] Migration complete: merged executable and source stores; backup={backup.name}")
        return True
    except Exception as exc:
        print(f"[Memory] Migration blocked safely: {exc}")
        return False


def load_memory() -> dict:
    global _memory_load_error
    if not MEMORY_PATH.exists():
        _memory_load_error = None
        return _empty_memory()
    with _lock:
        _reconcile_frozen_memory()
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise MemoryLoadError("memory root is not an object")
            memory = _normalise_memory(data)
            _memory_load_error = None
            return memory
        except Exception as e:
            error = e if isinstance(e, MemoryLoadError) else MemoryLoadError(str(e))
            _memory_load_error = error
            _quarantine_corrupt_memory(error)
            return _empty_memory()

def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((cat, key, entry))
    return entries


# Set by main.py so a trim can reach the activity log. Deleting something a
# person told you and mentioning it only on stdout is how a memory loses trust.
_trim_notifier = None


def set_trim_notifier(fn) -> None:
    """Register a callable(str) that surfaces trims to the user."""
    global _trim_notifier
    _trim_notifier = fn


def _archive_oversized_memory(memory: dict) -> list[str]:
    """Move, never delete, oldest facts if the explicit storage guard is hit."""
    if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
        return []
    archive = memory.setdefault("archive", {})
    if not isinstance(archive, dict):
        archive = {"entries": [], "sessions": []}
        memory["archive"] = archive
    archived_entries = archive.setdefault("entries", [])
    if not isinstance(archived_entries, list):
        archived_entries = []
        archive["entries"] = archived_entries
    moved: list[str] = []
    entries = _all_entries(memory)
    entries.sort(key=lambda item: item[2].get("updated", "0000-00-00"))
    for cat, key, entry in entries:
        if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
            break
        copied = {**entry, "archived_from": f"{cat}/{key}", "archived_at": datetime.now().isoformat()}
        archived_entries.append(copied)
        del memory[cat][key]
        moved.append(f"{cat}/{key}")
    if moved:
        message = f"SYS: Memory capacity reached — archived {len(moved)} oldest entries: {', '.join(moved[:3])}"
        print(f"[Memory] 📦 {message}")
        if _trim_notifier:
            try:
                _trim_notifier(message)
            except Exception:
                pass
    return moved


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def save_memory(memory: dict) -> None:
    if not isinstance(memory, dict):
        raise TypeError("memory must be an object")
    memory = _normalise_memory(memory)
    _archive_oversized_memory(memory)
    with _lock:
        _atomic_write_json(MEMORY_PATH, memory)


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _recursive_update(target: dict, updates: dict) -> bool:
    changed = False
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
        else:
            new_val  = _truncate_value(str(value["value"] if isinstance(value, dict) else value))
            entry    = {"value": new_val, "updated": datetime.now().strftime("%Y-%m-%d")}
            existing = target.get(key, {})
            if not isinstance(existing, dict) or existing.get("value") != new_val:
                target[key] = entry
                changed = True
    return changed


def update_memory(memory_update: dict) -> dict:
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()
    memory = load_memory()
    if _recursive_update(memory, memory_update):
        save_memory(memory)
        print(f"[Memory] 💾 Saved: {list(memory_update.keys())}")
    return memory

def _entry_value(entry) -> str:
    """Accept both the {'value': ..., 'updated': ...} shape and a bare string,
    because early versions of the store wrote plain strings."""
    if isinstance(entry, dict):
        return str(entry.get("value", "") or "").strip()
    return str(entry or "").strip()


def _pretty(key: str) -> str:
    return key.replace("_", " ").strip()


# Identity is always in the prompt; these categories compete for the remaining
# budget by recency.
_CATEGORY_LABELS = {
    "preferences":   "Preferences",
    "projects":      "Active projects / goals",
    "relationships": "People in their life",
    "wishes":        "Wishes / plans",
    "notes":         "Notes",
}

_IDENTITY_FIELDS = ["name", "age", "birthday", "city", "job",
                    "language", "school", "nationality"]


def format_memory_for_prompt(memory: dict | None) -> str:
    """Build the memory block that goes into the system prompt.

    This used to dump everything. It now sends three things:

      1. IDENTITY  - always, in full. It is small, and it is wrong for the
         assistant to have to look up your name.
      2. RECENT    - the most recently updated entries from every other
         category, up to PROMPT_CORE_CHARS. Recency is the cheapest useful
         relevance signal available without embeddings.
      3. AN INDEX  - the *keys* of everything else, values omitted.

    Point 3 is what makes recall work at all. A model cannot decide to look
    something up if it does not know the thing exists: with only points 1 and 2,
    "who is Ayse?" would get "I don't know" while ayse_sister sat on disk
    unread. The index costs a few hundred characters and turns recall from a
    gamble into a lookup.

    Net effect on latency: this block is SMALLER than the old full dump, so
    every session connects with fewer tokens. Occasionally the model spends one
    extra round trip on recall_memory - covered by the acknowledgment it
    already speaks before any slow step."""
    if not memory:
        return ""

    core_lines: list[str] = []

    # 1. Identity - always, in full
    identity = memory.get("identity", {}) or {}
    for field in _IDENTITY_FIELDS:
        val = _entry_value(identity.get(field))
        if not val:
            continue
        if field == "language":
            # Labelled as an observation, not a setting. A bare "Language:
            # English" line written months ago reads like a standing order and
            # was one of the reasons a Turkish question came back in English.
            core_lines.append(
                f"Has spoken to you in: {val} (an observation about the past — "
                f"always answer in the language of their CURRENT message)")
        else:
            core_lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in _IDENTITY_FIELDS:
            continue
        val = _entry_value(entry)
        if val:
            core_lines.append(f"{_pretty(key).title()}: {val}")

    # 2. Everything else, most recently updated first
    rest: list[tuple[str, str, str, str]] = []   # (updated, cat, key, value)
    for cat in _CATEGORY_LABELS:
        for key, entry in (memory.get(cat, {}) or {}).items():
            val = _entry_value(entry)
            if not val:
                continue
            updated = (entry.get("updated", "") if isinstance(entry, dict) else "") or "0000-00-00"
            rest.append((updated, cat, key, val))
    rest.sort(key=lambda t: t[0], reverse=True)

    used    = sum(len(l) + 1 for l in core_lines)
    shown: dict[str, list[str]] = {}
    overflow: dict[str, list[str]] = {}

    # Recency decides order, but no single category may take the whole budget.
    # Without the cap, someone with forty stored preferences gets a prompt that
    # is forty preferences and not one person's name — the categories that
    # matter most in conversation are also the ones that change least often, so
    # pure recency systematically buries them.
    per_cat_used: dict[str, int] = {}
    for _updated, cat, key, val in rest:
        line = f"  - {_pretty(key).title()}: {val}"
        if (per_cat_used.get(cat, 0) < PROMPT_MAX_PER_CATEGORY
                and used + len(line) + 1 <= PROMPT_CORE_CHARS):
            shown.setdefault(cat, []).append(line)
            per_cat_used[cat] = per_cat_used.get(cat, 0) + 1
            used += len(line) + 1
        else:
            overflow.setdefault(cat, []).append(_pretty(key))

    # The index is a table of contents, so it is interleaved across categories
    # rather than continuing in recency order. Sorted by recency it would list
    # twenty-four preferences before the first relationship, and the one entry
    # the index exists for — the old fact the model has no other way to know
    # about — would fall off the end.
    indexed: list[str] = []
    if overflow:
        cats  = [c for c in _CATEGORY_LABELS if overflow.get(c)]
        cursor = {c: 0 for c in cats}
        while cats:
            for cat in list(cats):
                i = cursor[cat]
                if i >= len(overflow[cat]):
                    cats.remove(cat)
                    continue
                indexed.append(overflow[cat][i])
                cursor[cat] = i + 1

    for cat, label in _CATEGORY_LABELS.items():
        if shown.get(cat):
            core_lines.append("")
            core_lines.append(f"{label}:")
            core_lines.extend(shown[cat])

    continuity = format_startup_context(memory, conversation_limit=2)
    if not core_lines and not indexed:
        if not continuity:
            return ""
        out = ["[CONTINUITY FROM PREVIOUS SESSIONS]", continuity]
    else:
        out = [
            "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]",
            *core_lines,
        ]

    # 3. The index of what is on disk but not in this prompt
    if indexed:
        budget, names = PROMPT_INDEX_CHARS, []
        for n in indexed:
            if budget - len(n) - 2 < 0:
                break
            names.append(n)
            budget -= len(n) + 2
        if names:
            out.append("")
            out.append(
                "[ALSO REMEMBERED — values not shown here. Call recall_memory "
                "with a keyword to read any of these before saying you do not know]"
            )
            out.append(", ".join(names)
                       + (f" (+{len(indexed) - len(names)} more)"
                          if len(indexed) > len(names) else ""))

    if continuity:
        out.extend(("", continuity))
    return "\n".join(out) + "\n"


# ── Recall ────────────────────────────────────────────────────────────────────

def _score(query_words: list[str], cat: str, key: str, value: str) -> int:
    """Cheap lexical relevance. No embeddings, no network, no model call - this
    runs in well under a millisecond, which is the entire point: recall must
    cost one model round trip, never two."""
    hay_key = _pretty(key).lower()
    hay_val = value.lower()
    score   = 0
    for w in query_words:
        if not w:
            continue
        if w == hay_key:
            score += 10
        elif w in hay_key:
            score += 6
        if w in hay_val:
            score += 3
        if w in cat:
            score += 1
    return score


def search_memory(query: str, limit: int = 8) -> str:
    """Find stored facts matching `query`. Backs the recall_memory tool.

    An empty query is treated as "show me everything you know", capped - the
    model asks that when the user says "what do you remember about me?"."""
    memory = load_memory()
    words  = [w for w in re.split(r"[^\w]+", (query or "").lower()) if len(w) > 1]

    rows: list[tuple[int, str, str, str]] = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue                     # skip 'sessions', which is a list
        for key, entry in items.items():
            val = _entry_value(entry)
            if not val:
                continue
            s = _score(words, cat, key, val) if words else 1
            if s > 0:
                rows.append((s, cat, key, val))

    if not rows:
        return (f"Nothing stored about '{query}'." if query
                else "I have not stored anything about this person yet.")

    rows.sort(key=lambda r: (-r[0], r[2]))
    lines = [f"{cat}/{_pretty(key)}: {val}" for _s, cat, key, val in rows[:max(1, limit)]]
    head  = (f"Stored facts matching '{query}':" if query
             else "Everything currently stored:")
    more  = (f"\n(+{len(rows) - len(lines)} more — search with a narrower keyword)"
             if len(rows) > len(lines) else "")
    return head + "\n" + "\n".join(lines) + more


def all_entries_for_ui() -> list[dict]:
    """Flat list for the memory panel: what JARVIS knows, and when it learned it.
    Sorted newest first so the panel opens on what changed most recently."""
    memory = load_memory()
    rows = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            val = _entry_value(entry)
            if not val:
                continue
            rows.append({
                "category": cat,
                "key":      key,
                "value":    val,
                "updated":  (entry.get("updated", "") if isinstance(entry, dict) else ""),
            })
    rows.sort(key=lambda r: (r["updated"] or "0000-00-00"), reverse=True)
    return rows

def remember(key: str, value: str, category: str = "notes") -> str:
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    memory = load_memory()
    cat    = memory.get(category, {})
    if key in cat:
        del cat[key]
        memory[category] = cat
        save_memory(memory)
        return f"Forgotten: {category}/{key}"
    return f"Not found: {category}/{key}"


forget_memory = forget


# ── Persistent conversation and project memory ───────────────────────────────

_SESSION_MAX = 20
_CONVERSATION_MAX = 50
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_ -]?key|password|secret|token)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)(AIza[0-9A-Za-z_-]{20,}|gh[pousr]_[0-9A-Za-z]{20,})"),
)


def _redact_sensitive_text(text: str) -> str:
    result = text or ""
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def _deduplicate_dict_list(items: list, key: str) -> list:
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        marker = str(item.get(key, ""))
        if marker and marker in seen:
            continue
        if marker:
            seen.add(marker)
        out.append(item)
    return out


def save_session_summary(summary: str, language: str = "") -> None:
    """Append a reusable, non-destructive session summary."""
    summary = _redact_sensitive_text((summary or "").strip())
    if not summary:
        return
    memory = load_memory()
    sessions = memory.get("sessions", [])
    entry = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "summary": summary[:280],
        "id": datetime.now().strftime("%Y%m%d-%H%M%S-%f"),
    }
    if language:
        entry["language"] = language
    if any(str(item.get("summary", "")).strip() == summary for item in sessions if isinstance(item, dict)):
        return
    sessions.append(entry)
    sessions = _deduplicate_dict_list(sessions, "id")
    if len(sessions) > _SESSION_MAX:
        archive = memory.setdefault("archive", {}).setdefault("sessions", [])
        archive.extend(sessions[:-_SESSION_MAX])
        sessions = sessions[-_SESSION_MAX:]
    memory["sessions"] = sessions
    save_memory(memory)
    print(f"[Memory] 📝 Session saved ({entry['date']}): {summary[:60]}…")


def save_conversation(turns: list[str], summary: str = "", language: str = "") -> dict | None:
    """Persist exact conversation turns (sanitised) and an optional summary."""
    clean_turns = [_redact_sensitive_text(str(turn).strip()) for turn in (turns or []) if str(turn).strip()]
    if not clean_turns:
        return None
    now = datetime.now()
    memory = load_memory()
    history = memory.get("conversation_history", [])
    signature = json.dumps(clean_turns[-40:], ensure_ascii=False)
    existing = next(
        (item for item in history if isinstance(item, dict) and item.get("turn_signature") == signature),
        None,
    )
    if existing:
        return existing
    entry = {
        "id": now.strftime("%Y%m%d-%H%M%S-%f"),
        "date": now.strftime("%Y-%m-%d"),
        "turns": clean_turns[-40:],
        "turn_signature": signature,
        "summary": _redact_sensitive_text(summary or "")[:280],
    }
    if language:
        entry["language"] = language
    history.append(entry)
    if len(history) > _CONVERSATION_MAX:
        archive = memory.setdefault("archive", {}).setdefault("conversations", [])
        if not isinstance(archive, list):
            archive = []
            memory["archive"]["conversations"] = archive
        archive.extend(history[:-_CONVERSATION_MAX])
        history = history[-_CONVERSATION_MAX:]
    memory["conversation_history"] = history
    save_memory(memory)
    return entry


def get_recent_sessions(limit: int = 3) -> list[dict]:
    memory = load_memory()
    sessions = [item for item in memory.get("sessions", []) if isinstance(item, dict)]
    return sessions[-max(1, limit):][::-1]


def get_recent_conversations(limit: int = 3) -> list[dict]:
    memory = load_memory()
    history = [item for item in memory.get("conversation_history", []) if isinstance(item, dict)]
    return history[-max(1, limit):][::-1]


def pop_last_session() -> dict | None:
    """Compatibility API: returns the newest summary without consuming it."""
    sessions = get_recent_sessions(1)
    return sessions[0] if sessions else None


def forget_session(session_id: str) -> bool:
    memory = load_memory()
    sessions = memory.get("sessions", [])
    retained = [item for item in sessions if item.get("id") != session_id]
    if len(retained) == len(sessions):
        return False
    memory["sessions"] = retained
    save_memory(memory)
    return True


def forget_conversation(conversation_id: str) -> bool:
    memory = load_memory()
    history = memory.get("conversation_history", [])
    retained = [item for item in history if item.get("id") != conversation_id]
    if len(retained) == len(history):
        return False
    memory["conversation_history"] = retained
    save_memory(memory)
    return True


def forget_project(project: str) -> bool:
    memory = load_memory()
    state = memory.get("project_state", {})
    projects = state.get("projects", {})
    if project not in projects:
        return False
    del projects[project]
    if state.get("active_project") == project:
        state["active_project"] = None
    save_memory(memory)
    return True


def format_startup_context(memory: dict | None = None, conversation_limit: int = 2) -> str:
    """Build a bounded, faithful continuity block for a new session."""
    memory = memory if isinstance(memory, dict) else load_memory()
    lines: list[str] = []
    state = memory.get("project_state", {})
    active = state.get("active_project") if isinstance(state, dict) else None
    projects = state.get("projects", {}) if isinstance(state, dict) else {}
    project = projects.get(active, {}) if isinstance(projects, dict) and active else {}
    if project:
        lines.append("[ACTIVE PROJECT CONTINUITY]")
        lines.append(f"Project: {project.get('name', active)}")
        for field, label in (
            ("state", "State"), ("objectives", "Objectives"), ("problems", "Problems"),
            ("decisions", "Decisions"), ("next_step", "Next step"),
        ):
            value = project.get(field)
            if not value:
                continue
            if isinstance(value, list):
                value = "; ".join(str(item) for item in value[-5:])
            lines.append(f"{label}: {value}")
        tasks = project.get("tasks", {})
        if isinstance(tasks, dict):
            for status in ("completed", "in_progress", "pending"):
                values = tasks.get(status, [])
                if values:
                    lines.append(f"Tasks {status}: {'; '.join(str(item) for item in values[-8:])}")
    conversations = get_recent_conversations(conversation_limit)
    if conversations:
        lines.append("[RECENT CONVERSATION CONTEXT]")
        for conversation in conversations:
            summary = str(conversation.get("summary", "")).strip()
            lines.append(summary or " | ".join(str(turn) for turn in conversation.get("turns", [])[-2:]))
    return "\n".join(lines)


def update_project_state(
    project: str, *, name: str = "", state: str = "", next_step: str = "",
    objectives: list[str] | None = None, completed_tasks: list[str] | None = None,
    in_progress_tasks: list[str] | None = None, pending_tasks: list[str] | None = None,
    problems: list[str] | None = None, attempted_solutions: list[str] | None = None,
    decisions: list[str] | None = None, active: bool = True,
) -> dict:
    """Update project continuity without inferring or overwriting status."""
    project = (project or "").strip()
    if not project:
        raise ValueError("project is required")
    memory = load_memory()
    root = memory.setdefault("project_state", {})
    projects = root.setdefault("projects", {})
    current = projects.setdefault(project, {"name": name or project, "tasks": {}})
    if name:
        current["name"] = name
    if state:
        current["state"] = state
    if next_step:
        current["next_step"] = next_step
    for field, values in (
        ("objectives", objectives), ("problems", problems),
        ("attempted_solutions", attempted_solutions), ("decisions", decisions),
    ):
        if values:
            current[field] = list(dict.fromkeys(str(value) for value in values))
    tasks = current.setdefault("tasks", {})
    for status, values in (
        ("completed", completed_tasks), ("in_progress", in_progress_tasks),
        ("pending", pending_tasks),
    ):
        if values:
            tasks[status] = list(dict.fromkeys(str(value) for value in values))
    current["updated"] = datetime.now().isoformat()
    if active:
        root["active_project"] = project
    save_memory(memory)
    return current


def record_action_outcome(project: str, action: str, status: str, evidence: str = "") -> dict:
    """Record proposed/validated/executed/completed action lifecycle exactly."""
    status = status.strip().lower()
    if status not in {"proposed", "validated", "executed", "completed"}:
        raise ValueError("status must be proposed, validated, executed, or completed")
    memory = load_memory()
    projects = memory.setdefault("project_state", {}).setdefault("projects", {})
    project_entry = projects.setdefault(project, {"name": project, "tasks": {}})
    actions = project_entry.setdefault("actions", [])
    matching = next((item for item in actions
                     if isinstance(item, dict) and item.get("action") == action
                     and item.get("evidence") == evidence), None)
    if matching:
        matching["status"] = status
        matching["updated"] = datetime.now().isoformat()
    else:
        actions.append({"action": action, "status": status, "evidence": evidence,
                        "updated": datetime.now().isoformat()})
    save_memory(memory)
    return project_entry