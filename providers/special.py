"""
Special models: the models a workspace uses for work a chat model does not do.

A chat model answers in text. Pictures, video, speech and transcripts come
from other models with other APIs, and a team may also run a model of its own
for one narrow job. Each of those is a *purpose*; a workspace picks the model
for each purpose it wants (``special_models`` in the workspace metadata). A
workspace uses only the models it added itself, with one exception: a
personal workspace (common/personal_workspace.py) takes ``default``'s model
for each purpose it left empty, so a person's assistant can listen and speak
without anyone configuring their workspace. Such an entry carries
``inherited_from: "default"`` and is called with default's connection
settings (:func:`entry_endpoint`); its price is still charged to the run, so
to the personal workspace. Custom models are never inherited.

Agents never choose the model. They call the purpose's tool
(tools/special_models.py: ``generate_image``, ``generate_video``,
``synthesize_speech``, ``transcribe_audio``, ``ask_special_model``), which runs
whatever the workspace configured, and their prompt says which purposes have
a model here (:func:`prompt_section`). A tool whose purpose has no model stays
on the agent and answers with an error saying the model is not added, so the
agent can tell the user what to add instead of quietly doing without.

Stored shape::

    {
      "image":         {"provider": "openai", "model": "gpt-image-1",
                        "price_usd": 0.04, "options": {"size": "1024x1024"}},
      "video":         {...}, "speech": {...}, "transcription": {...},
      "custom": [
        {"id": "jev", "description": "what it is for", "kind": "chat",
         "provider": "my-vllm", "model": "jev-7b", "price_usd": 0.0},
        {"id": "segmenter", "description": "...", "kind": "http",
         "url": "https://gpu.internal/segment", "headers": {"Authorization": "Bearer ${SEG_TOKEN}"}}
      ]
    }

``price_usd`` is what one unit costs (:attr:`Purpose.unit`): it is charged to
the run's money cap and shown on the Costs page (common/aux_usage.py).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: Workspace metadata key.
META_KEY = "special_models"

#: How a provider's API is spoken: the OpenAI shape (OpenAI itself, a local
#: server, a custom backend with the openai adapter) or Google's Gemini API.
OPENAI = "openai_compat"
GOOGLE = "google"
#: Anthropic's API, only for a custom chat model's connection check
#: (:func:`check`): it has no model for any purpose.
ANTHROPIC = "anthropic"


@dataclass(frozen=True)
class Purpose:
    id: str
    tool: str
    #: What ``price_usd`` is the price of.
    unit: str
    #: One line for the agent's prompt.
    summary: str
    #: API shapes that can serve the purpose.
    kinds: Tuple[str, ...]
    #: Model ids to suggest per API shape (the field stays free text).
    suggestions: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    #: Options the purpose accepts, with a short hint each.
    options: Dict[str, str] = field(default_factory=dict)
    #: Known values of the ``voice`` option per API shape (the field stays free
    #: text): the Models page and the assistant's voice picker offer them.
    voices: Dict[str, Tuple[str, ...]] = field(default_factory=dict)


PURPOSES: Tuple[Purpose, ...] = (
    Purpose(
        "image", "generate_image", "image",
        "creates or edits an image from a description and saves it as a workspace file",
        (OPENAI, GOOGLE),
        {OPENAI: ("gpt-image-1", "gpt-image-1-mini", "dall-e-3"),
         GOOGLE: ("gemini-2.5-flash-image", "imagen-4.0-generate-001")},
        {"size": "for example 1024x1024, 1536x1024 (OpenAI)", "quality": "low, medium, high or auto (OpenAI)",
         "aspect_ratio": "for example 1:1, 16:9 (Imagen)"},
    ),
    Purpose(
        "video", "generate_video", "second",
        "creates a short video clip from a description and saves it as a workspace file",
        (OPENAI, GOOGLE),
        {OPENAI: ("sora-2", "sora-2-pro"),
         GOOGLE: ("veo-3.0-generate-001", "veo-3.0-fast-generate-001")},
        {"size": "for example 1280x720 (OpenAI)", "seconds": "default clip length, for example 4 or 8",
         "aspect_ratio": "16:9 or 9:16 (Veo)"},
    ),
    Purpose(
        "speech", "synthesize_speech", "1k_chars",
        "reads text aloud and saves the audio as a workspace file",
        (OPENAI, GOOGLE),
        {OPENAI: ("gpt-4o-mini-tts", "tts-1", "tts-1-hd"),
         GOOGLE: ("gemini-2.5-flash-preview-tts",)},
        {"voice": "for example alloy, nova (OpenAI) or Kore, Puck (Google)",
         "format": "mp3, wav, opus (OpenAI)",
         # Only a model that reads it is offered the field (voices_for says which).
         "temperature": "0.1 to 1.5; lower reads steadier, the model's own when empty"},
        {OPENAI: ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage",
                  "shimmer", "verse"),
         GOOGLE: ("Kore", "Puck", "Charon", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr")},
    ),
    Purpose(
        "transcription", "transcribe_audio", "call",
        "turns an audio or video file of the workspace into text",
        (OPENAI, GOOGLE),
        {OPENAI: ("gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"),
         GOOGLE: ("gemini-2.5-flash",)},
        {"language": "ISO code such as en or ru, when known"},
    ),
)

_BY_ID: Dict[str, Purpose] = {p.id: p for p in PURPOSES}
_BY_TOOL: Dict[str, Purpose] = {p.tool: p for p in PURPOSES}

#: The tool for the workspace's own models (``custom`` entries).
CUSTOM_TOOL = "ask_special_model"
CUSTOM_KINDS = ("chat", "http")

#: Every tool this feature adds, for the factory's gating.
SPECIAL_MODEL_TOOLS: Tuple[str, ...] = (*(p.tool for p in PURPOSES), CUSTOM_TOOL)

_CUSTOM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_MAX_CUSTOM = 20


class SpecialModelError(ValueError):
    """A configuration that cannot be stored or a call that cannot be made."""


def get_purpose(purpose_id: str) -> Optional[Purpose]:
    return _BY_ID.get(str(purpose_id or "").strip())


def purpose_of_tool(tool_name: str) -> Optional[Purpose]:
    return _BY_TOOL.get(tool_name)


# ── providers ────────────────────────────────────────────────────────────────

def provider_kind(provider: str) -> Optional[str]:
    """The API shape ``provider`` speaks, or None when it serves none of
    these purposes (Anthropic has no image, video or audio models)."""
    pid = str(provider or "").strip().lower()
    if pid in ("openai", "ollama", "lmstudio"):
        return OPENAI
    if pid == "google":
        return GOOGLE
    if pid in ("anthropic", ""):
        return None
    try:
        from providers.registry import get_backend
        backend = get_backend(pid)
    except Exception:  # noqa: BLE001 - an unreadable registry knows no backend
        backend = None
    if backend is None:
        return None
    return OPENAI if str(backend.get("adapter") or "openai") == "openai" else None


def provider_choices() -> List[Dict[str, Any]]:
    """Providers that can serve a purpose: id, label and API shape."""
    out = [
        {"id": "openai", "label": "OpenAI", "kind": OPENAI},
        {"id": "google", "label": "Google", "kind": GOOGLE},
        {"id": "ollama", "label": "Ollama", "kind": OPENAI},
        {"id": "lmstudio", "label": "LM Studio", "kind": OPENAI},
    ]
    try:
        from providers.registry import list_backends
        from providers.local_models import HUB_LOCAL_ID
        for b in list_backends():
            if str(b.get("adapter") or "openai") == "openai":
                choice = {"id": b["id"], "label": b.get("label") or b["id"], "kind": OPENAI}
                if b["id"] == HUB_LOCAL_ID:
                    # The hub's own runtime: its models are what was downloaded
                    # there, each with its own voices (:func:`model_voices`).
                    choice["local_runtime"] = True
                out.append(choice)
    except Exception:  # noqa: BLE001 - custom backends are optional here
        log.debug("special models: could not list custom backends", exc_info=True)
    return out


@dataclass
class Endpoint:
    kind: str
    base_url: str
    api_key: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    #: The hub's own model runtime, which takes the sampling options
    #: (temperature) a cloud API would refuse.
    local: bool = False


def _effective_settings(workspace: Optional[str]) -> Dict[str, Any]:
    """The connection settings ``workspace`` overrides itself (a key, a base
    URL), ``${VAR}`` resolved; the hub's own come from the process
    environment, as for the agent's model (agents/agent_factory.py)."""
    if not workspace:
        return {}
    try:
        from workspace import get_effective_settings, get_workspace_metadata
        own = (get_workspace_metadata(workspace) or {}).get("settings") or {}
        if not isinstance(own, dict) or not own:
            return {}
        eff = get_effective_settings(workspace) or {}
        return {k: eff.get(k) for k in own if eff.get(k)}
    except Exception:  # noqa: BLE001 - no workspace settings: the process environment decides
        return {}


def endpoint(provider: str, workspace: Optional[str]) -> Endpoint:
    """Where and how to call ``provider`` for this workspace: its own key
    override when it sets one, the hub's key otherwise."""
    pid = str(provider or "").strip().lower()
    eff = _effective_settings(workspace)
    from common.config import settings as cfg
    if pid == "openai":
        key = eff.get("openai_api_key") or os.environ.get("OPENAI_API_KEY") or cfg.openai_api_key or ""
        base = eff.get("openai_base_url") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        return Endpoint(OPENAI, str(base).rstrip("/"), str(key))
    if pid == "google":
        key = eff.get("google_api_key") or os.environ.get("GOOGLE_API_KEY") or cfg.google_api_key or ""
        return Endpoint(GOOGLE, "https://generativelanguage.googleapis.com/v1beta", str(key))
    if pid in ("ollama", "lmstudio"):
        from common.hostnet import host_service_url
        if pid == "ollama":
            raw = eff.get("ollama_base_url") or os.environ.get("OLLAMA_BASE_URL") or cfg.ollama_base_url
        else:
            raw = eff.get("lmstudio_base_url") or os.environ.get("LMSTUDIO_BASE_URL") or cfg.lmstudio_base_url
        base = host_service_url(str(raw or "")).rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        return Endpoint(OPENAI, base, "local")
    from providers.registry import get_backend
    backend = get_backend(pid)
    if backend is None:
        raise SpecialModelError(f"Provider '{provider}' is not configured.")
    if str(backend.get("adapter") or "openai") != "openai":
        raise SpecialModelError(f"Provider '{provider}' does not speak the OpenAI API.")
    headers = backend.get("headers") if isinstance(backend.get("headers"), dict) else {}
    headers = {str(k): str(v) for k, v in headers.items()}
    from providers.local_models import HUB_LOCAL_ID, source_headers
    if pid == HUB_LOCAL_ID:
        headers.update(source_headers())
    return Endpoint(OPENAI, str(backend.get("base_url") or "").rstrip("/"),
                    str(backend.get("api_key") or ""), headers, local=pid == HUB_LOCAL_ID)


# ── configuration ────────────────────────────────────────────────────────────

def _price(value: Any, where: str) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        raise SpecialModelError(f"{where}: price must be a number of US dollars") from None
    if price < 0 or price > 1000:
        raise SpecialModelError(f"{where}: price must be between 0 and 1000 dollars")
    return price


def _options(raw: Any, allowed: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise SpecialModelError("options must be an object")
    out: Dict[str, str] = {}
    for key, value in raw.items():
        k = str(key).strip()
        if not k or value in (None, ""):
            continue
        if allowed is not None and k not in allowed:
            raise SpecialModelError(f"unknown option '{k}'; known: {', '.join(allowed) or 'none'}")
        out[k] = str(value).strip()[:200]
    return out


def _normalize_purpose(purpose: Purpose, raw: Any) -> Optional[Dict[str, Any]]:
    if raw in (None, "", {}):
        return None
    if not isinstance(raw, dict):
        raise SpecialModelError(f"{purpose.id}: expected an object")
    provider = str(raw.get("provider") or "").strip().lower()
    model = str(raw.get("model") or "").strip()
    if not provider and not model:
        return None
    if not provider or not model:
        raise SpecialModelError(f"{purpose.id}: both provider and model are needed")
    kind = provider_kind(provider)
    if kind is None:
        raise SpecialModelError(f"{purpose.id}: provider '{provider}' has no {purpose.id} models here")
    if kind not in purpose.kinds:
        raise SpecialModelError(f"{purpose.id}: provider '{provider}' cannot serve this purpose")
    out: Dict[str, Any] = {"provider": provider, "model": model[:200]}
    price = _price(raw.get("price_usd"), purpose.id)
    if price is not None:
        out["price_usd"] = price
    options = _options(raw.get("options"), purpose.options)
    if "temperature" in options:
        try:
            value = float(options["temperature"].replace(",", "."))
        except ValueError:
            value = -1.0
        if not 0.1 <= value <= 1.5:
            raise SpecialModelError(f"{purpose.id}: temperature must be a number from 0.1 to 1.5")
        options["temperature"] = f"{value:g}"
    if options:
        out["options"] = options
    if purpose.id == "speech" and raw.get("languages"):
        # A voice per language (providers/speech_languages.py).
        from providers import speech_languages
        try:
            languages = speech_languages.normalize(raw.get("languages"))
        except speech_languages.SpeechLanguageError as exc:
            raise SpecialModelError(str(exc)) from None
        if languages:
            out["languages"] = languages
    return out


def _normalize_custom(raw: Any) -> List[Dict[str, Any]]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise SpecialModelError("custom: expected a list")
    if len(raw) > _MAX_CUSTOM:
        raise SpecialModelError(f"custom: at most {_MAX_CUSTOM} models")
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for item in raw:
        if not isinstance(item, dict):
            raise SpecialModelError("custom: each model is an object")
        mid = str(item.get("id") or "").strip().lower()
        if not _CUSTOM_ID_RE.match(mid):
            raise SpecialModelError(
                f"custom: id '{mid}' must be 1 to 48 lowercase letters, digits, '-' or '_'")
        if mid in seen:
            raise SpecialModelError(f"custom: id '{mid}' is used twice")
        seen.add(mid)
        description = " ".join(str(item.get("description") or "").split())[:500]
        if not description:
            raise SpecialModelError(
                f"custom: '{mid}' needs a description; it is how agents know when to use it")
        kind = str(item.get("kind") or "chat").strip().lower()
        if kind not in CUSTOM_KINDS:
            raise SpecialModelError(f"custom: '{mid}' kind must be one of {', '.join(CUSTOM_KINDS)}")
        entry: Dict[str, Any] = {"id": mid, "description": description, "kind": kind}
        name = " ".join(str(item.get("name") or "").split())[:80]
        if name:
            entry["name"] = name
        if kind == "chat":
            provider = str(item.get("provider") or "").strip().lower()
            model = str(item.get("model") or "").strip()
            if not provider or not model:
                raise SpecialModelError(f"custom: '{mid}' needs a provider and a model")
            entry["provider"], entry["model"] = provider, model[:200]
        else:
            url = str(item.get("url") or "").strip()
            if not re.match(r"^https?://", url):
                raise SpecialModelError(f"custom: '{mid}' needs an http(s) url")
            entry["url"] = url[:1000]
            headers = item.get("headers") or {}
            if not isinstance(headers, dict):
                raise SpecialModelError(f"custom: '{mid}' headers must be an object")
            clean = {str(k).strip(): str(v) for k, v in headers.items() if str(k).strip()}
            if clean:
                entry["headers"] = clean
        price = _price(item.get("price_usd"), f"custom '{mid}'")
        if price is not None:
            entry["price_usd"] = price
        out.append(entry)
    return out


def normalize(raw: Any) -> Dict[str, Any]:
    """A configuration as it is stored, or :class:`SpecialModelError`."""
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise SpecialModelError("special_models must be an object")
    unknown = [k for k in raw if k not in _BY_ID and k != "custom"]
    if unknown:
        raise SpecialModelError(f"unknown purpose(s): {', '.join(map(str, unknown))}")
    out: Dict[str, Any] = {}
    for purpose in PURPOSES:
        entry = _normalize_purpose(purpose, raw.get(purpose.id))
        if entry:
            out[purpose.id] = entry
    custom = _normalize_custom(raw.get("custom"))
    if custom:
        out["custom"] = custom
    return out


def stored(workspace: Optional[str]) -> Dict[str, Any]:
    """What ``workspace`` itself configures (no fallback)."""
    if not workspace:
        return {}
    try:
        from common.workspace_context import workspace_name_from_path
        from workspace import get_workspace_metadata
        ws = workspace_name_from_path(workspace) or workspace
        raw = (get_workspace_metadata(ws) or {}).get(META_KEY)
    except Exception:  # noqa: BLE001 - unreadable metadata configures nothing
        log.debug("special models: could not read %s", workspace, exc_info=True)
        return {}
    if not isinstance(raw, dict):
        return {}
    try:
        return normalize(raw)
    except SpecialModelError:
        log.warning("special models: workspace %s holds an invalid configuration", workspace)
        return {}


#: What a literal header value reads as outside the hub (a token stays there).
MASK = "********"


def masked(config: Dict[str, Any]) -> Dict[str, Any]:
    """``config`` with each custom header value that is not only a
    ``${VAR}`` reference replaced by :data:`MASK`, for the API."""
    out = dict(config)
    if config.get("custom"):
        items = []
        for item in config["custom"]:
            item = dict(item)
            if item.get("headers"):
                item["headers"] = {k: (v if re.fullmatch(r"(\$\{[A-Za-z_][A-Za-z0-9_]*\}|\s|Bearer)*", v) else MASK)
                                   for k, v in item["headers"].items()}
            items.append(item)
        out["custom"] = items
    return out


def _unmask(raw: Any, previous: Dict[str, Any]) -> Any:
    """Put the stored value back wherever the form sent :data:`MASK`."""
    if not isinstance(raw, dict) or not isinstance(raw.get("custom"), list):
        return raw
    before = {c["id"]: c for c in previous.get("custom") or []}
    items = []
    for item in raw["custom"]:
        if isinstance(item, dict) and isinstance(item.get("headers"), dict):
            old = (before.get(str(item.get("id") or "").strip().lower()) or {}).get("headers") or {}
            item = {**item, "headers": {k: (old.get(k, "") if v == MASK else v)
                                        for k, v in item["headers"].items()}}
        items.append(item)
    return {**raw, "custom": items}


def save(workspace: str, raw: Any) -> Dict[str, Any]:
    """Validate and store ``workspace``'s own configuration; returns it."""
    config = normalize(_unmask(raw, stored(workspace)))
    from workspace import update_workspace_metadata
    update_workspace_metadata(workspace, {META_KEY: config})
    return config


#: Key on an effective entry that a personal workspace took from another.
INHERITED_KEY = "inherited_from"


def effective(workspace: Optional[str]) -> Dict[str, Any]:
    """What ``workspace`` uses: the models it added itself. A purpose it left
    empty has no model, whatever another workspace has, except in a personal
    workspace, where ``default``'s model for that purpose fills the gap (marked
    with :data:`INHERITED_KEY`)."""
    from common.workspace_context import workspace_name_from_path
    ws = (workspace_name_from_path(workspace) or workspace) if workspace else None
    own = stored(ws)
    from common import personal_workspace
    if not ws or not personal_workspace.is_personal(ws):
        return own
    fallback = personal_workspace.FALLBACK
    out = dict(own)
    for purpose_id, entry in stored(fallback).items():
        if purpose_id == "custom" or purpose_id in out:
            continue
        out[purpose_id] = {**entry, INHERITED_KEY: fallback}
    return out


def voice_entry(purpose: str, workspace: Optional[str], home: Optional[str]) -> Tuple[Optional[Dict[str, Any]], str]:
    """The model the assistant's voice uses for ``purpose`` (transcription or
    speech) and the workspace it is called from: the one of the workspace the
    turn runs in, else the person's home (with the personal fallback to
    ``default``), so a person's assistant keeps its voice in a workspace that
    added none. ``(None, workspace)`` when neither has one."""
    for where in dict.fromkeys(w for w in (workspace or home, home) if w):
        entry = effective(where).get(purpose)
        if entry:
            return entry, where
    return None, workspace or home or ""


def entry_endpoint(entry: Dict[str, Any], workspace: Optional[str]) -> Endpoint:
    """:func:`endpoint` for an :func:`effective` entry: an inherited one is
    called with the connection settings of the workspace it came from."""
    return endpoint(entry["provider"], entry.get(INHERITED_KEY) or workspace)


def configured_tools(workspace: Optional[str]) -> List[str]:
    """The special model tools that work in ``workspace``."""
    config = effective(workspace)
    tools = [p.tool for p in PURPOSES if p.id in config]
    if config.get("custom"):
        tools.append(CUSTOM_TOOL)
    return tools


def options_payload() -> Dict[str, Any]:
    """Purposes, providers and model suggestions, for the settings page."""
    return {
        "purposes": [
            {"id": p.id, "tool": p.tool, "unit": p.unit, "summary": p.summary,
             "kinds": list(p.kinds), "suggestions": {k: list(v) for k, v in p.suggestions.items()},
             "options": dict(p.options), "voices": {k: list(v) for k, v in p.voices.items()}}
            for p in PURPOSES
        ],
        "custom": {"tool": CUSTOM_TOOL, "kinds": list(CUSTOM_KINDS)},
        "providers": provider_choices(),
    }


# ── discovery ────────────────────────────────────────────────────────────────

def _token_re(*words: str) -> "re.Pattern[str]":
    """Any of ``words`` standing on its own inside a model id: ``tts`` finds
    ``gpt-4o-mini-tts`` and ``tts-1`` but not ``shortstop``."""
    return re.compile(r"(?<![a-z])(?:" + "|".join(words) + r")(?![a-z])")


#: What a model id of the OpenAI shape looks like per purpose. These lists
#: carry no capability flags, so the id is all there is to go on: a model the
#: patterns miss can still be typed in by hand.
_OPENAI_FITS: Dict[str, "re.Pattern[str]"] = {
    "image": re.compile(
        r"gpt-image|chatgpt-image|dall-e|stable-diffusion|sdxl|(?<![a-z])sd-?3|flux|imagen|kandinsky"
        r"|playground-v|ideogram|recraft|hidream|qwen-image|seedream"),
    "video": re.compile(
        r"sora|(?<![a-z])veo|(?<![a-z])wan-?2|ltx-?video|hunyuan-?video|mochi|cogvideo|kling|seedance"
        r"|text-to-video|(?<![a-z])[ti]2v(?![a-z])"),
    "speech": re.compile(
        _token_re("tts", "xtts", "kokoro", "piper", "kitten", "supertonic", "bark", "orpheus", "parler",
                  "chatterbox", "speecht5").pattern
        + r"|text-to-speech"),
    "transcription": re.compile(
        _token_re("whisper", "stt", "asr", "parakeet", "canary", "sensevoice", "voxtral").pattern
        + r"|transcri|speech-to-text"),
}

#: Ids that look like a purpose's but are something else: embeddings, chat
#: models that only read the medium, realtime and live (streaming) sessions.
_NOT_A_FIT = re.compile(
    r"embed|moderation|realtime|(?<![a-z])live(?![a-z])|audio-preview|llava|(?<![a-z])vl(?![a-z])|vision")

#: Gemini model ids that do not take an audio file and answer in text.
_GEMINI_NOT_TRANSCRIBING = _token_re("tts", "image", "embedding", "live", "native", "robotics", "computer")


def model_fits(purpose_id: str, kind: str, model_id: str, methods: Optional[List[str]] = None) -> bool:
    """Whether ``model_id`` (with Gemini's ``methods``) can serve the purpose."""
    mid = str(model_id or "").strip().lower()
    if not mid:
        return False
    if kind == GOOGLE:
        methods = list(methods or [])
        generates = "generateContent" in methods
        if purpose_id == "image":
            return mid.startswith("imagen") or (
                mid.startswith("gemini") and generates and bool(_token_re("image").search(mid)))
        if purpose_id == "video":
            return mid.startswith("veo") or "predictLongRunning" in methods
        if purpose_id == "speech":
            return generates and bool(_token_re("tts").search(mid))
        if purpose_id == "transcription":
            return mid.startswith("gemini") and generates and not _GEMINI_NOT_TRANSCRIBING.search(mid)
        return False
    pattern = _OPENAI_FITS.get(purpose_id)
    if pattern is None or _NOT_A_FIT.search(mid) or not pattern.search(mid):
        return False
    # A speech model reads text aloud; one that listens belongs to transcription.
    if purpose_id == "speech" and _OPENAI_FITS["transcription"].search(mid):
        return False
    return True


def _runtime_models() -> List[Dict[str, Any]]:
    """The hub runtime's model list, or nothing when it is not reachable."""
    from providers import local_models as lm
    if not lm.runtime_configured():
        return []
    try:
        return lm.RuntimeClient(timeout=5.0).models()
    except lm.LocalModelError:
        return []


def model_options(provider: str, model: str) -> List[str]:
    """The request options one model takes beyond its voice, when its
    server can say: the hub runtime lists each worker engine's (the cloning
    models' ``temperature``); nothing for every other provider."""
    from providers import local_models as lm
    if str(provider or "").strip().lower() != lm.HUB_LOCAL_ID:
        return []
    for m in _runtime_models():
        if m.get("name") == model:
            return [str(o) for o in m.get("options") or []]
    return []


def model_voices(provider: str, model: str, viewer: Any = None) -> Optional[List[str]]:
    """The voices one model has, when its server can say: the hub runtime
    reads them from each speech model's files (a Piper voice with several
    speakers, Kokoro's, Kitten's and Supertonic's voice sets; a
    single-speaker voice has none), and a cloning model's are the voices
    people recorded, of which ``viewer`` sees their own and the shared ones
    (:func:`providers.local_models.voice_visible`). None for every other
    provider, whose known voices per API shape (:attr:`Purpose.voices`)
    apply."""
    from providers import local_models as lm
    if str(provider or "").strip().lower() != lm.HUB_LOCAL_ID:
        return None
    for m in _runtime_models():
        if m.get("name") == model:
            voices = [str(v) for v in m.get("voices") or []]
            if m.get("engine") in lm.CLONING_ENGINES and viewer is not None:
                try:
                    records = lm.RuntimeClient(timeout=5.0).voices()
                except lm.LocalModelError:
                    records = []
                hidden = {r.get("name") for r in records if not lm.voice_visible(r, viewer)}
                voices = [v for v in voices if v not in hidden]
            return voices
    return []


#: Kokoro's voice names start with their language: ``af_heart`` is American
#: English, ``zf_xiaobei`` Mandarin.
_KOKORO_VOICE_RE = re.compile(r"^([abefhijpz])[fm]_")
_KOKORO_LANG = {"a": "en", "b": "en", "e": "es", "f": "fr", "h": "hi", "i": "it", "j": "ja", "p": "pt", "z": "zh"}
#: A locale inside a model id, the way Piper names its voices (``ru_RU``).
_LOCALE_IN_NAME_RE = re.compile(r"(?:^|[-_/.])([a-z]{2,3})_[A-Z]{2}(?=[-_.]|$)")


def voice_language(model: str, voice: str = "") -> Optional[str]:
    """The language a voice speaks, when its name or its model's says so:
    Kokoro's voice prefix, Piper's locale in the model id, English for every
    Kitten voice. None for a voice that speaks whatever it is given
    (OpenAI's, Google's, Supertonic's)."""
    m = _KOKORO_VOICE_RE.match(str(voice or ""))
    if m and "kokoro" in str(model or "").lower():
        return _KOKORO_LANG[m.group(1)]
    if "kitten" in str(model or "").lower():
        return "en"
    m = _LOCALE_IN_NAME_RE.search(str(model or ""))
    return m.group(1) if m else None


def voices_for(purpose_id: str, provider: str, model: str, viewer: Any = None) -> Dict[str, Any]:
    """The voices the form offers for one model: the model's own, when its
    server can say (:func:`model_voices`), else the ones known for the
    provider's API shape; with the language of each voice that has one, and
    the model's, for a model whose voices all speak it."""
    purpose = get_purpose(purpose_id)
    if purpose is None:
        raise SpecialModelError(f"unknown purpose '{purpose_id}'")
    pid, model = str(provider or "").strip().lower(), str(model or "").strip()
    kind = provider_kind(pid)
    if kind is None or kind not in purpose.kinds:
        raise SpecialModelError(f"provider '{provider}' cannot serve {purpose.id}")
    own = model_voices(pid, model, viewer) if model else None
    voices = own if own is not None else list(purpose.voices.get(kind, ()))
    languages = {v: lang for v in voices if (lang := voice_language(model, v))}
    return {"purpose": purpose.id, "provider": pid, "model": model, "voices": voices,
            "own": own is not None, "language": voice_language(model), "languages": languages,
            "options": [o for o in (model_options(pid, model) if model else []) if o in purpose.options]}


#: What a voice sample reads, per language: one short line, so a cloud model
#: charges a fraction of a cent for it.
SAMPLE_TEXTS: Dict[str, str] = {
    "en": "Hello! This is how this voice sounds. I can read your answers aloud.",
    "ru": "Привет! Так звучит этот голос. Я могу читать ваши ответы вслух.",
    "de": "Hallo! So klingt diese Stimme. Ich kann Ihre Antworten vorlesen.",
    "uk": "Привіт! Так звучить цей голос. Я можу читати ваші відповіді вголос.",
    "es": "¡Hola! Así suena esta voz. Puedo leer tus respuestas en voz alta.",
    "fr": "Bonjour ! Voici comment sonne cette voix. Je peux lire vos réponses à voix haute.",
    "it": "Ciao! Ecco come suona questa voce. Posso leggere ad alta voce le tue risposte.",
    "pt": "Olá! É assim que esta voz soa. Posso ler as suas respostas em voz alta.",
    "pl": "Cześć! Tak brzmi ten głos. Mogę czytać Twoje odpowiedzi na głos.",
    "nl": "Hallo! Zo klinkt deze stem. Ik kan je antwoorden voorlezen.",
    "ja": "こんにちは。これがこの声の響きです。回答を読み上げることができます。",
    "zh": "你好！这就是这个声音的效果。我可以为你朗读回答。",
    "hi": "नमस्ते! यह आवाज़ ऐसी सुनाई देती है। आपके जवाब ज़ोर से पढ़े जा सकते हैं।",
}

#: A voice name as a provider takes it.
VOICE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def sample_text(model: str, voice: str, language: Optional[str]) -> Tuple[str, str]:
    """``(language, text)`` of a voice sample: in the voice's own language
    when it has one, else in ``language`` (the page's), else in English."""
    lang = voice_language(model, voice) or str(language or "").strip().lower()[:2]
    lang = lang if lang in SAMPLE_TEXTS else "en"
    return lang, SAMPLE_TEXTS[lang]


def voice_sample(raw: Any, workspace: Optional[str], language: Optional[str] = None) -> Tuple[Any, str, str]:
    """Read :func:`sample_text` with a speech model the form holds (saved or
    not): ``{"provider", "model", "voice", "options"}``; no provider reads it
    with what the workspace uses (a personal workspace's inherited model too).
    Returns ``(media.Media, language, text)``, or raises
    :class:`SpecialModelError` with the provider's reason."""
    purpose = get_purpose("speech")
    raw = raw if isinstance(raw, dict) else {}
    provider, model, ws = str(raw.get("provider") or "").strip().lower(), str(raw.get("model") or "").strip(), workspace
    options = {k: str(v) for k, v in (raw.get("options") or {}).items()
               if k in purpose.options and isinstance(v, (str, int, float)) and str(v).strip()}
    if not provider:
        entry = effective(workspace).get(purpose.id)
        if not entry:
            raise SpecialModelError("No speech model is chosen.")
        provider, model, ws = entry["provider"], entry["model"], entry.get(INHERITED_KEY) or workspace
        options = {**{k: str(v) for k, v in (entry.get("options") or {}).items()}, **options}
    elif provider_kind(provider) not in purpose.kinds:
        raise SpecialModelError(f"provider '{provider}' cannot serve {purpose.id}")
    if not model:
        raise SpecialModelError("No model is chosen.")
    voice = str(raw.get("voice") or options.get("voice") or "").strip()
    if voice and not VOICE_NAME_RE.match(voice):
        raise SpecialModelError("The voice must be a voice name.")
    lang, text = sample_text(model, voice, language)
    import httpx
    from providers import media
    try:
        audio = media.synthesize_speech(endpoint(provider, ws), model, text, voice=voice or None,
                                        instructions=None, options=options)
    except SpecialModelError:
        raise
    except httpx.ConnectError:
        raise SpecialModelError(f"Could not reach {provider}: connection refused. Is it running?") from None
    except httpx.TimeoutException:
        raise SpecialModelError(f"{provider} did not answer in time.") from None
    except Exception as exc:  # noqa: BLE001 - the form shows the provider's reason
        raise SpecialModelError(f"Speech synthesis failed: {str(exc)[:300]}") from exc
    return audio, lang, text


def discover(purpose_id: str, provider: str, workspace: Optional[str]) -> Dict[str, Any]:
    """Ask ``provider`` which models it has, with this workspace's connection
    settings, and keep those that fit the purpose. Returns ``models`` (the
    purpose's suggestions first, as listed) and ``total``, the count before
    filtering, or raises :class:`SpecialModelError`."""
    purpose = get_purpose(purpose_id)
    if purpose is None:
        raise SpecialModelError(f"unknown purpose '{purpose_id}'")
    pid = str(provider or "").strip().lower()
    kind = provider_kind(pid)
    if kind is None or kind not in purpose.kinds:
        raise SpecialModelError(f"provider '{provider}' cannot serve {purpose.id}")
    listed = _list_models(pid, endpoint(pid, workspace))
    fit = {m["id"] for m in listed if model_fits(purpose.id, kind, m["id"], m.get("methods"))}
    known = [m for m in purpose.suggestions.get(kind, ()) if m in fit]
    out: Dict[str, Any] = {
        "purpose": purpose.id, "provider": pid,
        "models": known + sorted(fit - set(known)),
        "total": len({m["id"] for m in listed}),
    }
    from providers.local_models import HUB_LOCAL_ID
    if pid == HUB_LOCAL_ID and purpose.id == "speech":
        out["voices"] = {str(m.get("name")): [str(v) for v in m.get("voices") or []]
                         for m in _runtime_models() if m.get("name") in fit}
    return out


def _list_models(provider: str, ep: Endpoint, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
    """:func:`providers.media.list_models` with its failures put in words."""
    if provider == "openai" and not ep.api_key:
        raise SpecialModelError("No API key for OpenAI. Set it in Settings (or this workspace's key override).")
    if ep.kind == OPENAI and not ep.base_url:
        raise SpecialModelError(f"Provider '{provider}' has no base URL.")
    import httpx
    from providers import media
    try:
        return media.list_models(ep, timeout=timeout)
    except httpx.ConnectError:
        raise SpecialModelError(f"Could not reach {provider}: connection refused. Is it running?") from None
    except httpx.TimeoutException:
        raise SpecialModelError(f"{provider} did not answer in time.") from None
    except httpx.HTTPError as exc:
        raise SpecialModelError(f"Could not list the models of {provider}: {exc}") from None


# ── connection check ─────────────────────────────────────────────────────────

#: Seconds a check waits for an answer: it asks for a list, not for a picture.
CHECK_TIMEOUT = 15.0


def _chat_endpoint(provider: str, workspace: Optional[str]) -> Endpoint:
    """:func:`endpoint`, plus Anthropic, which a custom chat model may use."""
    if provider != "anthropic":
        return endpoint(provider, workspace)
    from common.config import settings as cfg
    eff = _effective_settings(workspace)
    key = eff.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY") or cfg.anthropic_api_key or ""
    return Endpoint(ANTHROPIC, "https://api.anthropic.com/v1", str(key))


def _check_listed(provider: str, model: str, workspace: Optional[str],
                  purpose: Optional[Purpose]) -> Tuple[str, str]:
    """Reach ``provider`` and look for ``model`` on its list."""
    pid = str(provider or "").strip().lower()
    model = str(model or "").strip()
    if not pid:
        raise SpecialModelError("No provider is chosen.")
    if not model:
        raise SpecialModelError("No model is typed in.")
    ep = _chat_endpoint(pid, workspace)
    listed = _list_models(pid, ep, CHECK_TIMEOUT)
    by_id = {m["id"].lower(): m for m in listed}
    found = by_id.get(model.lower().removeprefix("models/"))
    if found is None:
        return "warn", (f"{pid} answered with {len(by_id)} models, but '{model}' is not among them. "
                        "Check the spelling, or pull the model on a local server.")
    if purpose is not None and not model_fits(purpose.id, ep.kind, found["id"], found.get("methods")):
        return "warn", (f"{pid} has '{model}', but it does not look like a {purpose.id} model. "
                        "It may still work if the provider names it unusually.")
    return "ok", f"{pid} answered and has '{model}'."


def _check_http(item: Dict[str, Any], workspace: Optional[str]) -> Tuple[str, str]:
    """Send a GET to an HTTP model: it proves the address and the token
    without running the model (which takes a POST)."""
    url = str(item.get("url") or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise SpecialModelError("The URL must start with http:// or https://.")
    before = {c["id"]: c for c in stored(workspace).get("custom") or []} if workspace else {}
    old = (before.get(str(item.get("id") or "").strip().lower()) or {}).get("headers") or {}
    raw_headers = item.get("headers") if isinstance(item.get("headers"), dict) else {}
    env_vars: Dict[str, str] = {}
    if workspace:
        try:
            from workspace import get_workspace_metadata
            raw = (get_workspace_metadata(workspace) or {}).get("env_vars") or {}
            env_vars = {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        except Exception:  # noqa: BLE001 - no workspace variables: the process environment decides
            log.debug("special models: no env vars for %s", workspace, exc_info=True)
    import httpx
    from providers import media
    headers = {str(k): media.resolve_header_vars(old.get(k, "") if v == MASK else str(v), env_vars)
               for k, v in raw_headers.items()}
    try:
        with media._client() as client:
            client.timeout = CHECK_TIMEOUT
            code = client.get(url, headers=headers).status_code
    except httpx.ConnectError:
        raise SpecialModelError("Could not reach the URL: connection refused or the host is unknown.") from None
    except httpx.TimeoutException:
        raise SpecialModelError("The URL did not answer in time.") from None
    except httpx.HTTPError as exc:
        raise SpecialModelError(f"Could not reach the URL: {exc}") from None
    if code in (401, 403):
        return "error", f"The URL answered {code}: the token in the headers was not accepted."
    if code == 404:
        return "warn", "The URL answered 404: the server is up, check the path."
    if code >= 500:
        return "warn", f"The URL answered {code}: the server is up but failing."
    return "ok", f"The URL answered {code}: reachable, and the headers were accepted."


def check(raw: Any, workspace: Optional[str]) -> Dict[str, Any]:
    """Whether a model the form holds can be reached, without running it (a
    run costs money). A provider is asked for its model list with the
    workspace's connection settings, and the model must be on it; an HTTP
    model is sent a GET. Takes ``{"purpose", "provider", "model"}`` (no
    provider checks what a personal workspace inherits) or ``{"custom":
    {...}}``, unsaved values included. Returns ``status``: ``ok``, ``warn``
    (reached, but something looks off) or ``error``, with ``message``."""
    import time
    started = time.monotonic()
    status, message = "error", ""
    try:
        if not isinstance(raw, dict):
            raise SpecialModelError("Nothing to check.")
        if isinstance(raw.get("custom"), dict):
            item = raw["custom"]
            if item.get("kind") == "http":
                status, message = _check_http(item, workspace)
            else:
                status, message = _check_listed(item.get("provider"), item.get("model"), workspace, None)
        else:
            purpose = get_purpose(raw.get("purpose"))
            if purpose is None:
                raise SpecialModelError(f"unknown purpose '{raw.get('purpose')}'")
            provider, model, ws = raw.get("provider"), raw.get("model"), workspace
            if not str(provider or "").strip():
                entry = effective(workspace).get(purpose.id)
                if not entry:
                    raise SpecialModelError("No model is chosen for this purpose.")
                provider, model, ws = entry["provider"], entry["model"], entry.get(INHERITED_KEY) or workspace
            elif provider_kind(provider) not in purpose.kinds:
                raise SpecialModelError(f"provider '{provider}' cannot serve {purpose.id}")
            status, message = _check_listed(provider, model, ws, purpose)
    except SpecialModelError as exc:
        message = str(exc)
    return {"status": status, "message": message, "elapsed_ms": int((time.monotonic() - started) * 1000)}


def prompt_section(tool_names: List[str], workspace: Optional[str]) -> str:
    """What the agent is told about the special models here: which of its
    tools have a model and which do not. Empty when it holds none of them."""
    config = effective(workspace)
    lines: List[str] = []
    missing: List[str] = []
    for purpose in PURPOSES:
        if purpose.tool not in tool_names:
            continue
        if purpose.id in config:
            entry = config[purpose.id]
            lines.append(f"- `{purpose.tool}`: {purpose.summary} "
                         f"({entry['provider']}/{entry['model']}).")
        else:
            missing.append(f"`{purpose.tool}` ({purpose.id})")
    if CUSTOM_TOOL in tool_names:
        if config.get("custom"):
            lines.append(f"- `{CUSTOM_TOOL}` with `model` set to one of these:")
            for item in config["custom"]:
                lines.append(f"  - `{item['id']}`: {item['description']}")
        else:
            missing.append(f"`{CUSTOM_TOOL}` (the workspace's own models)")
    if not lines and not missing:
        return ""
    text = (
        "## Special models in this workspace\n"
        "These tools run models the workspace chose for work you do not do in text. "
        "When the user wants a picture, a video, audio or a transcript, or a task matches a "
        "workspace model below, use the tool instead of describing the result or writing code "
        "that calls an API. Each tool saves what it makes as a workspace file and returns its id "
        "and link; mention the file in your answer."
    )
    if lines:
        text += "\n" + "\n".join(lines)
    if missing:
        text += (
            "\n\nNo model is added in this workspace for: " + ", ".join(missing) + ". "
            "Such a call returns an error. When the user needs one of these, say plainly that "
            "the model is not added and that it is added on the Models page, Special models tab "
            "(or in the workspace settings); do not imitate the result another way."
        )
    return text


__all__ = [
    "PURPOSES", "Purpose", "META_KEY", "OPENAI", "GOOGLE",
    "CUSTOM_TOOL", "CUSTOM_KINDS", "SPECIAL_MODEL_TOOLS", "SpecialModelError", "Endpoint",
    "get_purpose", "purpose_of_tool", "provider_kind", "provider_choices", "endpoint",
    "normalize", "stored", "save", "effective", "entry_endpoint", "INHERITED_KEY", "masked", "MASK", "configured_tools", "options_payload",
    "prompt_section", "model_fits", "discover", "check", "CHECK_TIMEOUT",
]
