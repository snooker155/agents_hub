"""
Agents Hub model runtime: GGUF models served by llama.cpp's llama-server,
behind a small token-protected API and one OpenAI-compatible gateway.

Runs in its own container (the Dockerfile next to this file, the ``models``
profile in docker-compose.yml) or straight on the host
(``python deploy/models/app.py`` with llama-server on PATH, which is the only
way to use a Mac's GPU). The hub talks to it through
``providers/local_models.py`` and registers the gateway as the provider
``hub-local``. Every call except ``/healthz`` must carry the shared token
(``MODELS_TOKEN``, the hub's ``AGENTS_HUB_MODELS_TOKEN``); the service refuses
to start without one. See docs/local-models.md.

Models
    Files in ``MODELS_DIR`` ending in ``.gguf``. A directory holding
    ``config.json`` and ``*.safetensors`` is listed too, marked as not
    loadable: llama-server reads GGUF only.

Loading
    One llama-server subprocess per loaded model, on its own port from
    ``MODELS_BASE_PORT`` up, bound to ``MODELS_LLAMA_HOST`` (loopback by
    default: only this service talks to it). At most ``MODELS_MAX_LOADED`` run
    at once; loading one more evicts the least recently used.

Endpoints
    GET    /healthz                                  (no token)
    GET    /models
    DELETE /models/{file}
    GET    /models/{file}/structure
    POST   /download        {repo, file, revision}   -> {job_id}
    GET    /jobs, /jobs/{id}
    GET    /hf/files?repo=&revision=
    POST   /load            {file, context_length, gpu_layers, threads}
    POST   /unload          {file}
    GET    /memory
    GET    /v1/models, POST /v1/chat/completions, /v1/completions, /v1/embeddings
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

log = logging.getLogger("models_service")
logging.basicConfig(level=os.environ.get("MODELS_LOG_LEVEL", "INFO"))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def in_docker() -> bool:
    return Path("/.dockerenv").exists()


TOKEN = (os.environ.get("MODELS_TOKEN") or os.environ.get("AGENTS_HUB_MODELS_TOKEN") or "").strip()
MODELS_DIR = Path(os.environ.get("MODELS_DIR") or ("/models" if in_docker() else "./models"))
PORT = _env_int("MODELS_PORT", 8200)
LLAMA_SERVER_BIN = os.environ.get("LLAMA_SERVER_BIN") or "llama-server"
BASE_PORT = _env_int("MODELS_BASE_PORT", 8300)
MAX_LOADED = max(1, _env_int("MODELS_MAX_LOADED", 2))
LLAMA_HOST = os.environ.get("MODELS_LLAMA_HOST") or "127.0.0.1"
HF_TOKEN = (os.environ.get("HF_TOKEN") or "").strip()
HF_BASE = os.environ.get("HF_ENDPOINT") or "https://huggingface.co"
LOAD_TIMEOUT_SECONDS = float(_env_int("MODELS_LOAD_TIMEOUT", 120))
JOBS_KEPT = 50

_REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
_REVISION_RE = re.compile(r"^[\w.\-/]{1,100}$")
_QUANT_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(IQ\d_[A-Z0-9]+(?:_[A-Z0-9]+)?|Q\d_K(?:_[SML])?|Q\d_\d|"
                       r"Q\d_K|BF16|F16|F32)(?![A-Za-z0-9])")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Jobs ─────────────────────────────────────────────────────────────────────

class Jobs:
    """Downloads and their progress; the same job shape the hub's own
    registry uses (providers/local_models.py), so the UI reads both alike."""

    def __init__(self, keep: int = JOBS_KEPT) -> None:
        self.keep = keep
        self._jobs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def create(self, kind: str, name: str) -> Dict[str, Any]:
        job = {"id": uuid.uuid4().hex[:12], "kind": kind, "name": name, "status": "queued",
               "completed": 0, "total": 0, "percent": 0.0, "message": "", "error": None,
               "started_at": _now(), "finished_at": None}
        with self._lock:
            self._jobs[job["id"]] = job
            while len(self._jobs) > self.keep:
                victim = next((k for k, j in self._jobs.items()
                               if j["status"] in ("done", "error")), None)
                if victim is None:
                    break
                self._jobs.pop(victim)
        return dict(job)

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(fields)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            j = self._jobs.get(job_id)
            return dict(j) if j else None

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(j) for j in reversed(self._jobs.values())]


jobs = Jobs()


def http_client(**kwargs: Any) -> httpx.Client:
    """The client downloads and Hub lookups use; a test swaps it for one on a
    MockTransport."""
    return httpx.Client(follow_redirects=True, **kwargs)


def _hf_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {HF_TOKEN}"} if HF_TOKEN else {}


def run_download(job_id: str, url: str, dest: Path) -> None:
    """Stream ``url`` into ``dest``: written to ``<dest>.part`` and renamed only
    when complete, so a half file never shows up as a model."""
    part = dest.with_name(dest.name + ".part")
    jobs.update(job_id, status="running", message=f"downloading {dest.name}")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        timeout = httpx.Timeout(30.0, read=300.0)
        with http_client(timeout=timeout) as client:
            with client.stream("GET", url, headers=_hf_headers()) as resp:
                if resp.status_code >= 400:
                    hint = " (a gated repo needs HF_TOKEN)" if resp.status_code in (401, 403) else ""
                    raise RuntimeError(f"Hugging Face answered HTTP {resp.status_code}{hint}")
                total = int(resp.headers.get("content-length") or 0)
                jobs.update(job_id, total=total)
                done = 0
                last = 0.0
                with open(part, "wb") as fh:
                    for chunk in resp.iter_bytes(1 << 20):
                        fh.write(chunk)
                        done += len(chunk)
                        now = time.monotonic()
                        if now - last > 0.5:
                            last = now
                            jobs.update(job_id, completed=done,
                                        percent=round(100.0 * done / total, 1) if total else 0.0)
        if total and done != total:
            raise RuntimeError(f"download ended at {done} of {total} bytes")
        part.replace(dest)
        jobs.update(job_id, status="done", completed=done, total=total or done, percent=100.0,
                    message=f"saved {dest.name}", finished_at=_now())
    except Exception as exc:  # noqa: BLE001 - the job records the failure
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        jobs.update(job_id, status="error", error=f"{type(exc).__name__}: {exc}"[:500],
                    finished_at=_now())


# ── Loaded models ────────────────────────────────────────────────────────────

@dataclass
class Loaded:
    name: str
    file: str
    port: int
    proc: Any
    context_length: int
    loaded_at: str = field(default_factory=_now)
    last_used: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_used = time.monotonic()


class _State:
    loaded: Dict[str, Loaded] = {}
    lock: Optional[asyncio.Lock] = None


state = _State()


def _lock() -> asyncio.Lock:
    if state.lock is None:
        state.lock = asyncio.Lock()
    return state.lock


def model_name(file: str) -> str:
    return file[:-5] if file.lower().endswith(".gguf") else file


def _safe_file(file: str) -> str:
    """A plain file name inside MODELS_DIR, never a path."""
    f = (file or "").strip()
    if not f or "/" in f or "\\" in f or f.startswith(".") or "\x00" in f:
        raise HTTPException(status_code=400, detail=f"not a model file name: {file!r}")
    return f


def _model_path(file: str) -> Path:
    """The file a request names, or the same name with ``.gguf`` appended:
    the hub addresses a loaded model by its served name, which is the file
    name without the extension (see ``model_name``)."""
    f = _safe_file(file)
    path = MODELS_DIR / f
    if not path.exists() and not f.lower().endswith(".gguf") and (MODELS_DIR / f"{f}.gguf").is_file():
        path = MODELS_DIR / f"{f}.gguf"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no model file {f!r} in {MODELS_DIR}")
    return path


def _find_loaded(name_or_file: str) -> Optional[Loaded]:
    key = model_name(name_or_file)
    return state.loaded.get(key)


def list_models() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not MODELS_DIR.is_dir():
        return out
    for p in sorted(MODELS_DIR.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_file() and p.name.lower().endswith(".gguf"):
            loaded = state.loaded.get(model_name(p.name))
            out.append({"name": model_name(p.name), "file": p.name, "format": "gguf",
                        "loadable": True, "size_bytes": p.stat().st_size,
                        "loaded": loaded is not None,
                        "port": loaded.port if loaded else None,
                        "context_length": loaded.context_length if loaded else None,
                        "loaded_at": loaded.loaded_at if loaded else None})
        elif p.is_dir() and (p / "config.json").is_file() and any(p.glob("*.safetensors")):
            size = sum(f.stat().st_size for f in p.iterdir() if f.is_file())
            out.append({"name": p.name, "file": p.name, "format": "safetensors",
                        "loadable": False, "size_bytes": size, "loaded": False, "port": None,
                        "context_length": None, "loaded_at": None,
                        "note": "llama-server loads GGUF only; convert this directory with "
                                "llama.cpp's convert_hf_to_gguf.py to serve it."})
    return out


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((LLAMA_HOST, port))
            return True
        except OSError:
            return False


def _next_port() -> int:
    used = {m.port for m in state.loaded.values()}
    for port in range(BASE_PORT, BASE_PORT + 200):
        if port not in used and _port_free(port):
            return port
    raise HTTPException(status_code=503, detail=f"no free port from {BASE_PORT}")


def _log_path(name: str) -> Path:
    return MODELS_DIR / ".logs" / f"{name}.log"


def _log_tail(name: str, chars: int = 800) -> str:
    try:
        return _log_path(name).read_text(errors="replace")[-chars:]
    except OSError:
        return ""


def build_command(path: Path, name: str, port: int, context_length: int, gpu_layers: int,
                  threads: Optional[int]) -> List[str]:
    # -ngl larger than the layer count means "all"; llama-server has no -1.
    ngl = 999 if gpu_layers is None or gpu_layers < 0 else gpu_layers
    cmd = [LLAMA_SERVER_BIN, "-m", str(path), "--port", str(port), "--host", LLAMA_HOST,
           "-c", str(context_length), "-ngl", str(ngl), "--alias", name]
    if threads:
        cmd += ["-t", str(threads)]
    return cmd


async def wait_healthy(port: int, proc: Any, timeout: float = LOAD_TIMEOUT_SECONDS) -> None:
    """Poll llama-server's /health until it answers 200. It answers 503 while
    the weights load, so only a dead process or the deadline fails this."""
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"llama-server exited with code {proc.returncode}")
            try:
                r = await client.get(f"http://127.0.0.1:{port}/health")
                if r.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
    raise RuntimeError(f"llama-server did not become healthy within {int(timeout)} s")


def _stop(m: Loaded) -> None:
    try:
        m.proc.terminate()
        try:
            m.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            m.proc.kill()
            m.proc.wait(timeout=5)
    except Exception:  # noqa: BLE001 - the process may already be gone
        log.debug("stopping %s failed", m.name, exc_info=True)


def _drop_dead() -> None:
    for name, m in list(state.loaded.items()):
        if m.proc.poll() is not None:
            log.warning("llama-server for %s exited (code %s)", name, m.proc.returncode)
            state.loaded.pop(name, None)


async def load_model(file: str, context_length: int, gpu_layers: int,
                     threads: Optional[int]) -> Dict[str, Any]:
    path = _model_path(file)
    if not path.name.lower().endswith(".gguf"):
        raise HTTPException(status_code=400, detail=f"{path.name} is not a GGUF file; llama-server "
                                                    "cannot load it")
    name = model_name(path.name)
    evicted: List[str] = []
    async with _lock():
        _drop_dead()
        current = state.loaded.get(name)
        if current is not None:
            if current.context_length == context_length:
                current.touch()
                return {"ok": True, "name": name, "file": path.name, "port": current.port,
                        "context_length": current.context_length, "already_loaded": True,
                        "evicted": []}
            # A different context length needs a restart of that server.
            await asyncio.to_thread(_stop, state.loaded.pop(name))
        while len(state.loaded) >= MAX_LOADED:
            lru = min(state.loaded.values(), key=lambda m: m.last_used)
            state.loaded.pop(lru.name, None)
            await asyncio.to_thread(_stop, lru)
            evicted.append(lru.file)
            log.info("evicted %s to make room for %s", lru.name, name)
        port = _next_port()
        cmd = build_command(path, name, port, context_length, gpu_layers, threads)
        logfile = _log_path(name)
        logfile.parent.mkdir(parents=True, exist_ok=True)
        with open(logfile, "wb") as fh:
            try:
                proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL)
            except FileNotFoundError:
                raise HTTPException(status_code=500, detail=f"llama-server not found ({LLAMA_SERVER_BIN}); "
                                                            "set LLAMA_SERVER_BIN or install llama.cpp")
        try:
            await wait_healthy(port, proc)
        except Exception as exc:
            await asyncio.to_thread(_stop, Loaded(name, path.name, port, proc, context_length))
            raise HTTPException(status_code=502, detail=f"{exc}. Log tail: {_log_tail(name)}")
        m = Loaded(name=name, file=path.name, port=port, proc=proc, context_length=context_length)
        state.loaded[name] = m
    return {"ok": True, "name": name, "file": path.name, "port": port,
            "context_length": context_length, "already_loaded": False, "evicted": evicted}


async def unload_model(file: str) -> Dict[str, Any]:
    async with _lock():
        m = _find_loaded(_safe_file(file))
        if m is None:
            return {"ok": True, "unloaded": False}
        state.loaded.pop(m.name, None)
        await asyncio.to_thread(_stop, m)
    return {"ok": True, "unloaded": True, "name": m.name}


# ── Memory ───────────────────────────────────────────────────────────────────

def _meminfo_linux() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            out[key] = int(parts[0]) * 1024
    return {"total": out.get("MemTotal", 0), "available": out.get("MemAvailable", out.get("MemFree", 0))}


def _meminfo_macos() -> Dict[str, int]:
    total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True,
                               timeout=5).stdout.strip() or 0)
    vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
    page = 4096
    m = re.search(r"page size of (\d+) bytes", vm)
    if m:
        page = int(m.group(1))
    pages: Dict[str, int] = {}
    for line in vm.splitlines():
        key, _, val = line.partition(":")
        val = val.strip().rstrip(".")
        if val.isdigit():
            pages[key.strip()] = int(val)
    available = sum(pages.get(k, 0) for k in ("Pages free", "Pages inactive", "Pages speculative",
                                              "Pages purgeable")) * page
    return {"total": total, "available": available}


def _rss(pid: int) -> Optional[int]:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=5).stdout.strip()
        return int(out) * 1024 if out else None
    except Exception:  # noqa: BLE001 - no ps in a slim image, or the process just exited
        return None


def _gpu() -> Optional[List[Dict[str, Any]]]:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             timeout=5).stdout
    except Exception:  # noqa: BLE001 - a driver that hangs or is broken
        return None
    gpus = []
    for i, line in enumerate(out.strip().splitlines()):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            gpus.append({"index": i, "memory_total_bytes": int(parts[0]) * 1024 * 1024,
                         "memory_used_bytes": int(parts[1]) * 1024 * 1024})
    return gpus or None


def memory() -> Dict[str, Any]:
    ram: Dict[str, int] = {"total": 0, "available": 0}
    try:
        if Path("/proc/meminfo").exists():
            ram = _meminfo_linux()
        elif platform.system() == "Darwin":
            ram = _meminfo_macos()
    except Exception:  # noqa: BLE001 - memory numbers are informative, never fatal
        log.debug("reading memory failed", exc_info=True)
    rss = {name: _rss(getattr(m.proc, "pid", 0)) for name, m in state.loaded.items()}
    return {"ram_total_bytes": ram.get("total") or None,
            "ram_available_bytes": ram.get("available") or None,
            "process_rss_bytes": rss, "gpu": _gpu()}


# ── App and auth ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not TOKEN:
        raise RuntimeError("MODELS_TOKEN is not set; the model runtime refuses to run without one")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        yield
    finally:
        for m in list(state.loaded.values()):
            _stop(m)
        state.loaded.clear()


app = FastAPI(title="Agents Hub model runtime", lifespan=lifespan)


def token_ok(header_value: Optional[str], token: str) -> bool:
    """Constant-time check of an ``Authorization: Bearer <token>`` header."""
    if not token or not header_value:
        return False
    scheme, _, value = header_value.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(value.strip().encode(), token.encode())


def require_token(authorization: Optional[str] = Header(default=None)) -> None:
    if not token_ok(authorization, TOKEN):
        raise HTTPException(status_code=401, detail="missing or wrong token")


auth = [Depends(require_token)]


class DownloadBody(BaseModel):
    repo: str
    file: str
    revision: str = "main"


class LoadBody(BaseModel):
    file: str
    context_length: int = Field(default=4096, ge=256, le=1_048_576)
    gpu_layers: int = -1
    threads: Optional[int] = Field(default=None, ge=1, le=512)


class UnloadBody(BaseModel):
    file: str


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"ok": True, "loaded": len(state.loaded), "max_loaded": MAX_LOADED,
            "mode": "docker" if in_docker() else "host"}


@app.get("/models", dependencies=auth)
async def get_models() -> Dict[str, Any]:
    _drop_dead()
    return {"models_dir": str(MODELS_DIR), "max_loaded": MAX_LOADED, "models": list_models()}


@app.delete("/models/{file}", dependencies=auth)
async def delete_model(file: str) -> Dict[str, Any]:
    path = _model_path(file)
    if _find_loaded(path.name) is not None:
        raise HTTPException(status_code=409, detail=f"{path.name} is loaded; unload it first")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return {"ok": True, "deleted": path.name}


def _structure_reader() -> Any:
    """``structure_from_file`` from the copy the image vendors, or in host mode
    from the repository's providers/model_structure.py, loaded by path so the
    hub's ``providers`` package (and its imports) never loads here."""
    try:
        from hub_model_structure import structure_from_file  # type: ignore[import-not-found]
        return structure_from_file
    except ImportError:
        pass
    try:
        from providers.model_structure import structure_from_file  # type: ignore[import-not-found]
        return structure_from_file
    except ImportError:
        pass
    src = Path(__file__).resolve().parents[2] / "providers" / "model_structure.py"
    if not src.is_file():
        return None
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location("hub_model_structure", src)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return getattr(module, "structure_from_file", None)
    except Exception:  # noqa: BLE001 - a reader that fails to import is a missing reader
        log.warning("loading %s failed", src, exc_info=True)
        return None


@app.get("/models/{file}/structure", dependencies=auth)
async def model_structure(file: str) -> Dict[str, Any]:
    path = _model_path(file)
    structure_from_file = _structure_reader()
    if structure_from_file is None:
        raise HTTPException(status_code=501, detail="the structure reader is not installed in "
                                                    "this runtime (hub_model_structure.py)")
    try:
        return await asyncio.to_thread(structure_from_file, str(path))
    except Exception as exc:  # noqa: BLE001 - a file the parser cannot read
        raise HTTPException(status_code=422, detail=f"cannot read {path.name}: {exc}")


@app.post("/download", dependencies=auth)
async def download(body: DownloadBody) -> Dict[str, Any]:
    repo, revision = body.repo.strip(), body.revision.strip() or "main"
    if not _REPO_RE.match(repo):
        raise HTTPException(status_code=400, detail="repo must look like org/name")
    if not _REVISION_RE.match(revision) or ".." in revision:
        raise HTTPException(status_code=400, detail="bad revision")
    src = body.file.strip().lstrip("/")
    if not src or ".." in src.split("/") or not src.lower().endswith(".gguf"):
        raise HTTPException(status_code=400, detail="file must be a .gguf path inside the repo")
    dest_name = _safe_file(src.rsplit("/", 1)[-1])
    job = jobs.create("hf_download", f"{repo}/{src}")
    url = f"{HF_BASE}/{repo}/resolve/{revision}/{src}"
    threading.Thread(target=run_download, args=(job["id"], url, MODELS_DIR / dest_name),
                     name=f"download-{job['id']}", daemon=True).start()
    return {"job_id": job["id"], "file": dest_name}


@app.get("/jobs", dependencies=auth)
async def list_jobs() -> Dict[str, Any]:
    return {"jobs": jobs.list()}


@app.get("/jobs/{job_id}", dependencies=auth)
async def get_job(job_id: str) -> Dict[str, Any]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return job


def quantization_of(path: str) -> Optional[str]:
    m = _QUANT_RE.search(path.rsplit("/", 1)[-1])
    return m.group(1).upper() if m else None


@app.get("/hf/files", dependencies=auth)
async def hf_files(repo: str, revision: str = "main") -> Dict[str, Any]:
    repo = repo.strip()
    if not _REPO_RE.match(repo) or not _REVISION_RE.match(revision) or ".." in revision:
        raise HTTPException(status_code=400, detail="repo must look like org/name")

    def fetch() -> Any:
        with http_client(timeout=20.0) as client:
            return client.get(f"{HF_BASE}/api/models/{repo}/tree/{revision}",
                              params={"recursive": "true"}, headers=_hf_headers())

    try:
        resp = await asyncio.to_thread(fetch)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Hugging Face is unreachable: {exc}")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"no repo {repo} at {revision}")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Hugging Face answered HTTP {resp.status_code}")
    files = []
    for item in resp.json() or []:
        p = str(item.get("path") or "")
        if item.get("type") != "file" or not p.lower().endswith(".gguf"):
            continue
        size = (item.get("lfs") or {}).get("size") or item.get("size") or 0
        files.append({"file": p, "size_bytes": int(size), "quantization": quantization_of(p)})
    files.sort(key=lambda f: f["file"])
    return {"repo": repo, "revision": revision, "files": files}


@app.post("/load", dependencies=auth)
async def load(body: LoadBody) -> Dict[str, Any]:
    return await load_model(body.file, body.context_length, body.gpu_layers, body.threads)


@app.post("/unload", dependencies=auth)
async def unload(body: UnloadBody) -> Dict[str, Any]:
    return await unload_model(body.file)


@app.get("/memory", dependencies=auth)
async def get_memory() -> Dict[str, Any]:
    return await asyncio.to_thread(memory)


# ── OpenAI-compatible gateway ────────────────────────────────────────────────

@app.get("/v1/models", dependencies=auth)
async def v1_models() -> Dict[str, Any]:
    _drop_dead()
    return {"object": "list", "data": [
        {"id": m.name, "object": "model", "owned_by": "hub-local", "created": 0,
         "context_length": m.context_length} for m in state.loaded.values()]}


def _not_loaded(model: str) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": {
        "message": f"model {model!r} is not loaded in the hub runtime; load it on the Models page "
                   f"(loaded: {', '.join(sorted(state.loaded)) or 'none'})",
        "type": "invalid_request_error", "code": "model_not_loaded"}})


async def _proxy(request: Request, path: str) -> Response:
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return JSONResponse(status_code=400, content={"error": {"message": "body is not JSON"}})
    model = str(body.get("model") or "") if isinstance(body, dict) else ""
    m = _find_loaded(model) if model else None
    if m is None or m.proc.poll() is not None:
        return _not_loaded(model)
    m.touch()
    url = f"http://127.0.0.1:{m.port}{path}"
    headers = {"content-type": "application/json"}
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0))
    try:
        upstream = await client.send(client.build_request("POST", url, content=raw, headers=headers),
                                     stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        return JSONResponse(status_code=502, content={"error": {"message": f"llama-server: {exc}"}})

    async def close() -> None:
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                             media_type=upstream.headers.get("content-type"),
                             background=BackgroundTask(close))


@app.post("/v1/chat/completions", dependencies=auth)
async def v1_chat(request: Request) -> Response:
    return await _proxy(request, "/v1/chat/completions")


@app.post("/v1/completions", dependencies=auth)
async def v1_completions(request: Request) -> Response:
    return await _proxy(request, "/v1/completions")


@app.post("/v1/embeddings", dependencies=auth)
async def v1_embeddings(request: Request) -> Response:
    return await _proxy(request, "/v1/embeddings")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("MODELS_HOST") or ("0.0.0.0" if in_docker() else "127.0.0.1"),
                port=PORT)
