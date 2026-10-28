"""
core/confirm.py — a confirmation the model cannot forge.

THE PROBLEM WITH THE OLD GATE
    computer_settings guarded shutdown and restart like this:

        confirmed = str(params.get("confirmed", "")).lower()
        if confirmed not in ("yes", "true", "1", "confirm"):
            return "Please confirm by calling again with confirmed=yes."

    `confirmed` is a tool parameter, which means the *model* writes it. Nothing
    stops it from sending confirmed=yes on the first call, and nothing checks
    that a human was ever involved. It is a convention, not a gate — and its
    coverage was two actions, so deleting files and switching off the WiFi the
    assistant is talking over went through with no gate at all.

THE DESIGN HERE
    The confirmation token is issued by the *interface*, never by the model:

      1. An action calls `request(...)` with a callable that does the real work.
      2. This module hands the UI a banner with CONFIRM / CANCEL and returns
         IMMEDIATELY with a sentence for the model to say out loud.
      3. If — and only if — the user presses CONFIRM, the UI calls `resolve()`,
         which runs the stored callable off the Qt thread.

    Nothing blocks. The model keeps talking while the banner is up, so this
    costs no latency at all; in fact it is cheaper than the old gate, which
    burned two tool round trips (reject, then re-call) on every shutdown.

WHAT BELONGS HERE AND WHAT DOES NOT
    Every action or plugin call that can change the system, an application, a
    file, or the environment is parked here before its handler runs. Read-only
    tools stay outside this gate. Local callers must not execute a handler
    directly: dispatch is the execution boundary and the UI is the only source
    of acceptance.
"""

from __future__ import annotations

import threading
import time
import re
import unicodedata
import uuid
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Callable, Optional

# A pending confirmation is abandoned after this long. Chosen to outlast a
# normal "hang on, let me look at the screen" pause without leaving a live
# shutdown button sitting on the HUD for the rest of the day.
TIMEOUT_SECONDS = 90.0
MISSION_TIMEOUT_SECONDS = 30 * 60


class ConfirmationState(str, Enum):
    IDLE = "IDLE"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class VoiceDecision(str, Enum):
    NO_PENDING = "NO_PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    AMBIGUOUS = "AMBIGUOUS"


class _Answer(str, Enum):
    YES = "YES"
    NO = "NO"
    CANCEL = "CANCEL"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass
class _Pending:
    confirmation_id: str
    action_id: str
    key: str
    title: str
    detail: str
    run: Callable[[], str]
    at: float
    mission_id: str | None
    context: dict


@dataclass
class _Mission:
    mission_id: str
    description: str
    at: float
    grants: frozenset[str]


_pending: Optional[_Pending] = None
_lock = threading.Lock()
_state = ConfirmationState.IDLE
_mission: _Mission | None = None
_clarification_required = False
_voice_replay: tuple[str, float] | None = None

# Set once at startup by main.py. Signature: (id, title, detail) -> None for show,
# and () -> None for hide. Both are marshalled onto the Qt thread by the UI.
_show_cb: Optional[Callable[[str, str], None]] = None
_hide_cb: Optional[Callable[[], None]] = None
_log_cb:  Optional[Callable[[str], None]] = None


def bind(show, hide, log=None) -> None:
    """Wire this module to the HUD. Called once from main.py at startup."""
    global _show_cb, _hide_cb, _log_cb
    _show_cb, _hide_cb, _log_cb = show, hide, log


def _log(msg: str) -> None:
    if _log_cb:
        try:
            _log_cb(msg)
        except Exception:
            pass


def _notify_expired(pending: _Pending | None) -> None:
    if pending is None:
        return
    if _hide_cb:
        try:
            _hide_cb()
        except Exception:
            pass
    _log(f"SYS: Confirmation expired — {pending.title}")


def _fingerprint(key: str, parameters: object) -> str:
    try:
        encoded = json.dumps(parameters, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), default=str)
    except Exception:
        encoded = repr(parameters)
    return f"{key}:{encoded}"


def guard(name: str, parameters: object, title: str, detail: str,
          run: Callable[[], str]) -> str:
    """Execution boundary: bind exactly one tool call to one human approval."""
    key = _fingerprint(name, parameters)
    return request(key, title, detail, run,
                   context={"action": name, "parameters": parameters})


def request(key: str, title: str, detail: str, run: Callable[[], str],
            *, context: dict | None = None) -> str:
    """Park an irreversible action behind the on-screen gate.

    Returns the sentence the tool should hand back to the model — phrased as an
    instruction so the assistant asks the user out loud in their own language,
    rather than reading an English string verbatim."""
    global _pending, _state, _clarification_required

    if _show_cb is None:
        # No interface bound (headless, or a very early call). Refuse rather
        # than silently performing something irreversible.
        return (f"I cannot confirm '{title}' right now because the interface is "
                f"not available, so I have not done it.")

    with _lock:
        expired = _expire_pending_locked()
        if _pending is not None:
            return (
                f"[CONFIRMATION_PENDING] '{_pending.title}' is already waiting "
                f"for the user's answer. Do not call another action; ask the "
                f"user to confirm or cancel that one first."
            )
        mission_id = _active_mission_id_locked()
        confirmation_id = uuid.uuid4().hex
        action_id = _fingerprint(key, {"context": context or {}, "mission": mission_id})
        _pending = _Pending(
            confirmation_id=confirmation_id,
            action_id=action_id,
            key=key,
            title=title,
            detail=detail,
            run=run,
            at=time.monotonic(),
            mission_id=mission_id,
            context=dict(context or {}),
        )
        _state = ConfirmationState.WAITING_CONFIRMATION

    _notify_expired(expired)

    try:
        _show_cb(confirmation_id, title, detail)
    except Exception as e:
        with _lock:
            _pending = None
            _state = ConfirmationState.IDLE
            _clarification_required = False
        return f"Could not ask for confirmation: {e}. Nothing was done."

    _log(f"SYS: Awaiting confirmation — {title}")
    return (
        f"[CONFIRMATION_PENDING] I have put a confirmation on screen for: {title}. "
        f"Ask the user to confirm this exact action by voice or on the HUD. "
        f"Do not call another action or claim it is done while waiting."
    )


def resolve(accepted: bool, confirmation_id: str | None = None,
            *, rejected: bool = False) -> bool:
    """Called by the UI when the user presses CONFIRM or CANCEL.

    Runs the stored callable on a worker thread — this is invoked from the Qt
    thread, and shutting the machine down from inside a button handler would
    freeze the interface on its way out."""
    global _pending, _state, _clarification_required, _voice_replay

    invalid = False
    with _lock:
        expired = _expire_pending_locked()
        p = _pending
        if p is None or (confirmation_id is not None
                         and p.confirmation_id != confirmation_id):
            invalid = True
        else:
            _pending = None
            _clarification_required = False
            _state = (ConfirmationState.CONFIRMED if accepted else
                      ConfirmationState.REJECTED if rejected else
                      ConfirmationState.CANCELLED)
            if accepted and confirmation_id is not None:
                _voice_replay = (p.key, time.monotonic() + TIMEOUT_SECONDS)

    if invalid:
        _notify_expired(expired)
        return False

    if _hide_cb:
        try:
            _hide_cb()
        except Exception:
            pass

    if not accepted:
        label = "Rejected" if rejected else "Cancelled"
        _log(f"SYS: {label} — {p.title}")
        return True

    def _worker():
        try:
            result = p.run() or "Done."
            _log(f"SYS: Confirmed — {p.title}. {result}")
        except Exception as e:
            _log(f"ERR: {p.title} failed — {e}")

    threading.Thread(target=_worker, daemon=True,
                     name=f"confirm-{p.key}").start()
    return True


def pending_title() -> str:
    """'' when nothing is waiting. Lets an action avoid stacking two banners."""
    with _lock:
        expired = _expire_pending_locked()
        title = _pending.title if _pending is not None else ""
    _notify_expired(expired)
    return title


def state() -> ConfirmationState:
    with _lock:
        expired = _expire_pending_locked()
        current = _state
    _notify_expired(expired)
    return current


def pending_id() -> str:
    with _lock:
        expired = _expire_pending_locked()
        identifier = _pending.confirmation_id if _pending is not None else ""
    _notify_expired(expired)
    return identifier


def _expire_pending_locked() -> _Pending | None:
    global _pending, _state, _clarification_required
    if _pending is None or time.monotonic() - _pending.at <= TIMEOUT_SECONDS:
        return None
    expired, _pending = _pending, None
    _state = ConfirmationState.EXPIRED
    _clarification_required = False
    return expired


def _active_mission_id_locked() -> str | None:
    global _mission
    if _mission is not None and time.monotonic() - _mission.at > MISSION_TIMEOUT_SECONDS:
        _mission = None
    return _mission.mission_id if _mission is not None else None


def _normalize(text: object) -> str:
    raw = unicodedata.normalize("NFKD", str(text or "").casefold())
    plain = "".join(char for char in raw if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", plain))


def _search_categories(text: object) -> set[str]:
    words = set(_normalize(text).split())
    categories = set()
    event_terms = {
        "hackathon", "hackathons", "concours", "competition", "competitions",
        "contest", "contests", "challenge", "challenges", "prix", "prize",
        "prizes", "award", "awards", "recompense", "recompenses",
    }
    ai_terms = {"ia", "ai", "intelligence", "artificielle", "artificial"}
    access_terms = {
        "gratuit", "gratuite", "gratuites", "free", "essai", "essais",
        "trial", "trials", "beta", "bete", "early", "access", "testeur",
    }
    if words & event_terms:
        categories.add("events")
    if words & ai_terms and words & access_terms:
        categories.add("ai_trials")
    return categories


def _classify_answer(text: object) -> _Answer:
    normalized = _normalize(text)
    affirmative = {
        "oui", "oui vas y", "oui va y", "vas y", "d accord", "d accord vas y",
        "ok", "okay", "je confirme", "je confirme vas y", "oui je confirme",
        "yes", "yes go ahead", "confirm",
    }
    cancellation = {"annule", "annuler", "attends", "attend", "stop", "cancel", "wait"}
    refusal = {"non", "non merci", "je refuse", "no", "no thanks", "not now"}
    words = set(normalized.split())
    if normalized in cancellation or words & {"annule", "annuler", "attends", "attend", "stop", "cancel", "wait"}:
        return _Answer.CANCEL
    if normalized in refusal or (normalized.startswith("non ") and words <= {"non", "attends", "attend", "stop"}):
        return _Answer.NO
    if normalized in affirmative:
        return _Answer.YES
    return _Answer.AMBIGUOUS


def handle_voice_response(text: str) -> VoiceDecision:
    """Resolve only the action currently waiting; speech alone never creates consent."""
    global _clarification_required
    with _lock:
        expired = _expire_pending_locked()
        pending = _pending
        if pending is None:
            decision = VoiceDecision.NO_PENDING
        else:
            answer = _classify_answer(text)
            if answer is _Answer.AMBIGUOUS:
                _clarification_required = True
                decision = VoiceDecision.AMBIGUOUS
            else:
                _clarification_required = False
                decision = answer
                confirmation_id = pending.confirmation_id
    _notify_expired(expired)
    if pending is None or decision is VoiceDecision.AMBIGUOUS:
        return decision
    if decision is _Answer.YES:
        return (VoiceDecision.CONFIRMED if resolve(True, confirmation_id)
                else VoiceDecision.NO_PENDING)
    if decision is _Answer.NO:
        return (VoiceDecision.REJECTED if resolve(
            False, confirmation_id, rejected=True
        ) else VoiceDecision.NO_PENDING)
    return (VoiceDecision.CANCELLED if cancel(
        "voice response", confirmation_id=confirmation_id
    ) else VoiceDecision.NO_PENDING)


def is_confirmation_phrase(text: str) -> bool:
    return _classify_answer(text) is not _Answer.AMBIGUOUS


def cancel(reason: str = "interrupted", confirmation_id: str | None = None) -> bool:
    """Cancel the current pending action, optionally requiring its exact id."""
    global _pending, _state, _clarification_required
    with _lock:
        expired = _expire_pending_locked()
        p = _pending
        if p is None or (confirmation_id is not None
                         and p.confirmation_id != confirmation_id):
            _notify_expired(expired)
            return False
        _pending = None
        _state = ConfirmationState.CANCELLED
        _clarification_required = False
    if _hide_cb:
        try:
            _hide_cb()
        except Exception:
            pass
    _log(f"SYS: Confirmation cancelled — {reason} — {p.title}")
    return True


def clarification_required() -> bool:
    with _lock:
        expired = _expire_pending_locked()
        required = _pending is not None and _clarification_required
    if expired is not None:
        _log(f"SYS: Confirmation expired — {expired.title}")
    return required


def consume_voice_replay(name: str, parameters: dict) -> bool:
    """Block one model re-call of the exact action just run from voice consent."""
    global _voice_replay
    key = _fingerprint(name, parameters)
    with _lock:
        if _voice_replay is None:
            return False
        replay_key, expires_at = _voice_replay
        if time.monotonic() > expires_at:
            _voice_replay = None
            return False
        if key != replay_key:
            return False
        _voice_replay = None
        return True


def clear_voice_replay() -> None:
    global _voice_replay
    with _lock:
        _voice_replay = None


def begin_mission(description: str) -> str:
    """Start a short-lived mission scope from the user's explicit request."""
    global _mission, _state, _clarification_required
    normalized = _normalize(description)
    words = set(normalized.split())
    search_verbs = {"cherche", "chercher", "recherche", "rechercher", "trouve", "trouver", "search", "find"}
    chrome_terms = {"chrome", "navigateur", "onglets", "browser", "tabs"}
    report_terms = {"rapport", "redige", "ecris", "rapport", "report", "write", "document"}
    clipboard_terms = {"copie", "copier", "copy", "colle", "coller", "paste"}
    grants: set[str] = set()
    if words & search_verbs:
        grants.add("web_search")
        grants.update(f"web_search:{category}"
                      for category in _search_categories(normalized))
    if words & chrome_terms and (words & search_verbs or words & {"ouvre", "ouvrir", "analyse", "analyser", "consulte", "regarde"}):
        grants.add("chrome_research")
    if words & report_terms and (words & search_verbs or words & {"redige", "ecris", "write"}):
        grants.add("report_write")
    if words & clipboard_terms:
        grants.add("clipboard")
    with _lock:
        _mission = _Mission(uuid.uuid4().hex, str(description)[:1000],
                            time.monotonic(), frozenset(grants))
        _clarification_required = False
        if _pending is None:
            _state = ConfirmationState.IDLE
        return _mission.mission_id


def clear_mission() -> None:
    global _mission, _state, _clarification_required
    with _lock:
        _mission = None
        _clarification_required = False
        if _pending is None:
            _state = ConfirmationState.IDLE


def mission_authorizes(name: str, parameters: dict) -> bool:
    """Allow only safe, task-related steps explicitly requested in this mission."""
    with _lock:
        mission_id = _active_mission_id_locked()
        mission = _mission
    if mission is None or mission.mission_id != mission_id:
        return False
    params = parameters or {}
    action = _normalize(params.get("action", ""))
    if name == "web_search" and "web_search" in mission.grants:
        mission_categories = {
            grant.partition(":")[2]
            for grant in mission.grants
            if grant.startswith("web_search:")
        }
        return bool(mission_categories & _search_categories(params.get("query", "")))
    if name == "open_app" and "chrome_research" in mission.grants:
        app = _normalize(params.get("app_name", ""))
        return app in {"chrome", "google chrome"}
    if name == "browser_control" and "chrome_research" in mission.grants:
        browser = _normalize(params.get("browser", ""))
        safe_actions = {"go to", "search", "new tab", "get text", "get url", "back", "forward", "reload"}
        return browser == "chrome" and action in safe_actions
    if name == "file_controller" and "report_write" in mission.grants:
        if action not in {"create file", "write"}:
            return False
        filename = str(params.get("name", "")).strip()
        if Path(filename).suffix.lower() not in {".txt", ".md"}:
            return False
        try:
            from actions.file_controller import _resolve_path
            directory = _resolve_path(str(params.get("path", "desktop"))).resolve()
            target = (directory / filename).resolve()
            home = Path.home().resolve()
            return target.is_relative_to(home) and not target.exists()
        except (OSError, RuntimeError, ValueError):
            return False
    if name == "computer_control" and "clipboard" in mission.grants:
        return action in {"copy", "paste"}
    return False
