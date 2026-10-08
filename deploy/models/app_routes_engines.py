"""Engine install, the Ollama and LM Studio import routes, load, unload and memory."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import app_bodies
import app_core
import app_engines
import app_serving
import app_settings
import app_speech_models
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger("models_service")
router = APIRouter()


# ── Speech engines ───────────────────────────────────────────────────────────

def torch_index() -> str:
    """Where torch comes from: PyPI, except on Linux without an NVIDIA GPU,
    where PyPI's build carries gigabytes of CUDA the CPU never uses and the
    CPU-only build is taken instead. ``MODELS_TORCH_INDEX`` overrides it
    (empty for PyPI)."""
    pinned = os.environ.get("MODELS_TORCH_INDEX")
    if pinned is not None:
        return pinned.strip()
    if platform.system() == "Linux" and shutil.which("nvidia-smi") is None:
        return "https://download.pytorch.org/whl/cpu"
    return ""


def install_commands(engine: str) -> List[List[str]]:
    """The commands installing ``engine`` runs, in order. An engine with an
    environment of its own (:data:`ENGINE_VENVS`) gets it made first, and
    the worker's own packages in it; a torch engine gets torch from
    :func:`torch_index` before the rest, which then finds it in place."""
    python = app_speech_models.engine_python(engine)
    pip = [python, "-m", "pip", "install", "--disable-pip-version-check"]
    cmds: List[List[str]] = []
    venv = app_speech_models.ENGINE_VENVS.get(engine)
    if venv is not None:
        if not app_speech_models._pinned_python(venv) and not Path(python).is_file():
            cmds.append([app_settings.SPEECH_PYTHON, "-m", "venv", str(app_speech_models.venv_dir(venv))])
        index = torch_index() if venv == "torch" else ""
        if index:
            torch_reqs = [r for r in app_speech_models.ENGINE_PACKAGES[engine] if r.startswith(("torch==", "torch>=", "torchaudio"))]
            cmds.append([*pip, "--index-url", index, *torch_reqs])
        cmds.append([*pip, *app_speech_models.WORKER_PACKAGES, *app_speech_models.ENGINE_PACKAGES[engine]])
    else:
        cmds.append([*pip, *app_speech_models.ENGINE_PACKAGES[engine]])
    if app_speech_models.ENGINE_NO_DEPS.get(engine):
        cmds.append([*pip, "--no-deps", *app_speech_models.ENGINE_NO_DEPS[engine]])
    return cmds


#: One pip at a time: an engine install and a voice cleanup that installs
#: its engine may both write to the same environment.
_pip_lock = threading.Lock()


def install_steps(job_id: str, engine: str) -> Optional[str]:
    """``pip install`` an engine's packages into the Python it runs under
    (:func:`engine_python`), the last line of pip's output as the job's
    message. Returns what went wrong, None once the engine imports."""
    with _pip_lock:
        for cmd in install_commands(engine):
            app_core.jobs.update(job_id, status="running", message=" ".join(cmd[2:])[:200])
            tail: List[str] = []
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        stdin=subprocess.DEVNULL)
                for line in proc.stdout or []:
                    line = line.strip()
                    if line:
                        tail = (tail + [line])[-8:]
                        app_core.jobs.update(job_id, message=line[:200])
                code = proc.wait()
            except Exception as exc:  # noqa: BLE001 - the job records why
                return f"{type(exc).__name__}: {exc}"[:400]
            if code != 0:
                tool = "venv" if "venv" in cmd[1:3] else "pip"
                return (f"{tool} exited with code {code}: " + " | ".join(tail[-3:]))[:500]
    if not app_speech_models.engines().get(engine):
        return f"pip finished, but {app_speech_models.ENGINE_MODULES[engine]} still does not import"
    return None


def run_install(job_id: str, engine: str) -> None:
    error = install_steps(job_id, engine)
    if error:
        app_core.jobs.update(job_id, status="error", error=error, finished_at=app_core._now())
        return
    app_core.jobs.update(job_id, status="done", percent=100.0, message=f"{engine} installed", finished_at=app_core._now())


class OllamaImportBody(BaseModel):
    name: str


@router.get("/ollama/models", dependencies=app_core.auth)
async def get_ollama_models() -> Dict[str, Any]:
    models = await asyncio.to_thread(app_engines.ollama_models)
    return {"dir": str(app_engines.OLLAMA_DIR), "found": (app_engines.OLLAMA_DIR / "manifests").is_dir(), "models": models}


@router.post("/ollama/import", dependencies=app_core.auth)
async def import_ollama_model(body: OllamaImportBody) -> Dict[str, Any]:
    """An Ollama model as a runtime file: a hard link to Ollama's blob, so
    nothing is copied and nothing more is taken on disk (the file stays here
    even when Ollama deletes its copy), or a copy as a job when the two
    directories are on different disks."""
    found = next(((e, b) for e, b in await asyncio.to_thread(app_engines._ollama_entries) if e["name"] == body.name.strip()),
                 None)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Ollama has no model {body.name!r} with GGUF weights "
                                                    f"in {app_engines.OLLAMA_DIR}")
    entry, src = found
    if not entry.get("compatible", True):
        raise HTTPException(status_code=422, detail=f"{entry['name']}: {entry['note']}")
    dest = app_settings.MODELS_DIR / entry["file"]
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"{entry['file']} is already here")
    app_settings.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
        return {"ok": True, "file": entry["file"], "linked": True, "job_id": None}
    except OSError as exc:
        log.info("hard link of %s failed (%s); copying", src, exc)
    job = app_core.jobs.create("ollama_import", f"{entry['name']} from Ollama",
                      meta={"name": entry["name"], "dest": entry["file"]})
    threading.Thread(target=app_engines.run_ollama_copy, args=(job["id"], src, dest), name=f"import-{job['id']}",
                     daemon=True).start()
    return {"ok": True, "file": entry["file"], "linked": False, "job_id": job["id"]}


@router.get("/lmstudio/models", dependencies=app_core.auth)
async def get_lmstudio_models() -> Dict[str, Any]:
    root = app_engines.lmstudio_dir()
    models = [e for e, _ in await asyncio.to_thread(app_engines._lmstudio_entries)]
    return {"dir": str(root), "found": root.is_dir(), "models": models}


@router.post("/lmstudio/import", dependencies=app_core.auth)
async def import_lmstudio_model(body: OllamaImportBody) -> Dict[str, Any]:
    """An LM Studio model as a runtime model: hard links (a GGUF file, or
    every file of an MLX folder), nothing copied; a copy as a job across
    disks."""
    found = next(((e, p) for e, p in await asyncio.to_thread(app_engines._lmstudio_entries) if e["name"] == body.name.strip()),
                 None)
    if found is None:
        raise HTTPException(status_code=404, detail=f"LM Studio has no model {body.name!r} in {app_engines.lmstudio_dir()}")
    entry, src = found
    if not entry["compatible"]:
        raise HTTPException(status_code=422, detail=f"{entry['name']}: {entry['note']}")
    dest = app_settings.MODELS_DIR / entry["file"]
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"{entry['file']} is already here")
    app_settings.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if src.is_dir():
            staging = app_settings.MODELS_DIR / f".{dest.name}.import"
            shutil.rmtree(staging, ignore_errors=True)
            try:
                app_engines._link_tree(src, staging)
                (staging / app_core.MODEL_MARKER).write_text(json.dumps(
                    {"source": f"LM Studio: {entry['name']}", "imported_at": app_core._now()}), encoding="utf-8")
                staging.replace(dest)
            except OSError:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        else:
            os.link(src, dest)
        return {"ok": True, "file": entry["file"], "linked": True, "job_id": None}
    except OSError as exc:
        log.info("hard link of %s failed (%s); copying", src, exc)
    job = app_core.jobs.create("lmstudio_import", f"{entry['name']} from LM Studio",
                      meta={"name": entry["name"], "dest": entry["file"]})
    target = app_engines.run_tree_copy if src.is_dir() else app_engines.run_ollama_copy
    threading.Thread(target=target, args=(job["id"], src, dest), name=f"import-{job['id']}",
                     daemon=True).start()
    return {"ok": True, "file": entry["file"], "linked": False, "job_id": job["id"]}


@router.get("/engines", dependencies=app_core.auth)
async def get_engines() -> Dict[str, Any]:
    found = await asyncio.to_thread(app_speech_models.engines)
    llama = {"id": "llama", "kind": "chat", "installed": found.get("llama", False),
             "packages": [f"llama.cpp release build ({app_engines.llama_asset_suffix() or 'none for this platform'})"],
             "version": app_engines._llama_record().get("tag"), "bin": app_engines.llama_bin()}
    return {"python": app_settings.SPEECH_PYTHON, "engines": [llama] + [
        {"id": e, "kind": app_speech_models.ENGINE_KIND[e], "installed": found.get(e, False),
         "packages": app_speech_models.ENGINE_PACKAGES[e] + app_speech_models.ENGINE_NO_DEPS.get(e, []), "python": app_speech_models.engine_python(e)}
        for e in app_speech_models.ENGINE_MODULES if e in found]}


@router.post("/engines/{engine}/install", dependencies=app_core.auth)
async def install_engine(engine: str) -> Dict[str, Any]:
    if engine in app_speech_models.APPLE_ENGINES and not app_speech_models.mlx_platform():
        raise HTTPException(status_code=409, detail="MLX runs on Apple silicon only")
    if engine not in app_speech_models.ENGINE_PACKAGES and engine != "llama":
        raise HTTPException(status_code=404, detail=f"no engine {engine!r}; one of llama, {', '.join(app_speech_models.ENGINE_PACKAGES)}")
    for j in app_core.jobs.list():
        if j["kind"] == "engine_install" and j["status"] in ("queued", "running"):
            raise HTTPException(status_code=409, detail=f"an install is already running (job {j['id']})")
    job = app_core.jobs.create("engine_install", f"{engine} engine", meta={"engine": engine})
    if engine == "llama":
        target, args = app_engines.run_llama_install, (job["id"],)
    else:
        target, args = run_install, (job["id"], engine)
    threading.Thread(target=target, args=args, name=f"install-{job['id']}", daemon=True).start()
    return {"job_id": job["id"], "engine": engine}


@router.post("/load", dependencies=app_core.auth)
async def load(body: app_bodies.LoadBody) -> Dict[str, Any]:
    return await app_serving.load_model(body.file, body.context_length, body.gpu_layers, body.threads)


@router.post("/unload", dependencies=app_core.auth)
async def unload(body: app_bodies.UnloadBody) -> Dict[str, Any]:
    return await app_serving.unload_model(body.file)


@router.get("/memory", dependencies=app_core.auth)
async def get_memory() -> Dict[str, Any]:
    return await asyncio.to_thread(app_serving.memory)
