"""Request bodies and routes for models, downloads, jobs and Hugging Face files."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import app_bodies
import app_core
import app_hardware_search
import app_serving
import app_settings
import app_speech_models
import httpx
from fastapi import APIRouter, HTTPException

log = logging.getLogger("models_service")
router = APIRouter()


def _structure_source() -> Path:
    """The file the structure reader comes from: the image's vendored copy,
    or in host mode the repository's own."""
    here = Path(__file__).resolve()
    vendored = here.with_name("hub_model_structure.py")
    return vendored if vendored.is_file() else here.parents[2] / "providers" / "model_structure.py"


def code_version() -> str:
    """A digest of this service's own code and the structure reader it
    loads: the hub restarts a runtime it started when they changed under it."""
    import hashlib
    h = hashlib.sha1()
    here = Path(__file__).resolve().parent
    for f in (here / "app.py", app_settings.SPEECH_WORKER, app_settings.SPEECH_WORKER.with_name("openvoice_vc.py"),
              _structure_source(), app_settings.VOICE_ENHANCE, *sorted(here.glob("app_*.py"))):
        try:
            h.update(f.read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


VERSION = code_version()


@router.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"ok": True, "loaded": len(app_speech_models.state.loaded), "max_loaded": app_settings.MAX_LOADED,
            "max_speech_loaded": app_settings.MAX_SPEECH_LOADED, "mode": "docker" if app_settings.in_docker() else "host",
            "version": VERSION, "pid": os.getpid()}


@router.get("/models", dependencies=app_core.auth)
async def get_models() -> Dict[str, Any]:
    app_serving._drop_dead()
    models, found = await asyncio.to_thread(lambda: (app_speech_models.list_models(), app_speech_models.engines()))
    return {"models_dir": str(app_settings.MODELS_DIR), "max_loaded": app_settings.MAX_LOADED,
            "max_speech_loaded": app_settings.MAX_SPEECH_LOADED, "models": models, "engines": found}


@router.delete("/models/{file}", dependencies=app_core.auth)
async def delete_model(file: str) -> Dict[str, Any]:
    path = app_speech_models._model_path(file)
    if app_speech_models._find_loaded(path.name) is not None:
        raise HTTPException(status_code=409, detail=f"{path.name} is loaded; unload it first")
    if path.is_dir():
        shutil.rmtree(path)
        deleted = [path.name]
    else:
        deleted = []
        for part in app_speech_models.split_parts(path):
            part.unlink()
            deleted.append(part.name)
    return {"ok": True, "deleted": path.name, "files": deleted}


def _structure_module() -> Any:
    """The hub's GGUF reader (providers/model_structure.py): the copy the
    image vendors as ``hub_model_structure``, or in host mode the
    repository's file, loaded by path so the hub's ``providers`` package (and
    its imports) never loads here. None when neither is there."""
    try:
        import hub_model_structure  # type: ignore[import-not-found]
        return hub_model_structure
    except ImportError:
        pass
    try:
        from providers import model_structure  # type: ignore[import-not-found]
        return model_structure
    except ImportError:
        pass
    src = _structure_source()
    if not src.is_file():
        return None
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location("hub_model_structure", src)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        sys.modules["hub_model_structure"] = module
        return module
    except Exception:  # noqa: BLE001 - a reader that fails to import is a missing reader
        log.warning("loading %s failed", src, exc_info=True)
        return None


def _structure_reader() -> Any:
    """``structure_from_file`` from :func:`_structure_module`."""
    module = _structure_module()
    return getattr(module, "structure_from_file", None) if module is not None else None


@router.get("/models/{file}/structure", dependencies=app_core.auth)
async def model_structure(file: str) -> Dict[str, Any]:
    path = app_speech_models._model_path(file)
    structure_from_file = _structure_reader()
    if structure_from_file is None:
        raise HTTPException(status_code=501, detail="the structure reader is not installed in "
                                                    "this runtime (hub_model_structure.py)")
    try:
        return await asyncio.to_thread(structure_from_file, str(path))
    except Exception as exc:  # noqa: BLE001 - a file the parser cannot read
        raise HTTPException(status_code=422, detail=f"cannot read {path.name}: {exc}")


def _check_repo(repo: str, revision: str) -> None:
    if not app_core._REPO_RE.match(repo):
        raise HTTPException(status_code=400, detail="repo must look like org/name")
    if not app_core._REVISION_RE.match(revision) or ".." in revision:
        raise HTTPException(status_code=400, detail="bad revision")


@router.post("/download", dependencies=app_core.auth)
async def download(body: app_bodies.DownloadBody) -> Dict[str, Any]:
    repo, revision = body.repo.strip(), body.revision.strip() or "main"
    _check_repo(repo, revision)
    if body.package.strip():
        return await _download_package(repo, revision, body.package.strip())
    src = body.file.strip().lstrip("/")
    if not src or ".." in src.split("/") or not src.lower().endswith(".gguf"):
        raise HTTPException(status_code=400, detail="file must be a .gguf path inside the repo")
    dest_name = app_speech_models._safe_file(src.rsplit("/", 1)[-1])
    running = app_core.jobs.running_for(dest_name)
    if running is not None:
        raise HTTPException(status_code=409, detail=f"{dest_name} is already downloading (job {running['id']})")
    if (app_settings.MODELS_DIR / dest_name).is_file():
        raise HTTPException(status_code=409, detail=f"{dest_name} is already here; delete it to download again")
    part = app_settings.MODELS_DIR / f"{dest_name}.part"
    resuming = part.is_file() and part.stat().st_size > 0
    job = app_core.jobs.create("hf_download", f"{repo}/{src}",
                      meta={"repo": repo, "file": src, "revision": revision, "dest": dest_name})
    url = f"{app_settings.HF_BASE}/{repo}/resolve/{revision}/{src}"
    threading.Thread(target=app_core.run_download, args=(job["id"], url, app_settings.MODELS_DIR / dest_name),
                     name=f"download-{job['id']}", daemon=True).start()
    return {"job_id": job["id"], "file": dest_name, "resuming": resuming}


async def _download_package(repo: str, revision: str, name: str) -> Dict[str, Any]:
    """A speech model: the package of that name in the repo's tree, so the
    files fetched are the ones the listing showed and nothing a caller
    picked by hand."""
    app_speech_models._safe_file(name)
    packages = app_speech_models.speech_packages(repo, await _hf_tree_or_error(repo, revision))
    package = next((p for p in packages if p["name"] == name), None)
    if package is None:
        raise HTTPException(status_code=404, detail=f"no speech model {name!r} in {repo} at {revision}")
    running = app_core.jobs.running_for(name)
    if running is not None:
        raise HTTPException(status_code=409, detail=f"{name} is already downloading (job {running['id']})")
    if (app_settings.MODELS_DIR / name).exists():
        raise HTTPException(status_code=409, detail=f"{name} is already here; delete it to download again")
    resuming = app_core._staging_dir(name).is_dir()
    job = app_core.jobs.create("hf_package", f"{repo}: {name}",
                      meta={"repo": repo, "revision": revision, "dest": name, "engine": package["engine"],
                            "kind": package["kind"], "files": package["files"]})
    app_core.jobs.update(job["id"], total=package["size_bytes"])
    threading.Thread(target=app_core.run_package, args=(job["id"], repo, revision, package),
                     name=f"download-{job['id']}", daemon=True).start()
    return {"job_id": job["id"], "file": name, "resuming": resuming}


@router.get("/jobs", dependencies=app_core.auth)
async def list_jobs() -> Dict[str, Any]:
    return {"jobs": app_core.jobs.list()}


@router.get("/jobs/{job_id}", dependencies=app_core.auth)
async def get_job(job_id: str) -> Dict[str, Any]:
    job = app_core.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return job


def quantization_of(path: str) -> Optional[str]:
    m = app_core._QUANT_RE.search(path.rsplit("/", 1)[-1])
    return m.group(1).upper() if m else None


#: Pages of 1000 entries GET /hf/files follows; a voice collection such as
#: rhasspy/piper-voices takes a handful.
HF_TREE_PAGES = 20


def hf_tree(repo: str, revision: str) -> List[Dict[str, Any]]:
    """Every entry of a repo's tree, following the Hub's ``Link: rel=next``
    cursor. Raises HTTPException for a missing repo, httpx errors as they are."""
    url: str = f"{app_settings.HF_BASE}/api/models/{repo}/tree/{revision}"
    params: Optional[Dict[str, str]] = {"recursive": "true"}
    items: List[Dict[str, Any]] = []
    with app_core.http_client(timeout=20.0) as client:
        for _ in range(HF_TREE_PAGES):
            resp = client.get(url, params=params, headers=app_core._hf_headers())
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail=f"no repo {repo} at {revision}")
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail=f"Hugging Face answered HTTP {resp.status_code}")
            page = resp.json()
            items.extend(i for i in page or [] if isinstance(i, dict))
            nxt = (resp.links.get("next") or {}).get("url")
            if not nxt:
                break
            url, params = nxt, None
    return items


async def _hf_tree_or_error(repo: str, revision: str) -> List[Dict[str, Any]]:
    try:
        return await asyncio.to_thread(hf_tree, repo, revision)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Hugging Face is unreachable: {exc}")


@router.get("/hf/files", dependencies=app_core.auth)
async def hf_files(repo: str, revision: str = "main") -> Dict[str, Any]:
    repo = repo.strip()
    if not app_core._REPO_RE.match(repo) or not app_core._REVISION_RE.match(revision) or ".." in revision:
        raise HTTPException(status_code=400, detail="repo must look like org/name")
    items = await _hf_tree_or_error(repo, revision)
    files = []
    for item in items:
        p = str(item.get("path") or "")
        if item.get("type") != "file" or not p.lower().endswith(".gguf"):
            continue
        size = (item.get("lfs") or {}).get("size") or item.get("size") or 0
        files.append({"file": p, "size_bytes": int(size), "quantization": quantization_of(p)})
    files.sort(key=lambda f: f["file"])
    packages = [{k: v for k, v in p.items() if k not in ("sizes", "save_as")}
                for p in app_speech_models.speech_packages(repo, items)]
    for p in packages:
        p["downloaded"] = (app_settings.MODELS_DIR / p["name"]).exists()
    info = await asyncio.to_thread(app_hardware_search.hf_model_info, repo)
    hw = await asyncio.to_thread(app_hardware_search.hardware)
    split_total: Dict[str, int] = {}
    for f in files:
        m = app_speech_models._SPLIT_RE.match(f["file"].rsplit("/", 1)[-1])
        if m:
            split_total[m.group("stem")] = split_total.get(m.group("stem"), 0) + f["size_bytes"]
    for f in files:
        m = app_speech_models._SPLIT_RE.match(f["file"].rsplit("/", 1)[-1])
        # A split model runs whole: every part's size counts for each part.
        model_bytes = split_total[m.group("stem")] if m else f["size_bytes"]
        if model_bytes and not f["file"].rsplit("/", 1)[-1].lower().startswith("mmproj"):
            f["fit"] = app_hardware_search.estimate_fit(model_bytes, hw, active_share=info.get("active_share"))
    return {"repo": repo, "revision": revision, "files": files, "packages": packages,
            "model": info, "hardware": hw}
