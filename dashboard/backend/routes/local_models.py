"""Local models: an external Ollama and the hub's own model runtime
(deploy/models). Feature 5A/5B; see docs/local-models.md.

The logic lives in ``providers/local_models.py``; this module maps it to HTTP.
Two rules shape the answers:

* A status read never fails with a 5xx because a server is down. Ollama not
  running, or the runtime not started, is an ordinary state the page shows,
  so ``GET /ollama`` and ``GET /runtime`` answer 200 with ``ok: false``.
* An action (pull, delete, load, ...) that cannot reach its server is a 502
  with the reason as the detail; a refusal from the server (no such model, a
  loaded file) keeps the server's 4xx.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from common import identity
from providers import local_models as lm

router = APIRouter(prefix="/api/models/local", tags=["local-models"])


def _raise(exc: lm.LocalModelError) -> None:
    code = exc.status_code
    status = code if code is not None and 400 <= code < 500 else 502
    raise HTTPException(status_code=status, detail=str(exc))


def _catalog_seed() -> dict:
    """The Models route's loader: it seeds a first catalog from .env, which a
    bare empty dict would lose."""
    from routes.models import _load_catalog
    return _load_catalog()


# ── Ollama ───────────────────────────────────────────────────────────────────

class PullBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.get("/ollama")
def ollama_status() -> Dict[str, Any]:
    return lm.ollama_models()


@router.post("/ollama/pull")
def ollama_pull(body: PullBody) -> Dict[str, Any]:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    return {"job_id": lm.start_ollama_pull(name)}


@router.get("/ollama/{name:path}/show")
def ollama_show(name: str) -> Dict[str, Any]:
    try:
        return lm.ollama_show(name)
    except lm.LocalModelError as exc:
        _raise(exc)


@router.delete("/ollama/{name:path}")
def ollama_delete(name: str) -> Dict[str, Any]:
    try:
        lm.ollama_delete(name)
    except lm.LocalModelError as exc:
        _raise(exc)
    return {"ok": True}


@router.get("/servers")
def servers() -> Dict[str, Any]:
    """Which local model servers answer right now (Ollama, LM Studio, the
    hub runtime), for the markers in the model catalog. Never an error: a
    server that is down is ``ok: false``."""
    return {"servers": lm.server_status()}


# ── Hub jobs ─────────────────────────────────────────────────────────────────

def _runtime_jobs() -> List[Dict[str, Any]]:
    """The runtime's own jobs (downloads), tagged with where they live; an
    unconfigured or unreachable runtime contributes none rather than an
    error, so the one job list the UI polls always answers."""
    if not lm.runtime_configured():
        return []
    try:
        return [{**j, "source": "runtime"} for j in lm.RuntimeClient().jobs()]
    except lm.LocalModelError:
        return []


@router.get("/jobs")
def list_jobs() -> Dict[str, Any]:
    """Every job the Local tab shows: the hub's own (Ollama pulls, kept in the
    database) and the runtime's (downloads, kept in its jobs file), newest
    first. Both survive a restart; a job that was running through one comes
    back as an error marked ``resumable``."""
    own = [{**j, "source": "hub"} for j in lm.JOBS.list()]
    merged = own + _runtime_jobs()
    merged.sort(key=lambda j: str(j.get("started_at") or ""), reverse=True)
    return {"jobs": merged}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    job = lm.JOBS.get(job_id)
    if job is not None:
        return {**job, "source": "hub"}
    for j in _runtime_jobs():
        if j.get("id") == job_id:
            return j
    raise HTTPException(status_code=404, detail="no such job (finished jobs are kept for a while)")


# ── The hub runtime ──────────────────────────────────────────────────────────

class DownloadBody(BaseModel):
    repo: str = Field(min_length=3, max_length=200)
    #: A GGUF file of the repo, or
    file: str = Field(default="", max_length=300)
    #: a speech model the repo's listing named under ``packages``.
    package: str = Field(default="", max_length=200)
    revision: str = "main"


class LoadBody(BaseModel):
    file: str = Field(min_length=1, max_length=300)
    context_length: int = Field(default=4096, ge=256, le=1_048_576)
    gpu_layers: int = -1
    threads: Optional[int] = Field(default=None, ge=1, le=512)


class UnloadBody(BaseModel):
    file: str = Field(min_length=1, max_length=300)


@router.get("/runtime")
def runtime_status() -> Dict[str, Any]:
    """The runtime's models, engines, memory and call counts. For the runtime
    the hub runs itself, also its state (``managed``), and reading it starts
    the runtime when it is not running and nobody stopped it.

    ``usage`` is every call through the runtime, whoever made it: the hub's
    agents, its /v1 endpoint, the assistant's voice, or a caller with the
    runtime's own token. A runtime older than this count leaves it None with
    ``usage_outdated``, which asks for a restart."""
    cfg = lm.runtime_settings()
    out: Dict[str, Any] = {"configured": bool(cfg["url"]), "ok": False, "url": cfg["url"],
                           "provider": lm.HUB_LOCAL_ID, "models": [], "memory": None,
                           "error": None, "managed": None, "usage": None, "usage_outdated": False}
    if not cfg["url"]:
        return out
    if cfg.get("managed"):
        from providers import model_runtime_host as host
        out["managed"] = host.ensure()
        if out["managed"].get("state") != "running":
            return out
    try:
        lm.ensure_hub_local_backend()
    except Exception as exc:  # noqa: BLE001 - registration trouble must not hide the runtime
        out["error"] = f"Could not register the provider {lm.HUB_LOCAL_ID}: {exc}"
    client = lm.RuntimeClient()
    try:
        listing = client.listing()
        out["models"] = list(listing.get("models") or [])
        # Which speech engines run there; absent from a runtime older than them.
        out["engines"] = listing.get("engines")
        out["memory"] = client.memory()
        out["ok"] = True
    except lm.LocalModelError as exc:
        out["error"] = str(exc)
        return out
    try:
        out["usage"] = client.usage()
    except lm.LocalModelError as exc:
        # Counts are extra: a failure here leaves the runtime reported as up.
        out["usage_outdated"] = exc.status_code == 404
    return out


def _host() -> Any:
    from providers import model_runtime_host as host
    if not host.active():
        raise HTTPException(status_code=409, detail="the hub does not run the model runtime itself here "
                                                    "(AGENTS_HUB_MODELS_URL names another one, or "
                                                    "AGENTS_HUB_MODELS_MANAGED is off)")
    return host


@router.post("/runtime/start")
def runtime_start() -> Dict[str, Any]:
    """Start the runtime the hub runs (the first start also makes its Python
    environment, which takes a minute); answers at once with its state."""
    return _host().ensure(explicit=True)


@router.post("/runtime/stop")
def runtime_stop() -> Dict[str, Any]:
    """Stop it, with every loaded model, and keep it stopped across restarts
    of the hub until it is started again."""
    result = _host().stop()
    try:
        _disable_hub_local_chat_models()
    except Exception:  # noqa: BLE001 - the runtime is stopped; the picker catches up on the next load
        pass
    return result


@router.post("/runtime/restart")
def runtime_restart() -> Dict[str, Any]:
    """Restart it: what was loaded is unloaded, new runtime code takes effect."""
    result = _host().restart()
    try:
        _disable_hub_local_chat_models()
    except Exception:  # noqa: BLE001 - see runtime_stop
        pass
    return result


def _disable_hub_local_chat_models() -> None:
    """Nothing is loaded after a stop: the chat picker stops offering it."""
    from providers.catalog import load_catalog_raw
    for row in ((load_catalog_raw() or {}).get(lm.HUB_LOCAL_ID) or {}).get("models") or []:
        if isinstance(row, dict) and row.get("enabled"):
            lm.catalog_set_enabled(str(row["id"]), False, seed=_catalog_seed)


@router.post("/runtime/download")
def runtime_download(body: DownloadBody) -> Dict[str, Any]:
    if not body.file.strip() and not body.package.strip():
        raise HTTPException(status_code=400, detail="name a file or a package to download")
    try:
        return lm.RuntimeClient().download(body.repo.strip(), body.file.strip(),
                                           body.revision.strip() or "main", package=body.package.strip())
    except lm.LocalModelError as exc:
        _raise(exc)


class OllamaImportBody(BaseModel):
    name: str = Field(min_length=1, max_length=300)


@router.get("/runtime/ollama")
def runtime_ollama_models() -> Dict[str, Any]:
    """What Ollama has on the runtime's machine that the runtime can take
    over without downloading it again."""
    try:
        return lm.RuntimeClient().ollama_models()
    except lm.LocalModelError as exc:
        _raise(exc)


@router.post("/runtime/ollama/import")
def runtime_ollama_import(body: OllamaImportBody) -> Dict[str, Any]:
    """An Ollama model as a runtime file (a hard link, or a copy as a job);
    it is then loaded like any other GGUF."""
    try:
        return lm.RuntimeClient().ollama_import(body.name.strip())
    except lm.LocalModelError as exc:
        _raise(exc)


@router.get("/runtime/lmstudio")
def runtime_lmstudio_models() -> Dict[str, Any]:
    """What LM Studio has on the runtime's machine that the runtime can take
    over: GGUF files, and MLX folders it runs with mlx-lm on Apple silicon."""
    try:
        return lm.RuntimeClient().lmstudio_models()
    except lm.LocalModelError as exc:
        _raise(exc)


@router.post("/runtime/lmstudio/import")
def runtime_lmstudio_import(body: OllamaImportBody) -> Dict[str, Any]:
    try:
        return lm.RuntimeClient().lmstudio_import(body.name.strip())
    except lm.LocalModelError as exc:
        _raise(exc)


@router.get("/runtime/engines")
def runtime_engines() -> Dict[str, Any]:
    try:
        return lm.RuntimeClient().engines()
    except lm.LocalModelError as exc:
        _raise(exc)


@router.post("/runtime/engines/{engine}/install")
def runtime_install_engine(engine: str) -> Dict[str, Any]:
    """Installs a speech engine into the runtime's Python, as a job on the
    shared list."""
    try:
        return lm.RuntimeClient().install_engine(engine)
    except lm.LocalModelError as exc:
        _raise(exc)


@router.get("/runtime/hf/files")
def runtime_hf_files(repo: str, revision: str = "main") -> Dict[str, Any]:
    try:
        return lm.RuntimeClient().hf_files(repo.strip(), revision.strip() or "main")
    except lm.LocalModelError as exc:
        _raise(exc)


@router.post("/runtime/load")
def runtime_load(body: LoadBody) -> Dict[str, Any]:
    try:
        result = lm.RuntimeClient().load(body.file, body.context_length, body.gpu_layers,
                                         body.threads)
    except lm.LocalModelError as exc:
        _raise(exc)
    name = str(result.get("name") or lm.model_name(body.file))
    if result.get("kind") in lm.SPEECH_KINDS:
        # A speech model is not a chat model: it is picked per workspace as a
        # special model, so the chat catalog stays as it was.
        try:
            lm.ensure_hub_local_backend()
        except Exception as exc:  # noqa: BLE001 - the model is loaded; say what failed around it
            result["catalog_error"] = str(exc)
        return result
    try:
        lm.ensure_hub_local_backend()
        # The runtime may have evicted a model to make room; those are no
        # longer served, so they leave the picker too.
        for evicted in result.get("evicted") or []:
            lm.catalog_set_enabled(lm.model_name(evicted), False, seed=_catalog_seed)
        result["catalog"] = lm.catalog_set_enabled(
            name, True, context_window=int(result.get("context_length") or body.context_length),
            seed=_catalog_seed)
    except Exception as exc:  # noqa: BLE001 - the model is loaded; say what failed around it
        result["catalog_error"] = str(exc)
    return result


@router.post("/runtime/unload")
def runtime_unload(body: UnloadBody) -> Dict[str, Any]:
    try:
        result = lm.RuntimeClient().unload(body.file)
    except lm.LocalModelError as exc:
        _raise(exc)
    if result.get("kind") in lm.SPEECH_KINDS:
        return result
    try:
        lm.catalog_set_enabled(lm.model_name(body.file), False, seed=_catalog_seed)
    except Exception as exc:  # noqa: BLE001 - the model is unloaded; report the catalog trouble
        result["catalog_error"] = str(exc)
    return result


@router.delete("/runtime/models/{file}")
def runtime_delete(file: str) -> Dict[str, Any]:
    try:
        return lm.RuntimeClient().delete(file)
    except lm.LocalModelError as exc:
        _raise(exc)


@router.get("/runtime/models/{file}/structure")
def runtime_structure(file: str) -> Dict[str, Any]:
    try:
        return lm.RuntimeClient().structure(file)
    except lm.LocalModelError as exc:
        _raise(exc)


@router.get("/runtime/hf/search")
def runtime_hf_search(q: str = "", purpose: str = "chat", license: str = "any",
                      sort: str = "downloads", limit: int = 20) -> Dict[str, Any]:
    """Models on Hugging Face by text, purpose and license: GGUF models
    (chat, code, embeddings), each with whether it fits this machine and how
    fast it would write per quantization; or speech models (speech,
    transcription) that the runtime's engines run, with their packages."""
    try:
        return lm.RuntimeClient().hf_search(q, purpose, license, sort, limit)
    except lm.LocalModelError as exc:
        _raise(exc)


@router.get("/runtime/hardware")
def runtime_hardware() -> Dict[str, Any]:
    try:
        return lm.RuntimeClient().hardware()
    except lm.LocalModelError as exc:
        _raise(exc)


@router.delete("/runtime/usage")
def runtime_clear_usage() -> Dict[str, Any]:
    """Resets the counts ``GET /runtime`` reports as ``usage``."""
    try:
        return lm.RuntimeClient().clear_usage()
    except lm.LocalModelError as exc:
        _raise(exc)


# ── Prompt cache ─────────────────────────────────────────────────────────────
# The runtime's prompt cache (deploy/models/app.py, "Prompt cache"): one set
# of settings for every local chat model the runtime serves, so changing
# them, reloading models for them and clearing what is kept are an
# administrator's; anyone who sees the Models page sees how it does.

def _admin(request: Optional[Request]) -> None:
    identity.require_role(identity.request_principal(request), admin=True)


@router.get("/runtime/cache")
def runtime_cache() -> Dict[str, Any]:
    """The prompt cache's settings, loaded models, disk and memory. Like
    ``GET /runtime`` it never fails because the runtime is down: ``ok``
    false with ``error``, and ``outdated`` for a runtime older than the cache."""
    try:
        return {"ok": True, **lm.RuntimeClient().cache()}
    except lm.LocalModelError as exc:
        return {"ok": False, "error": str(exc), "outdated": exc.status_code == 404}


@router.put("/runtime/cache/settings")
def runtime_cache_settings(body: Dict[str, Any], request: Request) -> Dict[str, Any]:
    _admin(request)
    try:
        return {"ok": True, **lm.RuntimeClient().set_cache_settings(body)}
    except lm.LocalModelError as exc:
        _raise(exc)


@router.post("/runtime/cache/apply")
def runtime_cache_apply(request: Request) -> Dict[str, Any]:
    """Reload the models that run with older cache settings. They keep their
    names, so the chat catalog stays as it is."""
    _admin(request)
    try:
        return {"ok": True, **lm.RuntimeClient().apply_cache()}
    except lm.LocalModelError as exc:
        _raise(exc)


@router.post("/runtime/cache/warmup")
def runtime_cache_warmup(body: UnloadBody, request: Request) -> Dict[str, Any]:
    _admin(request)
    try:
        return lm.RuntimeClient().warm_cache(body.file)
    except lm.LocalModelError as exc:
        _raise(exc)


@router.delete("/runtime/cache")
def runtime_cache_clear(request: Request, model: Optional[str] = None) -> Dict[str, Any]:
    _admin(request)
    try:
        return {"ok": True, **lm.RuntimeClient().clear_cache(model)}
    except lm.LocalModelError as exc:
        _raise(exc)


@router.get("/runtime/jobs")
def runtime_jobs() -> Dict[str, Any]:
    try:
        return {"jobs": lm.RuntimeClient().jobs()}
    except lm.LocalModelError as exc:
        _raise(exc)


@router.get("/runtime/jobs/{job_id}")
def runtime_job(job_id: str) -> Dict[str, Any]:
    try:
        return lm.RuntimeClient().job(job_id)
    except lm.LocalModelError as exc:
        _raise(exc)


# ── Recorded voices ──────────────────────────────────────────────────────────
# Samples for the runtime's voice cloning engines (Chatterbox, OpenVoice).
# The runtime keeps them all; the hub decides who sees which: a person sees
# their own and the ones shared, an administrator every one, and only the
# one who recorded a voice (or an administrator) changes or removes it.

#: The largest recording taken: a minute of WAV is about 5 MB.
MAX_VOICE_BYTES = 50 * 1024 * 1024


def _viewer(request: Optional[Request]) -> Any:
    return identity.request_principal(request)


def _voice(name: str, viewer: Any, *, edit: bool = False) -> Dict[str, Any]:
    """The recording ``name`` as ``viewer`` may see it (404 otherwise), or
    change it with ``edit`` (403 when it is someone else's)."""
    try:
        record = next((v for v in lm.RuntimeClient().voices() if v.get("name") == name), None)
    except lm.LocalModelError as exc:
        _raise(exc)
    if record is None or not lm.voice_visible(record, viewer):
        raise HTTPException(status_code=404, detail=f"No recorded voice '{name}'.")
    if edit and not lm.voice_editable(record, viewer):
        raise HTTPException(status_code=403, detail="Only the person who recorded this voice can change it.")
    return record


def _shown(record: Dict[str, Any], viewer: Any) -> Dict[str, Any]:
    return {**record, "mine": bool(viewer is not None and record.get("owner") == getattr(viewer, "id", None)),
            "editable": lm.voice_editable(record, viewer)}


@router.get("/runtime/voices")
def runtime_voices(request: Request) -> Dict[str, Any]:
    viewer = _viewer(request)
    try:
        voices = lm.RuntimeClient().voices()
    except lm.LocalModelError as exc:
        _raise(exc)
    return {"voices": [_shown(v, viewer) for v in voices if lm.voice_visible(v, viewer)]}


@router.post("/runtime/voices")
async def runtime_add_voice(request: Request, name: str = Form(...), file: UploadFile = File(...),
                            language: str = Form(""), gender: str = Form(""), shared: bool = Form(False),
                            consent: bool = Form(False), replace: bool = Form(False)) -> Dict[str, Any]:
    """A recording for the cloning engines. ``consent``: the person confirms
    the voice is theirs or its owner allowed it; without it nothing is
    kept. ``replace`` gives a voice of theirs a new sample."""
    import asyncio
    if not consent:
        raise HTTPException(status_code=400, detail="Confirm that this is your voice or that its owner agreed.")
    viewer = _viewer(request)
    if replace:
        await asyncio.to_thread(_voice, name, viewer, edit=True)
    audio = await file.read(MAX_VOICE_BYTES + 1)
    if len(audio) > MAX_VOICE_BYTES:
        raise HTTPException(status_code=413, detail="The recording is larger than 50 MB.")
    if not audio:
        raise HTTPException(status_code=400, detail="The recording is empty.")
    try:
        record = await asyncio.to_thread(
            lm.RuntimeClient().add_voice, name.strip(), audio, file.filename or "voice",
            file.content_type or "application/octet-stream", owner=str(getattr(viewer, "id", "") or ""),
            language=language, gender=gender, shared=shared, replace=replace)
    except lm.LocalModelError as exc:
        _raise(exc)
    return _shown(record, viewer)


class VoicePatch(BaseModel):
    language: Optional[str] = Field(default=None, max_length=8)
    gender: Optional[str] = Field(default=None, max_length=10)
    shared: Optional[bool] = None
    #: The model that reads for OpenVoice in this voice, "" for automatic.
    base_model: Optional[str] = Field(default=None, max_length=120)
    base_voice: Optional[str] = Field(default=None, max_length=120)


@router.patch("/runtime/voices/{name}")
def runtime_update_voice(name: str, body: VoicePatch, request: Request) -> Dict[str, Any]:
    viewer = _viewer(request)
    _voice(name, viewer, edit=True)
    try:
        record = lm.RuntimeClient().update_voice(name, body.model_dump(exclude_none=True))
    except lm.LocalModelError as exc:
        _raise(exc)
    return _shown(record, viewer)


@router.delete("/runtime/voices/{name}")
def runtime_delete_voice(name: str, request: Request) -> Dict[str, Any]:
    _voice(name, _viewer(request), edit=True)
    try:
        return lm.RuntimeClient().delete_voice(name)
    except lm.LocalModelError as exc:
        _raise(exc)


@router.get("/runtime/voices/{name}/audio")
def runtime_voice_audio(name: str, request: Request) -> Response:
    """The recording as it is kept (cleaned up: trimmed, levelled, WAV)."""
    _voice(name, _viewer(request))
    try:
        return Response(content=lm.RuntimeClient().voice_audio(name), media_type="audio/wav")
    except lm.LocalModelError as exc:
        _raise(exc)


class VoiceTryBody(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    text: str = Field(default="", max_length=500)
    #: The page's language, for the line read when ``text`` is empty and the
    #: recording names none.
    language: str = Field(default="", max_length=8)


@router.post("/runtime/voices/{name}/try")
def runtime_try_voice(name: str, body: VoiceTryBody, request: Request) -> Response:
    """A line read in the recorded voice by a cloning model, to hear how
    close it comes: ``text``, else the voice sample line in the recording's
    language. Its time is in ``X-Synthesis-Seconds``."""
    import time
    from providers import special
    record = _voice(name, _viewer(request))
    lang = str(record.get("language") or body.language or "en").lower()[:2]
    text = body.text.strip() or special.SAMPLE_TEXTS.get(lang, special.SAMPLE_TEXTS["en"])
    started = time.monotonic()
    try:
        with lm.runtime_source("voice"):
            audio, media_type = lm.RuntimeClient().speech(body.model, text, name)
    except lm.LocalModelError as exc:
        _raise(exc)
    return Response(content=audio, media_type=media_type,
                    headers={"X-Synthesis-Seconds": f"{time.monotonic() - started:.2f}",
                             "Access-Control-Expose-Headers": "X-Synthesis-Seconds"})

