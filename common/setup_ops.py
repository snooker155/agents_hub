"""
What the assistant can set up in the hub during the guided setup
(common/setup_guide.py), behind one tool (``setup_step``, tools/setup_guide.py).

Four operations, the ones `ah setup` makes in the terminal and the rest of a
first setup that needs no secret: choose the default model, give the assistant
a cloud voice, give it a voice from the hub's own model runtime, seed the demo
workspace. A key is never one of them: it is typed into a connection card
(connectors/proposals.py, kind ``provider``).

**Every call waits for a yes.** ``setup_step`` is on the list of tools that ask
on every call (tools/approval.py ``ALWAYS_GATED``), and its card says what the
call would change in a sentence from :func:`describe`. Each operation is the
install's own configuration, so it needs an administrator (anyone outside
``multi`` mode) and is written to the audit log as ``setup.<operation>``.

**What they write is what the pages write.** The default model goes into the
Models catalog (starred and enabled) and ``.env`` (``DEFAULT_PROVIDER`` and the
provider's model, as `ah setup` does), and applies at once
(common/provider_env.py); a voice goes into the ``default`` workspace's special
models, which every personal workspace falls back to.

**A download goes on without the turn.** ``voice_local`` saves the models at
once and leaves the engines and downloads to the runtime as jobs; the guide
carries them as its ``work`` and :func:`advance_work` moves them on whenever
the guide is read (the setup panel polls it, the assistant reads it).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from cli.onboard import probe as P
from cli.onboard.presets import HUB_LOCAL, VOICE_CLOUD, VOICE_LOCAL_SPEECH, VOICE_LOCAL_TRANSCRIPTION

log = logging.getLogger(__name__)

#: How long the runtime may take to come up before the voice step says so.
START_GRACE_SECONDS = 20 * 60
#: A provider's model list is asked again after this long.
LISTING_TTL_SECONDS = 600

_listings: Dict[str, tuple] = {}


class SetupOpError(ValueError):
    """A call the hub refuses, with a short machine code; nothing was changed."""

    def __init__(self, message: str, code: str = "bad_request") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Operation:
    name: str
    #: The guide step it completes.
    step: str
    summary: str
    #: Argument -> hint, for the tool's description.
    params: Dict[str, str] = field(default_factory=dict)


OPERATIONS: Dict[str, Operation] = {op.name: op for op in (
    Operation("choose_model", "default_model",
              "make a model the hub's default, for every workspace without its own",
              {"provider": "openai, anthropic, google, ollama, lmstudio or a custom backend id",
               "model": "the model id, from setup_guide options"}),
    Operation("voice_cloud", "voice",
              "give the assistant a cloud voice: speech and transcription models of a connected provider",
              {"provider": "openai or google (its key must be connected)",
               "voice": "optional: one of the voices setup_guide options lists"}),
    Operation("voice_local", "voice",
              "give the assistant a voice from the hub's own model runtime (free; downloads 0.5 to 2 GB)",
              {"speech": "optional: a speech preset id from setup_guide options (default by language)",
               "transcription": "optional: whisper-small (default) or whisper-turbo"}),
    Operation("seed_demo", "demo", "add the demo workspace: four agents with chats, views, a team and a pulse"),
)}


# ── who and what is there ─────────────────────────────────────────────────────

def _require_admin(principal: Any) -> None:
    from common import setup_guide
    if not setup_guide.is_admin(principal):
        raise SetupOpError("Only an administrator sets up the hub itself; this person is not one.",
                           code="forbidden")


def _audit(principal: Any, op: str, details: Dict[str, Any]) -> None:
    try:
        from common import audit
        from common.auth import LOCAL_PRINCIPAL
        audit.record(f"setup.{op}", principal=principal or LOCAL_PRINCIPAL, object_type="setup",
                     object_id=op, workspace="default", details={"via": "setup_step", **details})
    except Exception:  # noqa: BLE001 - the change stands; the audit trail is best effort here
        log.warning("setup: audit of %s failed", op, exc_info=True)


def _key_of(provider: str) -> str:
    from common import provider_env
    var = P.KEY_VAR.get(provider)
    return provider_env.live_value(var) if var else ""


def listing(provider: str) -> P.ProbeResult:
    """What ``provider`` serves with the settings in force, cached a while."""
    from common import provider_env
    url_var = P.URL_VAR.get(provider)
    base = provider_env.live_value(url_var) if url_var else ""
    cache_key = f"{provider}|{provider_env.stamp()}"
    hit = _listings.get(cache_key)
    if hit and time.time() - hit[0] < LISTING_TTL_SECONDS:
        return hit[1]
    result = P.probe(provider, _key_of(provider), base, timeout=8.0)
    if result.ok:
        _listings[cache_key] = (time.time(), result)
    return result


def _custom_backend(provider: str) -> Optional[Dict[str, Any]]:
    try:
        from providers import get_backend
        return get_backend(provider)
    except Exception:  # noqa: BLE001
        return None


def options() -> Dict[str, Any]:
    """The choices the assistant offers, read now: each connected provider's
    model tiers, the voices, the web search providers, whether the demo and
    the hub's runtime are there. Names only, never a key."""
    from common import setup_guide
    usable = setup_guide.usable_providers()
    models: Dict[str, Any] = {}
    for provider in usable:
        if provider in P.PROVIDERS:
            found = listing(provider)
            tiers = P.presets(provider, found.models)
            models[provider] = {
                "listed": found.ok, "error": found.error if not found.ok else "",
                "count": len(found.models),
                "tiers": {t: {"model": m, "verified": ok, "label": P.TIER_LABELS.get(t, t)}
                          for t, (m, ok) in tiers.items()},
            }
        else:
            backend = _custom_backend(provider) or {}
            models[provider] = {"listed": False, "custom": True,
                                "default_model": backend.get("default_model") or ""}
    runtime = False
    try:
        from providers import local_models as lm
        runtime = lm.runtime_configured()
    except Exception:  # noqa: BLE001
        log.debug("setup options: runtime unreadable", exc_info=True)
    return {
        "default_model": setup_guide.default_model(),
        "models": models,
        "voice": {
            "cloud": {p: {"speech": s["speech"], "transcription": s["transcription"], "voices": s["voices"],
                          "connected": bool(_key_of(p))} for p, s in VOICE_CLOUD.items()},
            "local": {"available": runtime,
                      "speech": [{"id": i, "label": label, "size": size} for i, label, size, *_ in VOICE_LOCAL_SPEECH],
                      "transcription": [{"id": i, "label": label, "size": size}
                                        for i, label, size, *_ in VOICE_LOCAL_TRANSCRIPTION],
                      "default_speech": _local_speech_default(), "default_transcription": "whisper-small"},
            "browser": "without models the page uses the browser's own voice (Chrome, Edge, Safari; not Firefox)",
        },
        "web_search": ["brave", "tavily", "exa"],
        "providers_to_connect": [p for p in P.CLOUD if p not in usable],
    }


def _local_speech_default() -> str:
    lang = (os.environ.get("AGENTS_HUB_LANGUAGE") or os.environ.get("LC_ALL") or os.environ.get("LANG") or "").lower()
    return "piper-ru" if lang.startswith("ru") else "piper-de" if lang.startswith("de") else "piper-en"


# ── describing a call (the card's sentence) ───────────────────────────────────

def _args(raw: Optional[Dict[str, Any]]) -> Dict[str, str]:
    return {str(k): str(v).strip() for k, v in (raw or {}).items() if v not in (None, "")}


def describe(operation: str, args: Optional[Dict[str, Any]] = None) -> str:
    a = _args(args)
    if operation == "choose_model":
        return (f"Make {a.get('provider', '?')}/{a.get('model', '?')} the hub's default model: every "
                f"workspace without a model of its own uses it from the next message.")
    if operation == "voice_cloud":
        spec = VOICE_CLOUD.get(a.get("provider", ""), {})
        voice = a.get("voice") or (spec.get("voices") or ["?"])[0]
        return (f"Give the assistant {a.get('provider', '?')}'s voice: speech {spec.get('speech', '?')} "
                f"(voice {voice}) and transcription {spec.get('transcription', '?')}, paid per use, "
                f"for every workspace that has none of its own.")
    if operation == "voice_local":
        speech = _preset(VOICE_LOCAL_SPEECH, a.get("speech") or _local_speech_default())
        hear = _preset(VOICE_LOCAL_TRANSCRIPTION, a.get("transcription") or "whisper-small")
        return (f"Give the assistant a voice from the hub's own runtime: {speech[1] if speech else '?'} "
                f"({speech[2] if speech else '?'}) and {hear[1] if hear else '?'} ({hear[2] if hear else '?'}), "
                f"downloaded in the background, free to use.")
    if operation == "seed_demo":
        return "Add the demo workspace with four agents, their chats, views, a team and a pulse."
    return ""


def _preset(table: List[tuple], wanted: str) -> Optional[tuple]:
    return next((row for row in table if row[0] == wanted), None)


# ── the operations ────────────────────────────────────────────────────────────

def _models_route():
    try:
        from routes import models as m
    except ImportError:
        from dashboard.backend.routes import models as m
    return m


def choose_model(provider: str, model: str, *, principal: Any = None, check: bool = True) -> Dict[str, Any]:
    """Star and enable ``model`` in the catalog and make it the global default."""
    from common import provider_env
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    if not provider or not model:
        raise SetupOpError("Name the provider and the model (setup_guide options lists them).", code="missing")
    custom = None if provider in P.PROVIDERS else _custom_backend(provider)
    if provider not in P.PROVIDERS and custom is None:
        raise SetupOpError(f"Unknown provider '{provider}'.", code="unknown_provider")
    if provider in P.CLOUD and not _key_of(provider):
        raise SetupOpError(f"No {P.LABELS[provider]} key is connected yet: offer propose_connection with "
                           f"kind provider first.", code="no_key")
    if check and provider in P.PROVIDERS:
        found = listing(provider)
        if found.ok and found.models and model not in found.models:
            near = [m for m in found.models if m.startswith(model)]
            if not near:
                raise SetupOpError(f"{P.LABELS[provider]} does not offer '{model}'. Pick one from setup_guide "
                                   f"options.", code="unknown_model")
            model = sorted(near, reverse=True)[0]
    m = _models_route()
    catalog = m._load_catalog()
    entry = catalog.get(provider) or {"default": "", "models": []}
    have = {x["id"]: x for x in entry.get("models") or []}
    if model in have:
        have[model]["enabled"] = True
    else:
        in_price, out_price, cached = m._default_prices3(provider, model)
        have[model] = {"id": model, "enabled": True, "input_price": in_price, "output_price": out_price,
                       "cached_input_price": cached, "price_source": "auto",
                       "context_window": m._fallback_ctx(provider, model), "released_at": 0}
    entry["models"] = m._sort_models(list(have.values()))
    entry["default"] = model
    catalog[provider] = entry
    m._save_catalog(catalog)
    updates = {"DEFAULT_PROVIDER": provider}
    if provider in P.MODEL_VAR:
        updates[P.MODEL_VAR[provider]] = model
    else:
        from providers import registry
        registry.upsert_backend({**(custom or {}), "default_model": model})
    provider_env.save(updates)
    _audit(principal, "choose_model", {"provider": provider, "model": model})
    return {"ok": True, "provider": provider, "model": model,
            "summary": f"The default model is now {provider}/{model}."}


def voice_cloud(provider: str, voice: str = "", *, principal: Any = None) -> Dict[str, Any]:
    provider = (provider or "").strip().lower()
    spec = VOICE_CLOUD.get(provider)
    if spec is None:
        raise SetupOpError(f"A cloud voice comes from {' or '.join(VOICE_CLOUD)}.", code="unknown_provider")
    if not _key_of(provider):
        raise SetupOpError(f"No {P.LABELS[provider]} key is connected: offer propose_connection with kind "
                           f"provider first, or the hub's own runtime (voice_local).", code="no_key")
    voice = (voice or "").strip() or spec["voices"][0]
    known = {v.lower(): v for v in spec["voices"]}
    if voice.lower() not in known:
        raise SetupOpError(f"'{voice}' is not one of {provider}'s voices: {', '.join(spec['voices'])}.",
                           code="unknown_voice")
    entries = {
        "speech": {"provider": provider, "model": spec["speech"], "options": {"voice": known[voice.lower()]},
                   **({"price_usd": spec["speech_price"]} if spec.get("speech_price") is not None else {})},
        "transcription": {"provider": provider, "model": spec["transcription"], "options": {},
                          **({"price_usd": spec["transcription_price"]}
                             if spec.get("transcription_price") is not None else {})},
    }
    _save_voice(entries)
    _audit(principal, "voice_cloud", {"provider": provider, "voice": known[voice.lower()]})
    return {"ok": True, "summary": f"The assistant now hears with {spec['transcription']} and speaks with "
                                   f"{spec['speech']} (voice {known[voice.lower()]}). The page uses them "
                                   f"from the next answer."}


def _save_voice(entries: Dict[str, Any]) -> None:
    from providers import special
    from workspace import create_workspace_folder, get_workspace_folder
    if not get_workspace_folder("default"):
        create_workspace_folder("default")
    special.save("default", {**special.stored("default"), **entries})


def voice_local(speech: str = "", transcription: str = "", *, principal: Any = None) -> Dict[str, Any]:
    from common import setup_guide
    from providers import local_models as lm
    if not lm.runtime_configured():
        raise SetupOpError("This hub has no model runtime (AGENTS_HUB_MODELS_MANAGED is off and "
                           "AGENTS_HUB_MODELS_URL is empty): offer a cloud voice or the browser's own.",
                           code="no_runtime")
    s = _preset(VOICE_LOCAL_SPEECH, (speech or "").strip() or _local_speech_default())
    h = _preset(VOICE_LOCAL_TRANSCRIPTION, (transcription or "").strip() or "whisper-small")
    if s is None:
        raise SetupOpError("speech is one of " + ", ".join(r[0] for r in VOICE_LOCAL_SPEECH), code="unknown_voice")
    if h is None:
        raise SetupOpError("transcription is one of " + ", ".join(r[0] for r in VOICE_LOCAL_TRANSCRIPTION),
                           code="unknown_voice")
    _, s_label, _, s_repo, s_package, s_engine = s
    _, h_label, _, h_repo, h_package = h
    # The runtime's backend first: a special model names it as its provider.
    lm.ensure_hub_local_backend()
    _save_voice({"speech": {"provider": HUB_LOCAL, "model": s_package, "options": {}},
                 "transcription": {"provider": HUB_LOCAL, "model": h_package, "options": {}}})
    setup_guide.set_work(principal, {
        "step": "voice", "kind": "voice_local", "phase": "starting", "started_at": _now(),
        "engines": ["whisper", s_engine],
        "packages": [[h_repo, h_package, h_label], [s_repo, s_package, s_label]],
        "jobs": [], "message": "",
    })
    advance_work(principal)
    _audit(principal, "voice_local", {"speech": s[0], "transcription": h[0]})
    return {"ok": True, "summary": f"The voice is set to {s_label} and {h_label} on the hub's runtime. "
                                   f"The engines and models download in the background (setup_guide status "
                                   f"shows how far); the page uses the browser's voice until they are ready."}


def seed_demo(*, principal: Any = None) -> Dict[str, Any]:
    from common.demo_workspace import demo_status, ensure_demo_workspace
    ensure_demo_workspace()
    counts = demo_status().get("counts") or {}
    _audit(principal, "seed_demo", {})
    return {"ok": True, "summary": f"The demo workspace is there: {counts.get('agents', 0)} agents, "
                                   f"{counts.get('chats', 0)} chats, {counts.get('views', 0)} views."}


_RUNNERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "choose_model": lambda a, p: choose_model(a.get("provider", ""), a.get("model", ""), principal=p),
    "voice_cloud": lambda a, p: voice_cloud(a.get("provider", ""), a.get("voice", ""), principal=p),
    "voice_local": lambda a, p: voice_local(a.get("speech", ""), a.get("transcription", ""), principal=p),
    "seed_demo": lambda a, p: seed_demo(principal=p),
}


def perform(operation: str, args: Optional[Dict[str, Any]] = None, *, principal: Any = None) -> Dict[str, Any]:
    """Run one operation as ``principal``. Raises :class:`SetupOpError`."""
    operation = str(operation or "").strip().lower()
    if operation not in OPERATIONS:
        raise SetupOpError(f"operation must be one of {', '.join(OPERATIONS)}", code="unknown_operation")
    _require_admin(principal)
    out = _RUNNERS[operation](_args(args), principal)
    out["step"] = OPERATIONS[operation].step
    return out


# ── the first model, from the welcome window ─────────────────────────────────

def first_model(provider: str, api_key: str = "", base_url: str = "", *, principal: Any = None) -> Dict[str, Any]:
    """Connect a provider and make its balanced model the default, in one go:
    the one step before the assistant can talk at all. The key is checked
    against the provider first and saved only when it works."""
    from common import provider_env
    _require_admin(principal)
    provider = (provider or "").strip().lower()
    if provider not in P.PROVIDERS:
        raise SetupOpError(f"provider is one of {', '.join(P.PROVIDERS)}", code="unknown_provider")
    api_key, base_url = (api_key or "").strip(), (base_url or "").strip().rstrip("/")
    if provider in P.CLOUD and not api_key:
        raise SetupOpError("Paste the API key.", code="missing")
    if provider in P.LOCAL:
        base_url = base_url or P.DEFAULT_URL[provider]
    found = P.probe(provider, api_key, base_url, timeout=10.0)
    if not found.ok:
        raise SetupOpError(f"{P.LABELS[provider]} did not accept it: {found.error}.", code="rejected")
    tiers = P.presets(provider, found.models)
    model = (tiers.get("balanced") or next(iter(tiers.values()), (None, False)))[0]
    if not model:
        raise SetupOpError(f"{P.LABELS[provider]} answered but lists no model to use"
                           + (": pull one first." if provider in P.LOCAL else "."), code="no_models")
    updates: Dict[str, str] = {}
    if provider in P.KEY_VAR:
        updates[P.KEY_VAR[provider]] = api_key
    if provider in P.URL_VAR and (base_url or provider in P.LOCAL):
        updates[P.URL_VAR[provider]] = base_url
    provider_env.save(updates)
    _listings[f"{provider}|{provider_env.stamp()}"] = (time.time(), found)
    result = choose_model(provider, model, principal=principal, check=False)
    _audit(principal, "first_model", {"provider": provider, "model": model})
    return {**result, "models": len(found.models)}


# ── the voice download, carried on ────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_seconds(iso: Optional[str]) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(str(iso))).total_seconds()
    except (TypeError, ValueError):
        return 0.0


def _in_backend() -> bool:
    """A runner replica or an agent process must not start the runtime: it
    would become that process's child. Only the backend does."""
    from instances import registry
    return not os.environ.get(registry.ENV_INSTANCE_ID) and not os.environ.get("AGENT_RUN_ID")


def advance_work(principal: Any) -> Optional[Dict[str, Any]]:
    """Move the person's voice download on by one look: start the runtime,
    hand it the jobs, read how far they are. Never raises."""
    from common import setup_guide
    state = setup_guide.load_state(principal)
    work = state.get("work")
    if not isinstance(work, dict) or work.get("phase") in ("done", "error"):
        return work if isinstance(work, dict) else None
    try:
        work = _advance(dict(work))
    except Exception as exc:  # noqa: BLE001 - reported on the step, tried again next look
        log.debug("setup: voice work did not advance", exc_info=True)
        work["message"] = str(exc)[:200]
    setup_guide.set_work(principal, work)
    return work


def _advance(work: Dict[str, Any]) -> Dict[str, Any]:
    from providers import local_models as lm
    client = lm.RuntimeClient(timeout=15)
    if work.get("phase") == "starting":
        try:
            have = client.listing()
        except lm.LocalModelError:
            if _age_seconds(work.get("started_at")) > START_GRACE_SECONDS:
                work.update(phase="error", message="The hub's model runtime did not start: the Models page, "
                                                   "Local tab, shows why.")
                return work
            if _in_backend():
                from providers import model_runtime_host as host
                if host.active():
                    host.ensure(explicit=True)
            work["message"] = "starting the hub's model runtime"
            return work
        engines = (have.get("engines") or {})
        models = {m.get("name") for m in have.get("models") or [] if isinstance(m, dict)}
        jobs: List[Dict[str, Any]] = []
        for engine in dict.fromkeys(work.get("engines") or []):
            if not engines.get(engine):
                job = client.install_engine(engine)
                jobs.append({"id": job.get("job_id"), "label": f"Installing the {engine} engine",
                             "status": "running"})
        for repo, package, label in work.get("packages") or []:
            if package not in models:
                job = client.download(repo, package=package)
                jobs.append({"id": job.get("job_id"), "label": f"Downloading {label}", "status": "running"})
        work.update(phase="submitted", jobs=[j for j in jobs if j.get("id")], message="")
    if work.get("phase") == "submitted":
        for job in work.get("jobs") or []:
            if job.get("status") in ("done", "error"):
                continue
            rec = client.job(str(job["id"]))
            job["status"] = rec.get("status") or job.get("status")
            job["percent"] = rec.get("percent") or 0
            if job["status"] == "error":
                job["error"] = str(rec.get("error") or rec.get("message") or "")[:200]
        states = {j.get("status") for j in work.get("jobs") or []}
        if not states - {"done", "error"}:
            failed = "error" in states
            work.update(phase="error" if failed else "done",
                        message="" if failed else "the voice models are ready")
    return work


__all__ = ["OPERATIONS", "Operation", "SetupOpError", "advance_work", "choose_model", "describe",
           "first_model", "listing", "options", "perform", "seed_demo", "voice_cloud", "voice_local"]
