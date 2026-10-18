"""
Voice models: the speech and transcription models the assistant's voice uses.

A chat model answers in text. To listen and to speak the assistant needs two
more models with their own APIs: a *transcription* model that turns a
recording into text and a *speech* model that reads an answer aloud. Both are
chosen once for the hub, on the Models page (Voice tab), and every voice call
of the assistant (dashboard/backend/routes/assistant.py) and the first run's
voice step (common/setup_ops.py ``voice_cloud``) use them. Without one the
page falls back to the browser's own recognition or speech.

Stored in the ``default`` workspace's metadata under :data:`META_KEY`::

    {
      "speech":        {"provider": "openai", "model": "gpt-4o-mini-tts",
                        "price_usd": 0.015, "options": {"voice": "alloy"},
                        "languages": {"ru": {"voice": "nova"}}},
      "transcription": {"provider": "openai", "model": "gpt-4o-mini-transcribe",
                        "price_usd": 0.003}
    }

``languages`` is a voice per language (providers/speech_languages.py).
``price_usd`` is what one unit costs (:attr:`Purpose.unit`); each call is
charged to its run as a voice call (chat/voice.py ``call_entry``).

An installation from before keeps the entries it stored under the old key
(``special_models``, which also held image and video models): they are read
until the Voice tab saves under the new one.

The HTTP calls are here too: two API shapes are spoken. The OpenAI one
(``/audio/speech``, ``/audio/transcriptions``) covers OpenAI itself, a local
server that mirrors it (Ollama, LM Studio, a whisper server) and any custom
backend with the openai adapter; Google's Gemini API covers Gemini speech and
Gemini transcription. Calls are synchronous and raise :class:`VoiceModelError`
with the provider's own message when they fail.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
import wave
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: Workspace metadata key, on the ``default`` workspace.
META_KEY = "voice_models"
#: Where the entries lived before (read only, see the module docstring).
LEGACY_META_KEY = "special_models"
#: The workspace whose metadata holds the configuration.
HOME = "default"

#: How a provider's API is spoken: the OpenAI shape or Google's Gemini API.
OPENAI = "openai_compat"
GOOGLE = "google"

#: Seconds one HTTP request may take.
REQUEST_TIMEOUT = float(os.environ.get("AGENTS_HUB_MEDIA_TIMEOUT", "300") or 300)


@dataclass(frozen=True)
class Purpose:
    id: str
    #: What ``price_usd`` is the price of.
    unit: str
    #: API shapes that can serve the purpose.
    kinds: Tuple[str, ...]
    #: Model ids to suggest per API shape (the field stays free text).
    suggestions: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    #: Options the purpose accepts, with a short hint each.
    options: Dict[str, str] = field(default_factory=dict)
    #: Known values of the ``voice`` option per API shape (the field stays free text).
    voices: Dict[str, Tuple[str, ...]] = field(default_factory=dict)


PURPOSES: Tuple[Purpose, ...] = (
    Purpose(
        "speech", "1k_chars", (OPENAI, GOOGLE),
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
        "transcription", "call", (OPENAI, GOOGLE),
        {OPENAI: ("gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"),
         GOOGLE: ("gemini-2.5-flash",)},
        {"language": "ISO code such as en or ru, when known"},
    ),
)

_BY_ID: Dict[str, Purpose] = {p.id: p for p in PURPOSES}


class VoiceModelError(ValueError):
    """A configuration that cannot be stored or a call that cannot be made."""


def get_purpose(purpose_id: str) -> Optional[Purpose]:
    return _BY_ID.get(str(purpose_id or "").strip())


# ── providers ────────────────────────────────────────────────────────────────

def provider_kind(provider: str) -> Optional[str]:
    """The API shape ``provider`` speaks, or None when it has no voice models
    (Anthropic has none)."""
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
        log.debug("voice models: could not list custom backends", exc_info=True)
    return out


@dataclass
class Endpoint:
    kind: str
    base_url: str
    api_key: str = ""
    headers: Dict[str, str] = field(default_factory=dict)


def endpoint(provider: str) -> Endpoint:
    """Where and how to call ``provider``: the hub's key and base URL, as for
    the agent's model (Settings)."""
    pid = str(provider or "").strip().lower()
    from common.config import settings as cfg
    if pid == "openai":
        key = os.environ.get("OPENAI_API_KEY") or cfg.openai_api_key or ""
        base = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        return Endpoint(OPENAI, str(base).rstrip("/"), str(key))
    if pid == "google":
        key = os.environ.get("GOOGLE_API_KEY") or cfg.google_api_key or ""
        return Endpoint(GOOGLE, "https://generativelanguage.googleapis.com/v1beta", str(key))
    if pid in ("ollama", "lmstudio"):
        from common.hostnet import host_service_url
        if pid == "ollama":
            raw = os.environ.get("OLLAMA_BASE_URL") or cfg.ollama_base_url
        else:
            raw = os.environ.get("LMSTUDIO_BASE_URL") or cfg.lmstudio_base_url
        base = host_service_url(str(raw or "")).rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        return Endpoint(OPENAI, base, "local")
    from providers.registry import get_backend
    backend = get_backend(pid)
    if backend is None:
        raise VoiceModelError(f"Provider '{provider}' is not configured.")
    if str(backend.get("adapter") or "openai") != "openai":
        raise VoiceModelError(f"Provider '{provider}' does not speak the OpenAI API.")
    headers = backend.get("headers") if isinstance(backend.get("headers"), dict) else {}
    headers = {str(k): str(v) for k, v in headers.items()}
    return Endpoint(OPENAI, str(backend.get("base_url") or "").rstrip("/"),
                    str(backend.get("api_key") or ""), headers)


# ── configuration ────────────────────────────────────────────────────────────

def _price(value: Any, where: str) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        raise VoiceModelError(f"{where}: price must be a number of US dollars") from None
    if price < 0 or price > 1000:
        raise VoiceModelError(f"{where}: price must be between 0 and 1000 dollars")
    return price


def _options(raw: Any, allowed: Dict[str, str]) -> Dict[str, str]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise VoiceModelError("options must be an object")
    out: Dict[str, str] = {}
    for key, value in raw.items():
        k = str(key).strip()
        if not k or value in (None, ""):
            continue
        if k not in allowed:
            raise VoiceModelError(f"unknown option '{k}'; known: {', '.join(allowed) or 'none'}")
        out[k] = str(value).strip()[:200]
    return out


def _normalize_purpose(purpose: Purpose, raw: Any) -> Optional[Dict[str, Any]]:
    if raw in (None, "", {}):
        return None
    if not isinstance(raw, dict):
        raise VoiceModelError(f"{purpose.id}: expected an object")
    provider = str(raw.get("provider") or "").strip().lower()
    model = str(raw.get("model") or "").strip()
    if not provider and not model:
        return None
    if not provider or not model:
        raise VoiceModelError(f"{purpose.id}: both provider and model are needed")
    kind = provider_kind(provider)
    if kind is None:
        raise VoiceModelError(f"{purpose.id}: provider '{provider}' has no {purpose.id} models here")
    if kind not in purpose.kinds:
        raise VoiceModelError(f"{purpose.id}: provider '{provider}' cannot serve this purpose")
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
            raise VoiceModelError(f"{purpose.id}: temperature must be a number from 0.1 to 1.5")
        options["temperature"] = f"{value:g}"
    if options:
        out["options"] = options
    if purpose.id == "speech" and raw.get("languages"):
        # A voice per language (providers/speech_languages.py).
        from providers import speech_languages
        try:
            languages = speech_languages.normalize(raw.get("languages"))
        except speech_languages.SpeechLanguageError as exc:
            raise VoiceModelError(str(exc)) from None
        if languages:
            out["languages"] = languages
    return out


def normalize(raw: Any) -> Dict[str, Any]:
    """A configuration as it is stored, or :class:`VoiceModelError`."""
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise VoiceModelError("voice_models must be an object")
    unknown = [k for k in raw if k not in _BY_ID]
    if unknown:
        raise VoiceModelError(f"unknown purpose(s): {', '.join(map(str, unknown))}")
    out: Dict[str, Any] = {}
    for purpose in PURPOSES:
        entry = _normalize_purpose(purpose, raw.get(purpose.id))
        if entry:
            out[purpose.id] = entry
    return out


def stored() -> Dict[str, Any]:
    """The voice models the hub uses: ``{"speech": {...}, "transcription": {...}}``,
    each purpose present only when it has a model."""
    try:
        from workspace import get_workspace_metadata
        meta = get_workspace_metadata(HOME) or {}
    except Exception:  # noqa: BLE001 - unreadable metadata configures nothing
        log.debug("voice models: could not read the %s workspace", HOME, exc_info=True)
        return {}
    raw = meta.get(META_KEY)
    if not isinstance(raw, dict):
        legacy = meta.get(LEGACY_META_KEY)
        raw = {k: legacy.get(k) for k in _BY_ID if k in legacy} if isinstance(legacy, dict) else {}
    try:
        return normalize(raw)
    except VoiceModelError:
        log.warning("voice models: the stored configuration is invalid")
        return {}


def save(raw: Any) -> Dict[str, Any]:
    """Validate and store the configuration; returns it."""
    config = normalize(raw)
    from workspace import create_workspace_folder, get_workspace_folder, update_workspace_metadata
    if not get_workspace_folder(HOME):
        create_workspace_folder(HOME)
    update_workspace_metadata(HOME, {META_KEY: config})
    return config


def entry(purpose: str) -> Optional[Dict[str, Any]]:
    """The model the assistant's voice uses for ``purpose`` (``speech`` or
    ``transcription``), None when none is chosen."""
    return stored().get(purpose) or None


def options_payload() -> Dict[str, Any]:
    """Purposes, providers and model suggestions, for the Voice tab."""
    return {
        "purposes": [
            {"id": p.id, "unit": p.unit, "kinds": list(p.kinds),
             "suggestions": {k: list(v) for k, v in p.suggestions.items()},
             "options": dict(p.options), "voices": {k: list(v) for k, v in p.voices.items()}}
            for p in PURPOSES
        ],
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
        generates = "generateContent" in list(methods or [])
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


def voices_for(purpose_id: str, provider: str, model: str) -> Dict[str, Any]:
    """The voices the form offers for one model: the ones known for the
    provider's API shape. A cloud voice speaks whatever language it is
    given, so no voice carries a language of its own; the keys stay so the
    form reads one shape."""
    purpose = get_purpose(purpose_id)
    if purpose is None:
        raise VoiceModelError(f"unknown purpose '{purpose_id}'")
    pid, model = str(provider or "").strip().lower(), str(model or "").strip()
    kind = provider_kind(pid)
    if kind is None or kind not in purpose.kinds:
        raise VoiceModelError(f"provider '{provider}' cannot serve {purpose.id}")
    voices = list(purpose.voices.get(kind, ()))
    return {"purpose": purpose.id, "provider": pid, "model": model, "voices": voices,
            "own": False, "language": None, "languages": {}, "options": []}


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


def sample_text(language: Optional[str]) -> Tuple[str, str]:
    """``(language, text)`` of a voice sample: in ``language`` (the page's),
    else in English. A cloud voice speaks every language the same."""
    lang = str(language or "").strip().lower()[:2]
    lang = lang if lang in SAMPLE_TEXTS else "en"
    return lang, SAMPLE_TEXTS[lang]


def voice_sample(raw: Any, language: Optional[str] = None) -> Tuple["Media", str, str]:
    """Read :func:`sample_text` with a speech model the form holds (saved or
    not): ``{"provider", "model", "voice", "options"}``; no provider reads it
    with the stored one. Returns ``(Media, language, text)``, or raises
    :class:`VoiceModelError` with the provider's reason."""
    purpose = _BY_ID["speech"]
    raw = raw if isinstance(raw, dict) else {}
    provider, model = str(raw.get("provider") or "").strip().lower(), str(raw.get("model") or "").strip()
    options = {k: str(v) for k, v in (raw.get("options") or {}).items()
               if k in purpose.options and isinstance(v, (str, int, float)) and str(v).strip()}
    if not provider:
        chosen = entry(purpose.id)
        if not chosen:
            raise VoiceModelError("No speech model is chosen.")
        provider, model = chosen["provider"], chosen["model"]
        options = {**{k: str(v) for k, v in (chosen.get("options") or {}).items()}, **options}
    elif provider_kind(provider) not in purpose.kinds:
        raise VoiceModelError(f"provider '{provider}' cannot serve {purpose.id}")
    if not model:
        raise VoiceModelError("No model is chosen.")
    voice = str(raw.get("voice") or options.get("voice") or "").strip()
    if voice and not VOICE_NAME_RE.match(voice):
        raise VoiceModelError("The voice must be a voice name.")
    lang, text = sample_text(language)
    import httpx
    try:
        audio = synthesize_speech(endpoint(provider), model, text, voice=voice or None, options=options)
    except VoiceModelError:
        raise
    except httpx.ConnectError:
        raise VoiceModelError(f"Could not reach {provider}: connection refused. Is it running?") from None
    except httpx.TimeoutException:
        raise VoiceModelError(f"{provider} did not answer in time.") from None
    except Exception as exc:  # noqa: BLE001 - the form shows the provider's reason
        raise VoiceModelError(f"Speech synthesis failed: {str(exc)[:300]}") from exc
    return audio, lang, text


def discover(purpose_id: str, provider: str) -> Dict[str, Any]:
    """Ask ``provider`` which models it has and keep those that fit the
    purpose. Returns ``models`` (the purpose's suggestions first, as listed)
    and ``total``, the count before filtering, or raises :class:`VoiceModelError`."""
    purpose = get_purpose(purpose_id)
    if purpose is None:
        raise VoiceModelError(f"unknown purpose '{purpose_id}'")
    pid = str(provider or "").strip().lower()
    kind = provider_kind(pid)
    if kind is None or kind not in purpose.kinds:
        raise VoiceModelError(f"provider '{provider}' cannot serve {purpose.id}")
    listed = _list_models(pid, endpoint(pid))
    fit = {m["id"] for m in listed if model_fits(purpose.id, kind, m["id"], m.get("methods"))}
    known = [m for m in purpose.suggestions.get(kind, ()) if m in fit]
    return {
        "purpose": purpose.id, "provider": pid,
        "models": known + sorted(fit - set(known)),
        "total": len({m["id"] for m in listed}),
    }


def _list_models(provider: str, ep: Endpoint, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
    """:func:`list_models` with its failures put in words."""
    if provider == "openai" and not ep.api_key:
        raise VoiceModelError("No API key for OpenAI. Set it in Settings.")
    if ep.kind == OPENAI and not ep.base_url:
        raise VoiceModelError(f"Provider '{provider}' has no base URL.")
    import httpx
    try:
        return list_models(ep, timeout=timeout)
    except httpx.ConnectError:
        raise VoiceModelError(f"Could not reach {provider}: connection refused. Is it running?") from None
    except httpx.TimeoutException:
        raise VoiceModelError(f"{provider} did not answer in time.") from None
    except httpx.HTTPError as exc:
        raise VoiceModelError(f"Could not list the models of {provider}: {exc}") from None


# ── connection check ─────────────────────────────────────────────────────────

#: Seconds a check waits for an answer: it asks for a list, not for audio.
CHECK_TIMEOUT = 15.0


def check(raw: Any) -> Dict[str, Any]:
    """Whether a model the form holds can be reached, without running it (a
    run costs money): the provider is asked for its model list and the model
    must be on it. Takes ``{"purpose", "provider", "model"}``, unsaved values
    included; no provider checks the stored one. Returns ``status``: ``ok``,
    ``warn`` (reached, but something looks off) or ``error``, with ``message``."""
    started = time.monotonic()
    status, message = "error", ""
    try:
        if not isinstance(raw, dict):
            raise VoiceModelError("Nothing to check.")
        purpose = get_purpose(raw.get("purpose"))
        if purpose is None:
            raise VoiceModelError(f"unknown purpose '{raw.get('purpose')}'")
        provider, model = str(raw.get("provider") or "").strip().lower(), str(raw.get("model") or "").strip()
        if not provider:
            chosen = entry(purpose.id)
            if not chosen:
                raise VoiceModelError("No model is chosen for this purpose.")
            provider, model = chosen["provider"], chosen["model"]
        elif provider_kind(provider) not in purpose.kinds:
            raise VoiceModelError(f"provider '{provider}' cannot serve {purpose.id}")
        if not model:
            raise VoiceModelError("No model is typed in.")
        ep = endpoint(provider)
        listed = _list_models(provider, ep, CHECK_TIMEOUT)
        by_id = {m["id"].lower(): m for m in listed}
        found = by_id.get(model.lower().removeprefix("models/"))
        if found is None:
            status, message = "warn", (f"{provider} answered with {len(by_id)} models, but '{model}' is not "
                                       "among them. Check the spelling, or pull the model on a local server.")
        elif not model_fits(purpose.id, ep.kind, found["id"], found.get("methods")):
            status, message = "warn", (f"{provider} has '{model}', but it does not look like a {purpose.id} "
                                       "model. It may still work if the provider names it unusually.")
        else:
            status, message = "ok", f"{provider} answered and has '{model}'."
    except VoiceModelError as exc:
        message = str(exc)
    return {"status": status, "message": message, "elapsed_ms": int((time.monotonic() - started) * 1000)}


# ── HTTP calls ───────────────────────────────────────────────────────────────

_EXT = {
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
    "audio/ogg": "ogg", "audio/opus": "opus", "audio/aac": "aac", "audio/flac": "flac",
}


@dataclass
class Media:
    data: bytes
    mime_type: str
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def extension(self) -> str:
        return _EXT.get(self.mime_type.split(";")[0].strip().lower(), "bin")


def _client(timeout: Optional[float] = None):
    import httpx
    return httpx.Client(timeout=timeout or REQUEST_TIMEOUT, follow_redirects=True)


def _headers(ep: Endpoint, *, json_body: bool = True) -> Dict[str, str]:
    headers: Dict[str, str] = dict(ep.headers or {})
    if ep.kind == GOOGLE:
        if ep.api_key:
            headers["x-goog-api-key"] = ep.api_key
    elif ep.api_key:
        headers.setdefault("Authorization", f"Bearer {ep.api_key}")
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _error_text(response: Any) -> str:
    try:
        body = response.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            return str(err.get("message") or err)[:500]
        if err:
            return str(err)[:500]
        return json.dumps(body)[:500]
    except Exception:  # noqa: BLE001 - not JSON: the raw text
        return (getattr(response, "text", "") or "")[:500]


def _check(response: Any, what: str) -> Any:
    if response.status_code >= 400:
        raise VoiceModelError(f"{what} failed ({response.status_code}): {_error_text(response)}")
    return response


def _need_key(ep: Endpoint, provider_label: str) -> None:
    if not ep.api_key:
        raise VoiceModelError(f"No API key for {provider_label}. Set it in Settings.")


def _google_parts(response_json: Dict[str, Any]) -> list:
    try:
        return response_json["candidates"][0]["content"]["parts"] or []
    except (KeyError, IndexError, TypeError):
        feedback = response_json.get("promptFeedback") if isinstance(response_json, dict) else None
        raise VoiceModelError(
            f"The model returned nothing{': ' + json.dumps(feedback)[:300] if feedback else ''}") from None


def _inline(part: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    data = part.get("inlineData") or part.get("inline_data")
    if isinstance(data, dict) and data.get("data"):
        return str(data.get("mimeType") or data.get("mime_type") or ""), str(data["data"])
    return None


def list_models(ep: Endpoint, *, timeout: Optional[float] = None) -> list:
    """Every model ``ep`` lists, as ``{"id", "methods"}`` (``methods`` are
    Gemini's supportedGenerationMethods, empty for the OpenAI shape).
    ``timeout`` caps each request."""
    out: list = []
    with _client() as client:
        if timeout:
            client.timeout = timeout
        if ep.kind == GOOGLE:
            _need_key(ep, "Google")
            page = ""
            for _ in range(10):
                params = {"pageSize": 1000, **({"pageToken": page} if page else {})}
                body = _check(client.get(f"{ep.base_url}/models", headers=_headers(ep, json_body=False),
                                         params=params), "Listing models").json()
                for m in body.get("models") or []:
                    name = str(m.get("name") or "").removeprefix("models/")
                    if name:
                        out.append({"id": name, "methods": list(m.get("supportedGenerationMethods") or [])})
                page = str(body.get("nextPageToken") or "")
                if not page:
                    break
            return out
        body = _check(client.get(f"{ep.base_url}/models", headers=_headers(ep, json_body=False)),
                      "Listing models").json()
    items = body.get("data") if isinstance(body, dict) else None
    for m in items or []:
        mid = str((m or {}).get("id") or "").strip() if isinstance(m, dict) else ""
        if mid:
            out.append({"id": mid, "methods": []})
    return out


def _pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buf.getvalue()


def synthesize_speech(ep: Endpoint, model: str, text: str, *, voice: Optional[str],
                      options: Dict[str, str], instructions: Optional[str] = None,
                      timeout: Optional[float] = None) -> Media:
    """``text`` read aloud by ``model``, as audio."""
    if ep.kind == GOOGLE:
        _need_key(ep, "Google")
        prompt = f"{instructions.strip()}: {text}" if instructions and instructions.strip() else text
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {
                    "voiceName": voice or options.get("voice") or "Kore"}}},
            },
        }
        with _client(timeout) as client:
            resp = client.post(f"{ep.base_url}/models/{model}:generateContent", headers=_headers(ep), json=body)
            for part in _google_parts(_check(resp, "Speech synthesis").json()):
                got = _inline(part)
                if got:
                    raw = base64.b64decode(got[1])
                    rate = re.search(r"rate=(\d+)", got[0] or "")
                    if "wav" in (got[0] or ""):
                        return Media(raw, "audio/wav")
                    return Media(_pcm_to_wav(raw, int(rate.group(1)) if rate else 24000), "audio/wav")
        raise VoiceModelError("The speech model returned no audio.")
    fmt = (options.get("format") or "mp3").lower()
    payload: Dict[str, Any] = {"model": model, "input": text, "voice": voice or options.get("voice") or "alloy",
                               "response_format": fmt}
    if instructions and instructions.strip() and not model.startswith("tts-1"):
        payload["instructions"] = instructions.strip()
    with _client(timeout) as client:
        resp = _check(client.post(f"{ep.base_url}/audio/speech", headers=_headers(ep), json=payload),
                      "Speech synthesis")
    mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/opus", "aac": "audio/aac",
            "flac": "audio/flac", "pcm": "audio/L16"}.get(fmt, "audio/mpeg")
    data = resp.content
    if fmt == "pcm":
        data, mime = _pcm_to_wav(data), "audio/wav"
    return Media(data, mime)


#: What Whisper writes for a recording with no speech in it: the subtitle
#: credits and video sign-offs of its training data. A sentence matching one of
#: these is never what a person said into the microphone.
_PHANTOM_CREDITS = re.compile(
    r"субтитр\w*\s*(?::|сделал|создавал|делал|подготовил|подогнал|выполнил|by\b)|редактор субтитров"
    r"|корректор\s+[а-я]\.|dimatorzok|amara\.org|untertitel(?:ung)?\s+(?:im auftrag|des|von|der)\b"
    r"|im auftrag des zdf|(?:subtitles|captions) by|transcribed by|www\.\S+\.(?:com|ru|de|org)",
    re.IGNORECASE)
#: Sign-offs that are phantoms only when they are all there is.
_PHANTOM_ALONE = re.compile(
    r"^(?:продолжение следует|спасибо за (?:просмотр|внимание)|подпис\w* на (?:канал|наш канал)"
    r"|thanks? (?:you )?for watching|vielen dank (?:fürs|für's) zuschauen|bis zum nächsten mal)[.!…]*$",
    re.IGNORECASE)


def drop_phantom_text(text: str) -> str:
    """``text`` without the sentences Whisper makes up for silence: what is
    left is what was said, ``""`` when nothing was."""
    sentences = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", str(text or "").strip()) if p.strip()]
    kept = [p for p in sentences if not _PHANTOM_CREDITS.search(p)]
    if len(kept) == 1 and _PHANTOM_ALONE.match(kept[0]):
        kept = []
    return " ".join(kept)


def transcribe(ep: Endpoint, model: str, audio: Tuple[str, bytes, str], *, language: Optional[str],
               prompt: Optional[str] = None) -> str:
    """The text of a recording ``(name, bytes, mime)``."""
    name, data, mime = audio
    if ep.kind == GOOGLE:
        _need_key(ep, "Google")
        ask = "Transcribe this recording verbatim. Return only the transcript."
        if language:
            ask += f" The language is {language}."
        if prompt:
            ask += f" Context: {prompt}"
        body = {"contents": [{"parts": [
            {"text": ask},
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
        ]}]}
        with _client() as client:
            resp = client.post(f"{ep.base_url}/models/{model}:generateContent", headers=_headers(ep), json=body)
            parts = _google_parts(_check(resp, "Transcription").json())
        return drop_phantom_text("".join(str(p.get("text") or "") for p in parts))
    form: Dict[str, str] = {"model": model, "response_format": "json"}
    if language:
        form["language"] = language
    if prompt:
        form["prompt"] = prompt
    with _client() as client:
        resp = client.post(f"{ep.base_url}/audio/transcriptions", headers=_headers(ep, json_body=False),
                           data=form, files={"file": (name, data, mime)})
        body = _check(resp, "Transcription")
    try:
        return drop_phantom_text(str(body.json().get("text") or ""))
    except ValueError:
        return drop_phantom_text(body.text)


__all__ = [
    "PURPOSES", "Purpose", "META_KEY", "LEGACY_META_KEY", "OPENAI", "GOOGLE", "VoiceModelError", "Endpoint",
    "Media", "get_purpose", "provider_kind", "provider_choices", "endpoint", "normalize", "stored", "save",
    "entry", "options_payload", "model_fits", "voices_for", "sample_text", "voice_sample", "discover", "check",
    "list_models", "synthesize_speech", "transcribe", "drop_phantom_text", "SAMPLE_TEXTS", "VOICE_NAME_RE",
    "CHECK_TIMEOUT",
]
