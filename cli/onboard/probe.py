"""Provider checks and model presets for `ah setup`.

The probe asks each provider for its model list with the key just typed, the
same endpoints the Settings page's "Test" button calls
(dashboard/backend/routes/settings.py, ``test_provider``). It is written
against ``requests`` rather than imported from that route on purpose: the
wizard also runs where only the terminal client is installed (the Docker and
remote paths), and there FastAPI and httpx are not importable.

The presets are three tiers per provider, strongest, balanced and fastest,
each a preference list matched against what the provider actually lists, so a
retired id simply never matches and the next one wins. When the provider could
not be asked, the first id of each list is offered unverified.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

CLOUD = ("openai", "anthropic", "google")
LOCAL = ("ollama", "lmstudio")
PROVIDERS = CLOUD + LOCAL

LABELS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google Gemini",
    "ollama": "Ollama (local)",
    "lmstudio": "LM Studio (local)",
}

# The .env keys each provider is configured through (.env.example).
KEY_VAR = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "google": "GOOGLE_API_KEY"}
URL_VAR = {"openai": "OPENAI_BASE_URL", "ollama": "OLLAMA_BASE_URL", "lmstudio": "LMSTUDIO_BASE_URL"}
MODEL_VAR = {
    "openai": "OPENAI_MODEL",
    "anthropic": "ANTHROPIC_MODEL",
    "google": "GOOGLE_MODEL",
    "ollama": "OLLAMA_MODEL",
    "lmstudio": "LMSTUDIO_MODEL",
}
DEFAULT_URL = {"ollama": "http://localhost:11434", "lmstudio": "http://localhost:1234"}

# tier -> preference list, best first. Matched exactly, or with a date or
# "-latest" suffix (claude-haiku-4-5-20251001 matches claude-haiku-4-5).
TIERS: dict[str, dict[str, list[str]]] = {
    "openai": {
        "balanced": ["gpt-5.6-terra", "gpt-5.5", "gpt-5.4", "gpt-5.1", "gpt-5", "gpt-4.1"],
        "strong": ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.5-pro", "gpt-5.5", "gpt-5.4"],
        "fast": ["gpt-5.6-luna", "gpt-5.4-mini", "gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"],
    },
    "anthropic": {
        "balanced": ["claude-sonnet-5", "claude-sonnet-4-5", "claude-sonnet-4"],
        "strong": ["claude-opus-5-5", "claude-opus-4-7", "claude-opus-4-1"],
        "fast": ["claude-haiku-4-5", "claude-3-5-haiku"],
    },
    "google": {
        "balanced": ["gemini-3-pro", "gemini-2.5-pro", "gemini-2.5-flash"],
        "strong": ["gemini-3-pro", "gemini-2.5-pro"],
        "fast": ["gemini-3-flash", "gemini-2.5-flash", "gemini-2.0-flash"],
    },
}
TIER_LABELS = {"balanced": "balanced", "strong": "strongest", "fast": "fastest and cheapest"}

_SUFFIX = re.compile(r"^-(\d{8}|\d{4}-\d{2}-\d{2}|latest)$")


@dataclass
class ProbeResult:
    ok: bool
    models: list[str] = field(default_factory=list)
    error: str = ""
    latency_ms: int = 0


def probe(provider: str, api_key: str = "", base_url: str = "", timeout: float = 10.0) -> ProbeResult:
    """Ask a provider which models it serves. Never raises."""
    import requests

    start = time.time()

    def done(models: list[str]) -> ProbeResult:
        return ProbeResult(True, sorted({m for m in models if m}), latency_ms=int((time.time() - start) * 1000))

    try:
        if provider == "openai":
            if not api_key:
                return ProbeResult(False, error="no API key")
            base = (base_url or "https://api.openai.com/v1").rstrip("/")
            r = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
            r.raise_for_status()
            return done([m.get("id", "") for m in r.json().get("data", [])])
        if provider == "anthropic":
            if not api_key:
                return ProbeResult(False, error="no API key")
            r = requests.get("https://api.anthropic.com/v1/models", params={"limit": 1000},
                             headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                             timeout=timeout)
            r.raise_for_status()
            return done([m.get("id", "") for m in r.json().get("data", [])])
        if provider == "google":
            if not api_key:
                return ProbeResult(False, error="no API key")
            r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                             params={"key": api_key, "pageSize": 1000}, timeout=timeout)
            r.raise_for_status()
            items = r.json().get("models", [])
            return done([m.get("name", "").replace("models/", "") for m in items
                         if "generateContent" in (m.get("supportedGenerationMethods") or ["generateContent"])])
        if provider == "ollama":
            base = (base_url or DEFAULT_URL["ollama"]).rstrip("/")
            r = requests.get(f"{base}/api/tags", timeout=timeout)
            r.raise_for_status()
            return done([m.get("name", "") for m in r.json().get("models", [])])
        if provider == "lmstudio":
            base = (base_url or DEFAULT_URL["lmstudio"]).rstrip("/")
            r = requests.get(f"{base}/v1/models", timeout=timeout)
            r.raise_for_status()
            return done([m.get("id", "") for m in r.json().get("data", [])])
        return ProbeResult(False, error=f"unknown provider {provider}")
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        reason = "the key was rejected" if code in (401, 403) else f"HTTP {code}"
        return ProbeResult(False, error=reason)
    except requests.ConnectionError:
        return ProbeResult(False, error="could not connect")
    except requests.Timeout:
        return ProbeResult(False, error="timed out")
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never stops the wizard
        return ProbeResult(False, error=str(exc) or exc.__class__.__name__)


def _match(preferred: str, models: list[str]) -> Optional[str]:
    if preferred in models:
        return preferred
    dated = sorted((m for m in models if m.startswith(preferred) and _SUFFIX.match(m[len(preferred):])),
                   reverse=True)
    return dated[0] if dated else None


def presets(provider: str, models: list[str]) -> dict[str, tuple[str, bool]]:
    """``{tier: (model id, verified)}`` for the tiers this provider has.

    Verified means the provider listed it. With no list at all (the probe
    failed or was skipped) each tier falls back to its first preference,
    unverified. A local runtime has no tiers: whatever is pulled is the
    choice, and the first model it lists stands in for "balanced".
    """
    tiers = TIERS.get(provider)
    if not tiers:
        return {"balanced": (models[0], True)} if models else {}
    out: dict[str, tuple[str, bool]] = {}
    for tier, prefs in tiers.items():
        if models:
            hit = next((m for m in (_match(p, models) for p in prefs) if m), None)
            if hit:
                out[tier] = (hit, True)
        else:
            out[tier] = (prefs[0], False)
    return out


def mask(secret: str) -> str:
    """The first and last few characters of a key, for "keep the current one"."""
    secret = secret or ""
    if len(secret) <= 10:
        return "****" if secret else ""
    return f"{secret[:5]}…{secret[-4:]}"
