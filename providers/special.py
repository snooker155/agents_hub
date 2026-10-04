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
         "format": "mp3, wav, opus (OpenAI)"},
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
        for b in list_backends():
            if str(b.get("adapter") or "openai") == "openai":
                out.append({"id": b["id"], "label": b.get("label") or b["id"], "kind": OPENAI})
    except Exception:  # noqa: BLE001 - custom backends are optional here
        log.debug("special models: could not list custom backends", exc_info=True)
    return out


@dataclass
class Endpoint:
    kind: str
    base_url: str
    api_key: str = ""
    headers: Dict[str, str] = field(default_factory=dict)


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
    return Endpoint(OPENAI, str(backend.get("base_url") or "").rstrip("/"),
                    str(backend.get("api_key") or ""), {str(k): str(v) for k, v in headers.items()})


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
    if options:
        out["options"] = options
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
    "prompt_section",
]
