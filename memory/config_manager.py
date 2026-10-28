import json
import sys
from pathlib import Path

from core.avatar_paths import avatar_config_path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR    = get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"

def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def config_exists() -> bool:
    return CONFIG_FILE.exists()

def save_api_keys(gemini_api_key: str) -> None:
    ensure_config_dir()

    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    data["gemini_api_key"] = gemini_api_key.strip()

    CONFIG_FILE.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )

def load_api_keys() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Failed to load api_keys.json: {e}")
        return {}

def get_gemini_key() -> str | None:
    return load_api_keys().get("gemini_api_key")

def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)


def get_assistant_name() -> str:
    """Return the configured assistant name, or 'JARVIS' if not set."""
    return load_api_keys().get("assistant_name", "JARVIS") or "JARVIS"


def get_user_name() -> str:
    """Return the configured user name for addressing."""
    return load_api_keys().get("user_name", "")


def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["assistant_name"] = assistant_name.strip() or "JARVIS"
    data["user_name"] = user_name.strip()
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Assistant voice ──────────────────────────────────────────────────────────
# Gemini Live prebuilt voices. Names are proper nouns — identical in every
# language, so this list is safe to show verbatim in any locale.
AVAILABLE_VOICES = ["Charon", "Puck", "Kore", "Fenrir", "Aoede"]
DEFAULT_VOICE    = "Charon"


def get_voice() -> str:
    """Return the configured Live voice, falling back to the default if unset
    or if the stored value is not a voice we recognise."""
    v = load_api_keys().get("voice_name", DEFAULT_VOICE) or DEFAULT_VOICE
    return v if v in AVAILABLE_VOICES else DEFAULT_VOICE


def save_voice(voice_name: str) -> None:
    """Persist the chosen Live voice. Unknown names collapse to the default so a
    bad value can never reach the API and break the session."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    v = (voice_name or "").strip()
    data["voice_name"] = v if v in AVAILABLE_VOICES else DEFAULT_VOICE
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_wake_word_enabled() -> bool:
    """Whether local wake-word gating is on (assistant sleeps until 'Hey Jarvis')."""
    return load_api_keys().get("wake_word_enabled", False)


def save_wake_word_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["wake_word_enabled"] = bool(enabled)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_push_to_talk_enabled() -> bool:
    """Hold-a-key-to-speak. When on, the mic is closed unless the chord is held."""
    return load_api_keys().get("push_to_talk_enabled", False)


def save_push_to_talk_enabled(enabled: bool) -> None:
    _save_flag("push_to_talk_enabled", enabled)


HUD_STYLES = ("face", "core")


def get_hud_style() -> str:
    """Which centrepiece the HUD draws: the animated head, or the reactor core.

    Taste, not capability — both render in the same software painter and cost
    about the same. Defaults to the head because that is what MARK LIV shipped
    with; anyone who preferred the older look can switch back in ⚙ and the
    choice survives a restart.
    """
    v = str(load_api_keys().get("hud_style", "face")).strip().lower()
    return v if v in HUD_STYLES else "face"


def save_hud_style(style: str) -> None:
    s = str(style or "").strip().lower()
    _save_flag("hud_style", s if s in HUD_STYLES else "face")


def get_garment_path() -> str | None:
    """PNG d'habillage du visage 2D : chemin explicite, "" si retiré, None si jamais choisi.

    None (clé absente) => le visage charge assets/avatar/garment.png par défaut
    s'il existe. "" (retrait explicite, ou fichier déplacé) => aucun habillage,
    même si le fichier par défaut existe. Le choix survit au redémarrage.
    """
    v = load_api_keys().get("garment_path")
    if v is None:
        return None
    v = str(v).strip()
    return v if v and Path(v).is_file() else ""


def save_garment_path(path: str) -> None:
    """Mémorise le PNG d'habillage ("" = retiré, voir get_garment_path)."""
    _save_flag("garment_path", str(path or ""))


def get_photo_path() -> str | None:
    """Portrait photoréaliste du visage 2D : chemin, "" si retiré, None si jamais choisi.

    Même convention à 3 états que l'habillage : None => assets/avatar/face.png
    par défaut s'il existe, "" => tête procédurale même si face.png existe.
    """
    v = load_api_keys().get("photo_path")
    if v is None:
        return None
    v = str(v).strip()
    return v if v and Path(v).is_file() else ""


def save_photo_path(path: str) -> None:
    """Mémorise le portrait ("" = retiré, voir get_photo_path)."""
    _save_flag("photo_path", str(path or ""))


# ── Live-session tuning ──────────────────────────────────────────────────────
# Everything here is optional and has a working default, so an untouched
# config behaves exactly like a configured one. Each value is also a way out:
# if a future model dislikes one of these, set it back and nothing else changes.

def get_thinking_enabled() -> bool:
    """Whether the Live model may spend tokens thinking before it answers.

    Off by default. A voice assistant is judged on how fast it starts talking,
    and the reasoning that actually needs deliberation in this app is delegated
    to the planning tools, which run on a separate non-Live model.
    """
    return bool(load_api_keys().get("thinking_enabled", False))


def save_thinking_enabled(enabled: bool) -> None:
    _save_flag("thinking_enabled", enabled)


def get_turn_tuning() -> dict:
    """How eagerly the server decides you have stopped speaking.

    OFF by default, and that default was earned. Cutting turns shorter looks
    like a free speed win and is not: proactive audio has to judge whether an
    utterance was even addressed to the assistant, and a turn clipped early
    gives it less to judge, so it stays quiet — and the reply to your first
    sentence only arrives once your second one has given it enough context.
    That reads as the assistant being a turn behind, which is far worse than
    the fraction of a second the tuning saves.

    Turn it on with "turn_tuning": {"enabled": true} if your own microphone and
    speaking pace suit it. `silence_ms` is the one that is felt: the pause the
    server sits through before accepting your turn is over.
    """
    cfg = load_api_keys().get("turn_tuning")
    cfg = cfg if isinstance(cfg, dict) else {}

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(cfg.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "enabled":    bool(cfg.get("enabled", False)),
        "silence_ms": _int("silence_ms", 550, 200, 3000),
        "prefix_ms":  _int("prefix_ms", 150, 0, 1000),
        # "high" = quicker to decide speech has ended.
        "end_sensitivity":   str(cfg.get("end_sensitivity", "high")).lower(),
        "start_sensitivity": str(cfg.get("start_sensitivity", "default")).lower(),
    }


def save_turn_tuning(values: dict) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    cur = data.get("turn_tuning")
    cur = dict(cur) if isinstance(cur, dict) else {}
    cur.update(values or {})
    data["turn_tuning"] = cur
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_proactive_audio_enabled() -> bool:
    """Whether the model gets to decide an utterance was not aimed at it and
    stay quiet.

    On by default — it is what stops the assistant answering the room. But it
    is also the first thing to switch off if replies ever seem to arrive a turn
    late: what looks like lag is usually the model having judged your previous
    sentence as not addressed to it, and only changing its mind once the next
    one arrives.
    """
    return bool(load_api_keys().get("proactive_audio", True))


def save_proactive_audio_enabled(enabled: bool) -> None:
    _save_flag("proactive_audio", enabled)


MEDIA_RESOLUTIONS = ("default", "low", "medium", "high")


def get_media_resolution() -> str:
    """How finely the model tokenises the screenshots and camera frames it is
    sent. 'medium' keeps on-screen text readable at a fraction of the tokens a
    full-resolution frame costs; 'low' is cheaper still but starts losing small
    text, which is most of what screen captures are for."""
    v = str(load_api_keys().get("media_resolution", "medium")).strip().lower()
    return v if v in MEDIA_RESOLUTIONS else "medium"


def save_media_resolution(value: str) -> None:
    v = str(value or "").strip().lower()
    _save_flag("media_resolution", v if v in MEDIA_RESOLUTIONS else "medium")


def _save_flag(key: str, value) -> None:
    """Read-modify-write one key without disturbing the rest of the config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[key] = bool(value) if isinstance(value, bool) else value
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_brief_enabled() -> bool:
    return load_api_keys().get("morning_brief_enabled", True)


def save_brief_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["morning_brief_enabled"] = enabled
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Audio devices ────────────────────────────────────────────────────────────
# Stored as device NAMES, not sounddevice indices. Indices shift every time a
# USB device is plugged in or removed, so a saved index silently starts pointing
# at a different microphone. The empty string means "system default", which is
# both the factory setting and what an unresolvable saved device falls back to —
# so unplugging a headset degrades to the built-in speakers instead of crashing.

def _patch_config(**fields) -> None:
    """Read-modify-write one or more keys in api_keys.json.

    Every setter in this file open-coded this. Collapsing it here means a new
    setting is one line, and there is one place where a corrupt config file is
    handled instead of nine."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(fields)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_input_device() -> str:
    """Microphone device name, or '' for the system default."""
    return (load_api_keys().get("input_device", "") or "").strip()


def save_input_device(name: str) -> None:
    _patch_config(input_device=(name or "").strip())


def get_output_device() -> str:
    """Speaker device name, or '' for the system default."""
    return (load_api_keys().get("output_device", "") or "").strip()


def save_output_device(name: str) -> None:
    _patch_config(output_device=(name or "").strip())


def get_plugin_enabled(plugin_name: str) -> bool:
    """Plugins are enabled by default the moment they're discovered (opt-out model)."""
    return load_api_keys().get("plugins_enabled", {}).get(plugin_name, True)


# ── Per-plugin settings ("tokens" / connection details) ───────────────────────
# Generic store so a plugin can declare its own config fields (PLUGIN_SETTINGS)
# and the settings UI renders + persists them WITHOUT any core edit — keeping the
# drop-in model intact. Values live under plugin_config[<namespace>][<key>].
# A namespace defaults to the plugin name, but a suite of plugins (e.g. the
# several printer plugins) can share ONE namespace.
def get_plugin_config(namespace: str) -> dict:
    """All stored values for a namespace (empty dict if none set yet)."""
    cfg = load_api_keys().get("plugin_config")
    val = cfg.get(namespace) if isinstance(cfg, dict) else None
    return dict(val) if isinstance(val, dict) else {}


def get_plugin_setting(namespace: str, key: str, default=None):
    """A single value from a namespace, or `default` if unset."""
    return get_plugin_config(namespace).get(key, default)


def save_plugin_config(namespace: str, values: dict) -> None:
    """Merge `values` into a namespace's stored config (read-modify-write, like
    every other helper here). Only the provided keys are touched."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    pc = data.get("plugin_config")
    if not isinstance(pc, dict):
        pc = {}
    cur = pc.get(namespace)
    if not isinstance(cur, dict):
        cur = {}
    cur.update(values)
    pc[namespace] = cur
    data["plugin_config"] = pc
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def save_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    plugins_cfg = data.get("plugins_enabled")
    if not isinstance(plugins_cfg, dict):
        plugins_cfg = {}
    plugins_cfg[plugin_name] = enabled
    data["plugins_enabled"] = plugins_cfg
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


LIPSYNC_FILE = avatar_config_path("avatar_lipsync.json")

def _lipsync_gain(value, default: float = 1.0) -> float:
    try:
        return max(0.2, min(2.0, float(value)))
    except (TypeError, ValueError):
        return default


def _lipsync_fluidite(value) -> float:
    try:
        return max(0.5, min(1.5, float(value)))
    except (TypeError, ValueError):
        return 1.0


def _lipsync_seuil(value) -> float:
    try:
        return max(0.0, min(0.10, float(value)))
    except (TypeError, ValueError):
        return 0.02


def _lipsync_sync(value) -> float:
    try:
        return max(-500.0, min(500.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


_TRIM_COMPS = ("open", "close", "stretch", "round")
_HUMEURS_CONNUES = ("neutre", "ecoute", "sourire", "grand_sourire", "joie",
                    "triste", "colere", "surpris", "reflechi")


def _lipsync_humeurs(data) -> dict:
    """Intensités par humeur {nom: 0.1..1.0}, creux (1.0 implicite)."""
    out: dict = {}
    if not isinstance(data, dict):
        return out
    for nom, val in data.items():
        if str(nom) not in _HUMEURS_CONNUES:
            continue
        try:
            v = max(0.1, min(1.0, float(val)))
        except (TypeError, ValueError):
            continue
        if v != 1.0:
            out[str(nom)] = v
    return out


def _lipsync_trim(value) -> float:
    try:
        return max(-0.5, min(2.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


def _lipsync_trims(data) -> dict:
    """Trims par visème {id: {comp: 0..2}}, creux (seuls les ≠1 persistent)."""
    out: dict = {}
    if not isinstance(data, dict):
        return out
    for vid, trims in data.items():
        try:
            i = int(vid)
        except (TypeError, ValueError):
            continue
        if not 0 <= i <= 14 or not isinstance(trims, dict):
            continue
        clean = {}
        for comp, val in trims.items():
            if str(comp).lower() in _TRIM_COMPS:
                v = _lipsync_trim(val)
                if v != 1.0:
                    clean[str(comp).lower()] = v
        if clean:
            out[str(i)] = clean
    return out


def _lipsync_neutre(data) -> dict:
    """Neutre personnalisé : les 7 gains de référence du comparateur."""
    base = {"jaw_gain": 1.0, "articulation_gain": 1.0, "fluidite": 1.0,
            "force_fermetures": 1.0, "amplitude": 1.0, "sync_ms": 0.0,
            "seuil_silence": 0.02}
    if not isinstance(data, dict):
        return dict(base)
    return {
        "jaw_gain": _lipsync_gain(data.get("jaw_gain"), 1.0),
        "articulation_gain": _lipsync_gain(data.get("articulation_gain"), 1.0),
        "fluidite": _lipsync_fluidite(data.get("fluidite")),
        "force_fermetures": _lipsync_gain(data.get("force_fermetures"), 1.0),
        "amplitude": _lipsync_gain(data.get("amplitude"), 1.0),
        "sync_ms": _lipsync_sync(data.get("sync_ms")),
        "seuil_silence": _lipsync_seuil(data.get("seuil_silence")),
    }


# Plages absolues autorisées par réglage : [min, max] en unités slider.
_PLAGES_ABSOLUES = {
    "jaw_gain": (20, 400), "articulation_gain": (20, 400),
    "fluidite": (50, 300), "force_fermetures": (20, 400),
    "amplitude": (20, 400), "sync_ms": (-1000, 1000),
    "seuil_silence": (0, 20),
    "trim_open": (-50, 300), "trim_close": (-50, 300),
    "trim_stretch": (-50, 300), "trim_round": (-50, 300),
}
_PLAGES_DEFAUT = {
    "jaw_gain": [20, 200], "articulation_gain": [20, 200],
    "fluidite": [50, 150], "force_fermetures": [20, 200],
    "amplitude": [20, 200], "sync_ms": [-500, 500],
    "seuil_silence": [0, 10],
    "trim_open": [-50, 100], "trim_close": [0, 200],
    "trim_stretch": [0, 200], "trim_round": [0, 200],
}


def _lipsync_plages(data) -> dict:
    """Plages des sliders {réglage: [min, max]}, bornées aux absolus."""
    out = {k: list(v) for k, v in _PLAGES_DEFAUT.items()}
    if not isinstance(data, dict):
        return out
    for cle, (abs_lo, abs_hi) in _PLAGES_ABSOLUES.items():
        val = data.get(cle)
        if not isinstance(val, (list, tuple)) or len(val) != 2:
            continue
        try:
            lo, hi = int(val[0]), int(val[1])
        except (TypeError, ValueError):
            continue
        lo = max(abs_lo, min(abs_hi, lo))
        hi = max(abs_lo, min(abs_hi, hi))
        if lo < hi:
            out[cle] = [lo, hi]
    return out


def _lipsync_autocalib(data) -> dict:
    """Paramètres de l'auto-calibration (seuils et ampleurs d'ajustement)."""
    neutre = {"sonde": 0.9, "attente_ms": 600.0, "pp_fuite": 0.04,
              "aa_bas": 0.15, "aa_haut": 0.50, "i_bas": 0.04, "i_haut": 0.20,
              "jaw_plus": 1.3, "jaw_moins": 0.8, "trim_fort": 1.5}

    def _dans(valeur, lo, hi, defaut):
        try:
            return max(lo, min(hi, float(valeur)))
        except (TypeError, ValueError):
            return defaut

    if not isinstance(data, dict):
        return dict(neutre)
    return {
        "sonde": _dans(data.get("sonde"), 0.5, 1.0, 0.9),
        "attente_ms": _dans(data.get("attente_ms"), 300.0, 1500.0, 600.0),
        "pp_fuite": _dans(data.get("pp_fuite"), 0.0, 0.20, 0.04),
        "aa_bas": _dans(data.get("aa_bas"), 0.05, 0.40, 0.15),
        "aa_haut": _dans(data.get("aa_haut"), 0.30, 0.80, 0.50),
        "i_bas": _dans(data.get("i_bas"), 0.0, 0.20, 0.04),
        "i_haut": _dans(data.get("i_haut"), 0.10, 0.40, 0.20),
        "jaw_plus": _dans(data.get("jaw_plus"), 1.0, 2.0, 1.3),
        "jaw_moins": _dans(data.get("jaw_moins"), 0.2, 1.0, 0.8),
        "trim_fort": _dans(data.get("trim_fort"), 1.0, 2.0, 1.5),
    }


def get_lipsync_gains() -> dict:
    """Calibration labiale : gains mâchoire/articulation/amplitude (0.2..2.0),
    fluidité (0.5..1.5), force des fermetures (0.2..2.0), décalage A/V en ms
    (-500..+500, négatif = bouche plus tôt), seuil de silence (0..0.10),
    trims par visème, intensités par humeur et paramètres d'auto-calibration.

    Fichier dédié config/avatar_lipsync.json, servi tel quel à la page 3D
    (GET /avatar_lipsync.json) qui l'applique au démarrage. Neutres : 1.0,
    0 ms, 0.02 ; les clés absentes (anciens fichiers) valent le neutre.
    """
    neutre = {"jaw_gain": 1.0, "articulation_gain": 1.0,
              "fluidite": 1.0, "force_fermetures": 1.0,
              "amplitude": 1.0, "sync_ms": 0.0, "seuil_silence": 0.02,
              "visemes": {}, "humeurs": {},
              "autocalib": _lipsync_autocalib(None),
              "neutre": _lipsync_neutre(None),
              "plages": _lipsync_plages(None)}
    try:
        data = json.loads(LIPSYNC_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return neutre
        return {
            "jaw_gain": _lipsync_gain(data.get("jaw_gain"), 1.0),
            "articulation_gain": _lipsync_gain(data.get("articulation_gain"), 1.0),
            "fluidite": _lipsync_fluidite(data.get("fluidite")),
            "force_fermetures": _lipsync_gain(data.get("force_fermetures"), 1.0),
            "amplitude": _lipsync_gain(data.get("amplitude"), 1.0),
            "sync_ms": _lipsync_sync(data.get("sync_ms")),
            "seuil_silence": _lipsync_seuil(data.get("seuil_silence")),
            "visemes": _lipsync_trims(data.get("visemes")),
            "humeurs": _lipsync_humeurs(data.get("humeurs")),
            "autocalib": _lipsync_autocalib(data.get("autocalib")),
            "neutre": _lipsync_neutre(data.get("neutre")),
            "plages": _lipsync_plages(data.get("plages")),
        }
    except Exception:
        return neutre


def save_lipsync_gains(jaw_gain: float = 1.0, articulation_gain: float = 1.0,
                       fluidite: float = 1.0,
                       force_fermetures: float = 1.0,
                       amplitude: float = 1.0, sync_ms: float = 0.0,
                       seuil_silence: float = 0.02,
                       visemes: dict | None = None,
                       humeurs: dict | None = None,
                       autocalib: dict | None = None,
                       neutre: dict | None = None,
                       plages: dict | None = None) -> dict:
    """Persiste la calibration labiale (valeurs bornées) et la retourne."""
    ensure_config_dir()
    data = {
        "jaw_gain": _lipsync_gain(jaw_gain, 1.0),
        "articulation_gain": _lipsync_gain(articulation_gain, 1.0),
        "fluidite": _lipsync_fluidite(fluidite),
        "force_fermetures": _lipsync_gain(force_fermetures, 1.0),
        "amplitude": _lipsync_gain(amplitude, 1.0),
        "sync_ms": _lipsync_sync(sync_ms),
        "seuil_silence": _lipsync_seuil(seuil_silence),
        "visemes": _lipsync_trims(visemes or {}),
        "humeurs": _lipsync_humeurs(humeurs or {}),
        "autocalib": _lipsync_autocalib(autocalib or {}),
        "neutre": _lipsync_neutre(neutre or {}),
        "plages": _lipsync_plages(plages or {}),
    }
    LIPSYNC_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data