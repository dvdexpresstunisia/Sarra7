"""Moteur de raisonnement indépendant de Gemini Live."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
PROVIDERS_FILE = BASE_DIR / "config" / "providers.json"
API_KEYS_FILE = BASE_DIR / "config" / "api_keys.json"
STATE_FILE = BASE_DIR / "config" / "reasoning_state.json"
AUDIT_FILE = BASE_DIR / "config" / "reasoning_audit.jsonl"
_LOCK = threading.RLock()
_SYSTEM_PROMPT = (
    "Tu es un moteur de raisonnement strict. Réponds UNIQUEMENT à partir du contexte fourni. "
    "Si le contexte est insuffisant, réponds exactement : Je n'ai pas assez d'éléments pour affirmer. "
    "N'invente jamais faits, chiffres, citations, URLs, noms, APIs ou versions. "
    "Sépare FAIT (sourcé), INFÉRENCE et INCONNU. Termine par confidence: 0.0-1.0 et assumptions: []."
)


class ReasoningError(RuntimeError):
    """Erreur explicite : aucune réponse de secours n'est fabriquée."""


def _read(path: Path, default: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default.copy()
    except (OSError, ValueError):
        return default.copy()


def _config() -> dict:
    return _read(PROVIDERS_FILE, {"default_provider": "ollama", "providers": {}})


def _state() -> dict:
    return _read(STATE_FILE, {"active_provider": "ollama", "settings": {}, "strict_mode": True, "temperature": 0.0, "timeout": 45})


def _api_config() -> dict:
    return _read(API_KEYS_FILE, {})


def provider_names() -> list[str]:
    return list(_config().get("providers", {}).keys())


def active_provider() -> str:
    cfg = _config()
    selected = _state().get("active_provider") or cfg.get("default_provider")
    if selected in cfg.get("providers", {}):
        return str(selected)
    default = cfg.get("default_provider", "")
    return str(default) if default in cfg.get("providers", {}) else ""


def provider_spec(name: str) -> dict:
    spec = _config().get("providers", {}).get(name)
    if not isinstance(spec, dict):
        raise ReasoningError(f"Provider inconnu : {name}")
    return spec


def get_settings(name: str | None = None) -> dict:
    name = name or active_provider()
    spec = provider_spec(name)
    saved = _state().get("settings", {}).get(name, {})
    api_saved = _api_config().get("reasoning", {}).get(name, {})
    return {
        "provider": name,
        "label": spec.get("label", name),
        "base_url": saved.get("base_url", spec.get("base_url", "")),
        "model": saved.get("model", (spec.get("fallback_models") or [""])[0]),
        "temperature": min(0.1, max(0.0, float(saved.get("temperature", _state().get("temperature", 0.0))))),
        "strict_mode": bool(saved.get("strict_mode", _state().get("strict_mode", True))),
        "timeout": min(300, max(5, int(saved.get("timeout", _state().get("timeout", 45))))),
        "api_key": str(api_saved.get("api_key", "")),
        "free_key_url": spec.get("free_key_url", ""),
        "notes": spec.get("notes", ""),
    }


def save_settings(provider: str, values: dict) -> dict:
    spec = provider_spec(provider)
    base_url = str(values.get("base_url", spec.get("base_url", ""))).strip()
    model = str(values.get("model", "")).strip()
    if not base_url or base_url.startswith("TODO:") or not model:
        raise ReasoningError("Base URL et modèle obligatoires")
    state = _state()
    state.setdefault("settings", {})[provider] = {
        "base_url": base_url, "model": model,
        "temperature": min(0.1, max(0.0, float(values.get("temperature", 0.0)))),
        "strict_mode": bool(values.get("strict_mode", True)),
        "timeout": min(300, max(5, int(values.get("timeout", 45)))),
    }
    state["active_provider"] = provider
    api = _api_config()
    entered_key = str(values.get("api_key", "")).strip()
    current_key = _api_config().get("reasoning", {}).get(provider, {}).get("api_key", "")
    api.setdefault("reasoning", {})[provider] = {"api_key": entered_key or current_key}
    with _LOCK:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        API_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
        API_KEYS_FILE.write_text(json.dumps(api, indent=2), encoding="utf-8")
    return get_settings(provider)


def set_active_provider(name: str) -> str:
    provider_spec(name)
    state = _state()
    state["active_provider"] = name
    with _LOCK:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return name


def _key(spec: dict, provider: str) -> str:
    saved = _api_config().get("reasoning", {}).get(provider, {})
    return str(saved.get("api_key") or os.getenv(str(spec.get("api_key_env", "")), "")).strip()


def _is_free(model: dict) -> bool:
    ident = str(model.get("id", "")).lower()
    pricing = model.get("pricing", {})
    return ident.endswith(":free") or ident == "free" or (
        isinstance(pricing, dict) and all(str(pricing.get(k, "")) in ("0", "0.0", "0.00") for k in ("prompt", "completion"))
    )


def list_models(provider: str, api_key: str | None = None, base_url: str | None = None, timeout: float = 20.0) -> list[dict]:
    spec = provider_spec(provider)
    url = (base_url or get_settings(provider)["base_url"]).rstrip("/")
    if url.startswith("TODO:"):
        raise ReasoningError("Provider non documenté")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = requests.get(url + str(spec.get("models_path", "/models")), headers=headers, timeout=timeout)
        response.raise_for_status()
        raw = response.json().get("data", [])
        return sorted([item for item in raw if isinstance(item, dict) and item.get("id")], key=lambda item: (not _is_free(item), str(item["id"])))
    except (requests.RequestException, ValueError) as exc:
        fallback = [{"id": model, "fallback": True, "free": True} for model in spec.get("fallback_models", [])]
        if fallback:
            return fallback
        raise ReasoningError(f"GET /models échoué : {exc}") from exc


def _chat(provider: str, prompt: str, system: str, timeout: float, temperature: float, max_tokens: int = 1200) -> tuple[str, dict]:
    spec = provider_spec(provider)
    settings = get_settings(provider)
    key = _key(spec, provider)
    if provider != "ollama" and not key:
        raise ReasoningError(f"Clé absente pour {spec.get('label', provider)}")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {"model": settings["model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "temperature": temperature, "top_p": 1, "seed": 17, "max_tokens": min(2400, max_tokens), "stream": False}
    started = time.perf_counter()
    try:
        response = requests.post(settings["base_url"].rstrip("/") + "/chat/completions", json=payload, headers=headers, timeout=timeout)
        if response.status_code >= 400:
            raise ReasoningError(f"HTTP {response.status_code}: provider indisponible")
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return str(content).strip(), {"latency_ms": round((time.perf_counter() - started) * 1000), "tokens": data.get("usage", {}).get("total_tokens")}
    except requests.Timeout as exc:
        raise ReasoningError("timeout provider") from exc
    except (requests.RequestException, ValueError) as exc:
        raise ReasoningError(f"appel provider échoué : {exc}") from exc


def _json_result(text: str) -> dict:
    candidate = text.strip().removeprefix("```json").removesuffix("```").strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ReasoningError(f"sortie JSON invalide : {exc.msg}") from exc
    if not isinstance(value, dict) or not all(isinstance(value.get(key), list) for key in ("facts", "inferences", "unknowns", "assumptions")):
        raise ReasoningError("schéma JSON invalide")
    confidence = value.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ReasoningError("confidence invalide")
    return value


def _audit(provider: str, model: str, meta: dict, confidence: float | None, error: str = "") -> None:
    entry = {"time": time.time(), "provider": provider, "model": model, "temperature": get_settings(provider)["temperature"], "latency_ms": meta.get("latency_ms"), "tokens": meta.get("tokens"), "confidence": confidence, "error": error}
    try:
        with AUDIT_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise ReasoningError(f"journal d'audit inaccessible : {exc}") from exc


def _call(name: str, prompt: str, timeout: float) -> str:
    settings = get_settings(name)
    raw, meta = _chat(name, prompt, _SYSTEM_PROMPT, timeout, settings["temperature"])
    try:
        result = _json_result(raw)
    except ReasoningError as first_error:
        raw, meta = _chat(name, prompt + "\nErreur précédente : " + str(first_error) + "\nRéponds uniquement avec un objet JSON valide.", timeout, settings["temperature"])
        result = _json_result(raw)
    _audit(name, settings["model"], meta, float(result["confidence"]))
    return json.dumps(result, ensure_ascii=False)


def reason(prompt: str, timeout: float = 45.0) -> str:
    """Deux passes strictes, puis fallback explicite sans texte fabriqué."""
    cfg = _config()
    active = active_provider()
    candidates = [active] + [p for p in cfg.get("fallbacks", []) if p != active]
    errors = []
    for name in candidates:
        if not name or not cfg.get("providers", {}).get(name, {}).get("enabled_by_default", True):
            continue
        try:
            first = _call(name, prompt, timeout)
            settings = get_settings(name)
            if settings["strict_mode"]:
                critic_prompt = "Critique cet objet JSON contre le contexte suivant et réécris-le corrigé, sans ajouter de faits.\nCONTEXTE:\n" + prompt + "\nOBJET:\n" + first
                return _call(name, critic_prompt, timeout)
            return first
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise ReasoningError("Raisonnement indisponible — aucune réponse fabriquée : " + " | ".join(errors))


def status() -> dict:
    cfg = _config()
    result = []
    for name, spec in cfg.get("providers", {}).items():
        settings = get_settings(name)
        result.append({"name": name, "label": spec.get("label", name), "active": name == active_provider(), "configured": bool(_key(spec, name) or name == "ollama"), "free_key_url": spec.get("free_key_url", ""), "base_url": settings["base_url"], "model": settings["model"], "notes": spec.get("notes", "")})
    return {"active": active_provider(), "providers": result, "audit_file": str(AUDIT_FILE), "timestamp": time.time()}