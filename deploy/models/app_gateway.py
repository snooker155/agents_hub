"""The OpenAI-compatible gateway, usage and cache routes, and the speech routes."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import app_cache
import app_core
import app_engines
import app_serving
import app_speech_models
import app_voices
import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

router = APIRouter()


# ── OpenAI-compatible gateway ────────────────────────────────────────────────

@router.get("/v1/models", dependencies=app_core.auth)
async def v1_models() -> Dict[str, Any]:
    """Loaded chat models, and every speech model that can run here, loaded
    or not (the gateway loads one on its first request). ``kind`` tells them
    apart; the hub keeps speech models out of its chat model picker."""
    app_serving._drop_dead()
    data: List[Dict[str, Any]] = [
        {"id": m.name, "object": "model", "owned_by": "hub-local", "created": 0, "kind": "chat",
         "context_length": m.context_length} for m in app_speech_models.state.loaded.values() if m.engine == "llama"]
    for e in await asyncio.to_thread(app_speech_models.list_models):
        if e.get("kind") in app_speech_models.SPEECH_KINDS and e.get("loadable"):
            data.append({"id": e["name"], "object": "model", "owned_by": "hub-local", "created": 0,
                         "kind": e["kind"], "engine": e["engine"], "loaded": e["loaded"],
                         "voices": e.get("voices") or []})
    return {"object": "list", "data": data}


def _error(status: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {
        "message": message, "type": "invalid_request_error", "code": code}})


def _not_loaded(model: str) -> JSONResponse:
    return _error(404, f"model {model!r} is not loaded in the hub runtime; load it on the Models page "
                       f"(loaded: {', '.join(sorted(app_speech_models.state.loaded)) or 'none'})", "model_not_loaded")


async def _forward(m: app_speech_models.Loaded, path: str, raw: bytes, content_type: str) -> Response:
    """``raw`` as it came, to the model's own server, the answer streamed back."""
    m.touch()
    url = f"http://127.0.0.1:{m.port}{path}"
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0))
    try:
        upstream = await client.send(client.build_request("POST", url, content=raw,
                                                          headers={"content-type": content_type}),
                                     stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        return JSONResponse(status_code=502, content={"error": {"message": f"{m.engine}: {exc}"}})

    async def close() -> None:
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                             media_type=upstream.headers.get("content-type"),
                             background=BackgroundTask(close))


async def _proxy(request: Request, path: str) -> Response:
    started = time.monotonic()
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return app_core.metered(request, path, "", JSONResponse(
            status_code=400, content={"error": {"message": "body is not JSON"}}), started)
    model = str(body.get("model") or "") if isinstance(body, dict) else ""
    streamed = isinstance(body, dict) and bool(body.get("stream"))
    return app_core.metered(request, path, model, await _route_chat(path, raw, body, model), started,
                   streamed=streamed)


async def _route_chat(path: str, raw: bytes, body: Any, model: str) -> Response:
    m = app_speech_models._find_loaded(model) if model else None
    if m is None or m.proc.poll() is not None:
        return _not_loaded(model)
    if m.kind != "chat":
        return _error(400, f"{model!r} is a {m.kind} model; use /v1/audio/"
                           f"{'speech' if m.kind == 'speech' else 'transcriptions'}", "wrong_model_kind")
    if path == "/v1/chat/completions":
        c = app_cache.cache_settings()
        if c["enabled"] and c["warmup"]:
            app_cache.heads.note(m.name, body, c["warmup_prompts"])
    if m.engine == "mlx":
        return await _forward_mlx(m, path, body)
    return await _forward(m, path, raw, "application/json")


async def _forward_mlx(m: app_speech_models.Loaded, path: str, body: Dict[str, Any]) -> Response:
    """A chat call to an MLX model. mlx-lm takes any other ``model`` value as
    a model to fetch from Hugging Face, so it always gets ``default_model``
    (the one it was started with); its answer is brought to llama-server's
    shape on the way back (:func:`mlx_message`)."""
    m.touch()
    payload = {**body, "model": "default_model"}
    # mlx-lm counts a streamed answer's tokens (cached ones included) only
    # when asked; a caller that did not ask gets that chunk as an SSE comment,
    # which clients skip and the gateway's count still reads.
    hide_usage = False
    if body.get("stream") and not (isinstance(body.get("stream_options"), dict)
                                   and body["stream_options"].get("include_usage")):
        payload["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
        hide_usage = True
    url = f"http://127.0.0.1:{m.port}{path}"
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0))
    try:
        upstream = await client.send(client.build_request("POST", url, json=payload), stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        return JSONResponse(status_code=502, content={"error": {"message": f"mlx: {exc}"}})
    name = m.name

    def fix(data: Dict[str, Any], harmony: Optional[app_engines.HarmonyStream], whole: bool) -> Dict[str, Any]:
        if isinstance(data, dict):
            data["model"] = name
            for choice in data.get("choices") or []:
                if isinstance(choice, dict):
                    app_engines.mlx_message(choice.get("message") or choice.get("delta"), harmony, whole=whole)
        return data

    if not body.get("stream") or upstream.status_code >= 400:
        try:
            raw = await upstream.aread()
        finally:
            await upstream.aclose()
            await client.aclose()
        try:
            data = fix(json.loads(raw), None if not m.harmony else app_engines.HarmonyStream(), True)
        except ValueError:
            return Response(content=raw, status_code=upstream.status_code,
                            media_type=upstream.headers.get("content-type"))
        return JSONResponse(status_code=upstream.status_code, content=data)

    harmony = app_engines.HarmonyStream() if m.harmony else None

    async def events() -> Any:
        try:
            async for line in upstream.aiter_lines():
                if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                    try:
                        data = fix(json.loads(line[6:]), harmony, False)
                        line = "data: " + json.dumps(data, ensure_ascii=False)
                        if hide_usage and isinstance(data, dict) and data.get("usage") and not data.get("choices"):
                            line = ": " + json.dumps({"usage": data["usage"]})
                    except ValueError:
                        pass
                yield (line + "\n").encode()
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(events(), status_code=upstream.status_code, media_type="text/event-stream")


@router.post("/v1/chat/completions", dependencies=app_core.auth)
async def v1_chat(request: Request) -> Response:
    return await _proxy(request, "/v1/chat/completions")


@router.post("/v1/completions", dependencies=app_core.auth)
async def v1_completions(request: Request) -> Response:
    return await _proxy(request, "/v1/completions")


@router.post("/v1/embeddings", dependencies=app_core.auth)
async def v1_embeddings(request: Request) -> Response:
    return await _proxy(request, "/v1/embeddings")


@router.get("/usage", dependencies=app_core.auth)
async def get_usage() -> Dict[str, Any]:
    """The gateway's calls since ``since``: ``totals``, ``rows`` per model,
    caller (``source``) and ``kind``, and the latest calls in ``recent``."""
    return app_core.usage.snapshot()


@router.delete("/usage", dependencies=app_core.auth)
async def clear_usage() -> Dict[str, Any]:
    app_core.usage.clear()
    return app_core.usage.snapshot()


class CacheModelBody(BaseModel):
    model: str = Field(min_length=1, max_length=300)


@router.get("/cache", dependencies=app_core.auth)
async def get_cache() -> Dict[str, Any]:
    """The prompt cache: ``settings`` (over ``defaults``), each loaded chat
    model's slots, KV size, restore and warm-up (``models``), what is kept
    for every model on disk (``stored``), the memory it comes out of, and
    which models run with older settings (``pending``)."""
    return await asyncio.to_thread(app_cache.cache_overview)


@router.put("/cache/settings", dependencies=app_core.auth)
async def put_cache_settings(body: Dict[str, Any]) -> Dict[str, Any]:
    """Change some settings; they reach a running model when it is loaded
    again (``POST /cache/apply``). A smaller disk limit applies at once."""
    try:
        settings = app_cache.check_cache_settings(body, app_cache.cache_settings())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    app_cache.save_cache_settings(settings)
    await asyncio.to_thread(app_cache.trim_slot_disk, settings["disk_mib"])
    return await asyncio.to_thread(app_cache.cache_overview)


@router.post("/cache/apply", dependencies=app_core.auth)
async def apply_cache() -> Dict[str, Any]:
    """Load again every chat model whose cache flags differ from the
    settings, with the file, context length, GPU layers and threads it had;
    its slots are saved first and come back if they still fit it."""
    reloaded: List[str] = []
    failed: List[Dict[str, str]] = []
    for m in list(app_speech_models.state.loaded.values()):
        if m.kind != "chat" or tuple(m.cache_flags) == app_cache.cache_flags(m.engine, m.name):
            continue
        async with app_speech_models._lock():
            if app_speech_models.state.loaded.get(m.name) is not m:
                continue
            app_speech_models.state.loaded.pop(m.name, None)
            await app_cache.save_slots(m)
            await asyncio.to_thread(app_serving._stop, m)
        try:
            await app_serving.load_model(m.file, m.context_length or 4096, m.gpu_layers, m.threads)
            reloaded.append(m.name)
        except HTTPException as exc:
            failed.append({"name": m.name, "error": str(exc.detail)})
    return {**await asyncio.to_thread(app_cache.cache_overview), "reloaded": reloaded, "failed": failed}


@router.post("/cache/save", dependencies=app_core.auth)
async def save_cache() -> Dict[str, Any]:
    """Save every loaded model's slots now: what the hub calls before it
    stops this runtime, whose servers die with it."""
    saved = {}
    async with app_speech_models._lock():
        for m in list(app_speech_models.state.loaded.values()):
            saved[m.name] = await app_cache.save_slots(m)
    return {"saved": saved}


@router.post("/cache/warmup", dependencies=app_core.auth)
async def warmup_cache(body: CacheModelBody) -> Dict[str, Any]:
    """Send a loaded model its kept prompt heads again, in the background."""
    m = app_speech_models._find_loaded(body.model)
    if m is None or m.kind != "chat":
        raise HTTPException(status_code=404, detail=f"no loaded chat model {body.model!r}")
    return {"started": app_cache.start_warm_up(m), "heads": len(app_cache.heads.list(m.name))}


@router.delete("/cache", dependencies=app_core.auth)
async def clear_cache(model: Optional[str] = None) -> Dict[str, Any]:
    """Forget what is kept on disk, the saved slots and the prompt heads,
    for ``model`` or for every model. What the running servers hold in
    memory stays until they are loaded again."""
    root = app_cache._cache_dir() / "slots"
    names = [app_speech_models.model_name(app_speech_models._safe_file(model))] if model else (
        [d.name for d in root.iterdir() if d.is_dir()] if root.is_dir() else [])
    for name in names:
        app_cache._drop_slot_files(app_cache._slot_dir(name))
    app_cache.heads.clear(app_speech_models.model_name(model) if model else None)
    return await asyncio.to_thread(app_cache.cache_overview)


async def _speech_model(model: str, kind: str, keep: Tuple[str, ...] = ()) -> Any:
    """The running server of speech model ``model``, loaded now when it is
    on disk but not running (evicting none of ``keep``); or the error answer
    to send instead."""
    app_serving._drop_dead()
    m = app_speech_models._find_loaded(model) if model else None
    if m is None and model:
        entry = next((e for e in await asyncio.to_thread(app_speech_models.list_models)
                      if e["name"] == model and e.get("kind") in app_speech_models.SPEECH_KINDS), None)
        if entry is not None:
            if not entry["loadable"]:
                return _error(409, entry.get("note") or f"{model} cannot run here", "engine_missing")
            try:
                await app_serving.load_model(entry["file"], 4096, -1, None, keep=keep)
            except HTTPException as exc:
                return _error(exc.status_code if exc.status_code < 500 else 502, str(exc.detail),
                              "load_failed")
            m = app_speech_models._find_loaded(model)
    if m is None:
        here = sorted(e["name"] for e in await asyncio.to_thread(app_speech_models.list_models)
                      if e.get("kind") == kind and e.get("loadable"))
        return _error(404, f"no {kind} model {model!r} in the hub runtime; download one on the Models page "
                           f"(here: {', '.join(here) or 'none'})", "model_not_found")
    if m.kind != kind:
        return _error(400, f"{model!r} is a {m.kind} model, not a {kind} model", "wrong_model_kind")
    return m


@router.post("/v1/audio/speech", dependencies=app_core.auth)
async def v1_speech(request: Request) -> Response:
    started = time.monotonic()
    path = "/v1/audio/speech"
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return app_core.metered(request, path, "", _error(400, "body is not JSON", "bad_request"), started)
    model = str(body.get("model") or "") if isinstance(body, dict) else ""
    target = await _speech_model(model, "speech")
    if isinstance(target, Response):
        return app_core.metered(request, path, model, target, started)
    if target.engine == "openvoice":
        return app_core.metered(request, path, model, await _converted_speech(target, body), started)
    return app_core.metered(request, path, model, await _forward(target, path, raw, "application/json"), started)


async def _converted_speech(converter: app_speech_models.Loaded, body: Dict[str, Any]) -> Response:
    """Speech in a recorded voice the fast way: a reading model
    (:func:`pick_base`) reads the text, OpenVoice gives it the voice."""
    text = str(body.get("input") or "").strip()
    if not text:
        return _error(400, "input is empty", "bad_request")
    voice = str(body.get("voice") or "")
    record = await asyncio.to_thread(app_voices.voice_record, voice) if app_voices._VOICE_NAME.match(voice) else None
    base = app_voices.pick_base(text, record, await asyncio.to_thread(app_speech_models.list_models))
    if base is None:
        lang = app_speech_models._worker().chatterbox_language(text, (record or {}).get("language"))
        return _error(409, f"OpenVoice needs a speech model that reads {lang!r} to read the text first; "
                           "download one on the Models page (Supertonic 3 reads 31 languages, Piper has a "
                           "voice per language, Kokoro reads English and seven more)", "no_reading_model")
    reader = await _speech_model(base[0], "speech", keep=(converter.name,))
    if isinstance(reader, Response):
        return reader
    converter.touch()
    reader.touch()
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0)) as client:
        try:
            read = await client.post(f"http://127.0.0.1:{reader.port}/v1/audio/speech", json={
                "input": text, "voice": base[1], "speed": body.get("speed") or 1.0, "response_format": "wav"})
            if read.status_code >= 400:
                return Response(content=read.content, status_code=read.status_code,
                                media_type=read.headers.get("content-type"))
            out = await client.post(f"http://127.0.0.1:{converter.port}/v1/audio/convert",
                                    files={"file": ("speech.wav", read.content, "audio/wav")},
                                    data={"voice": voice, "source": f"{base[0]}/{base[1]}",
                                          "tau": str(body.get("tau") or 0.3),
                                          "response_format": str(body.get("response_format") or "mp3")})
        except httpx.HTTPError as exc:
            return JSONResponse(status_code=502, content={"error": {"message": f"openvoice: {exc}"}})
    return Response(content=out.content, status_code=out.status_code, media_type=out.headers.get("content-type"))


@router.post("/v1/audio/transcriptions", dependencies=app_core.auth)
async def v1_transcriptions(request: Request) -> Response:
    started = time.monotonic()
    path = "/v1/audio/transcriptions"
    raw = await request.body()
    try:
        form = await request.form()
        model = str(form.get("model") or "")
        await form.close()
    except Exception:  # noqa: BLE001 - anything that is not a readable form
        return app_core.metered(request, path, "", _error(400, "expected multipart/form-data with a file and a model",
                                                 "bad_request"), started)
    target = await _speech_model(model, "transcription")
    if isinstance(target, Response):
        return app_core.metered(request, path, model, target, started)
    return app_core.metered(request, path, model,
                   await _forward(target, path, raw, request.headers.get("content-type") or "multipart/form-data"),
                   started)
