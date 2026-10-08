"""
Local model servers the hub can manage: an external Ollama (phase A) and the
hub's own runtime under deploy/models (phase B). Feature 5 of the third plan;
see docs/local-models.md.

This module is the Python side the routes and other packages call. It must
stay importable without httpx being configured for anything: every function
that talks to a server takes the failure as a returned error or raises
``LocalModelError`` with a message safe to show.

Three parts:

* Ollama: list, pull (streamed, as a background job), delete, show.
* ``JobRegistry``: the in-process record of long operations (a pull), polled
  by the UI. It lives in this process only, so a restart forgets the jobs;
  the runtime service keeps its own registry for downloads.
* The hub runtime: ``RuntimeClient`` talks to deploy/models, and
  ``ensure_hub_local_backend`` plus ``catalog_set_enabled`` make what it has
  loaded usable as the provider ``hub-local``. Its speech and image models
  (kind ``speech``, ``transcription`` or ``image``) stay out of the chat
  catalog: a workspace picks them as special models (providers/special.py)
  under the same provider.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

log = logging.getLogger(__name__)

#: The custom backend id the hub runtime is registered under.
HUB_LOCAL_ID = "hub-local"
HUB_LOCAL_LABEL = "Hub runtime"

#: Model kinds the runtime serves on /v1/audio rather than for chat.
SPEECH_KINDS = ("speech", "transcription")
#: ... and on /v1/images (Qwen-Image through mflux).
IMAGE_KINDS = ("image",)
#: Every kind that is picked per workspace as a special model, never in the
#: chat catalog.
SPECIAL_KINDS = SPEECH_KINDS + IMAGE_KINDS

#: The header the runtime counts its calls by (deploy/models/app.py, Usage):
#: ``hub`` for the hub's agents and its own work, ``endpoint`` for a call to
#: the hub's /v1 from outside, ``voice`` for the assistant's speech.
SOURCE_HEADER = "X-Hub-Source"
_source: ContextVar[str] = ContextVar("runtime_source", default="hub")


@contextmanager
def runtime_source(name: str) -> Iterator[None]:
    """Calls to the runtime set up inside this block (a chat model built, a
    speech endpoint resolved) are counted under ``name``."""
    token = _source.set(name)
    try:
        yield
    finally:
        _source.reset(token)


def source_headers() -> Dict[str, str]:
    """The header naming the current caller, for a call to the runtime."""
    return {SOURCE_HEADER: _source.get()}

#: How many finished jobs the registry remembers.
JOBS_KEPT = 50


class LocalModelError(Exception):
    """A local model server refused or could not be reached; the message is
    safe to show in the UI.

    ``status_code`` is the HTTP status the server answered with, or None when
    it could not be reached at all. Routes pass a 4xx through and turn the
    rest into a 502.
    """

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LocalModelNotFound(LocalModelError):
    """The server answered, and has no such model."""

    def __init__(self, message: str) -> None:
        super().__init__(message, 404)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Ollama ───────────────────────────────────────────────────────────────────

def ollama_base_url() -> str:
    """The Ollama address the hub uses, from the host's point of view,
    rewritten for a containerised backend the same way build_chat_model does."""
    import os
    from common.config import settings
    from common.hostnet import host_service_url
    url = (os.environ.get("OLLAMA_BASE_URL") or getattr(settings, "ollama_base_url", "")
           or "http://localhost:11434")
    return host_service_url(str(url).strip().rstrip("/"))


def ollama_list(timeout: float = 5.0) -> Dict[str, Any]:
    """``GET /api/tags``: the models Ollama has on disk."""
    import httpx
    try:
        resp = httpx.get(f"{ollama_base_url()}/api/tags", timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise LocalModelError(f"Ollama at {ollama_base_url()} is unreachable: {type(exc).__name__}: {exc}")
    return resp.json() if resp.content else {"models": []}


def ollama_show(name: str, *, verbose: bool = False, timeout: float = 30.0) -> Dict[str, Any]:
    """``POST /api/show``: a model's details. With ``verbose`` Ollama also lists
    every tensor (name, type, shape), which is what the structure view reads."""
    import httpx
    payload: Dict[str, Any] = {"model": name, "name": name}
    if verbose:
        payload["verbose"] = True
    try:
        resp = httpx.post(f"{ollama_base_url()}/api/show", json=payload, timeout=timeout)
    except httpx.HTTPError as exc:
        raise LocalModelError(f"Ollama at {ollama_base_url()} is unreachable: {type(exc).__name__}: {exc}")
    if resp.status_code == 404:
        raise LocalModelNotFound(f"Ollama has no model named {name!r}")
    if resp.status_code != 200:
        raise LocalModelError(f"Ollama answered HTTP {resp.status_code}: {resp.text[:200]}",
                              resp.status_code)
    return resp.json()


def ollama_models(timeout: float = 5.0) -> Dict[str, Any]:
    """The Ollama card's payload: every model flattened to the fields the UI
    shows, and the disk they take together. Never raises: an unreachable
    Ollama is ``{"ok": False, "error": ...}``, which is a normal state (it is
    simply not running), not a server error."""
    base = ollama_base_url()
    try:
        raw = ollama_list(timeout=timeout)
    except LocalModelError as exc:
        return {"ok": False, "error": str(exc), "base_url": base}
    except ValueError as exc:  # a body that is not JSON: something else on that port
        return {"ok": False, "error": f"{base} did not answer like Ollama: {exc}", "base_url": base}
    models: List[Dict[str, Any]] = []
    for m in raw.get("models") or []:
        if not isinstance(m, dict):
            continue
        details = m.get("details") or {}
        models.append({
            "name": str(m.get("name") or m.get("model") or ""),
            "size": int(m.get("size") or 0),
            "modified_at": m.get("modified_at"),
            "digest": m.get("digest"),
            "family": details.get("family"),
            "parameter_size": details.get("parameter_size"),
            "quantization_level": details.get("quantization_level"),
            "format": details.get("format"),
        })
    models.sort(key=lambda x: x["name"])
    return {"ok": True, "base_url": base, "models": models,
            "disk_bytes": sum(m["size"] for m in models)}


def ollama_delete(name: str, timeout: float = 30.0) -> None:
    """``DELETE /api/delete``. Raises ``LocalModelNotFound`` for an unknown
    name so the route can answer 404."""
    import httpx
    try:
        resp = httpx.request("DELETE", f"{ollama_base_url()}/api/delete",
                             json={"model": name, "name": name}, timeout=timeout)
    except httpx.HTTPError as exc:
        raise LocalModelError(f"Ollama at {ollama_base_url()} is unreachable: {type(exc).__name__}: {exc}")
    if resp.status_code == 404:
        raise LocalModelNotFound(f"Ollama has no model named {name!r}")
    if resp.status_code >= 400:
        raise LocalModelError(f"Ollama answered HTTP {resp.status_code}: {resp.text[:200]}",
                              resp.status_code)


def ollama_pull_stream(name: str) -> Iterator[Dict[str, Any]]:
    """``POST /api/pull`` with ``stream: true``: one dict per progress line
    (``status``, and ``digest``/``completed``/``total`` while a layer
    downloads). Ollama reports a failure as a line with ``error``, which the
    job registry turns into a failed job."""
    import httpx
    # No overall deadline: a large model takes as long as it takes. The read
    # timeout only catches a server that stopped talking.
    timeout = httpx.Timeout(30.0, read=600.0)
    try:
        with httpx.stream("POST", f"{ollama_base_url()}/api/pull",
                          json={"model": name, "name": name, "stream": True},
                          timeout=timeout) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise LocalModelError(f"Ollama answered HTTP {resp.status_code}: {resp.text[:200]}",
                                      resp.status_code)
            for line in resp.iter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    yield event
    except httpx.HTTPError as exc:
        raise LocalModelError(f"Ollama at {ollama_base_url()} is unreachable: {type(exc).__name__}: {exc}")


def lmstudio_base_url() -> str:
    """LM Studio's address, rewritten for a containerised backend like
    Ollama's (:func:`ollama_base_url`)."""
    import os
    from common.config import settings
    from common.hostnet import host_service_url
    url = (os.environ.get("LMSTUDIO_BASE_URL") or getattr(settings, "lmstudio_base_url", "")
           or "http://localhost:1234")
    return host_service_url(str(url).strip().rstrip("/"))


#: Seconds a status check waits for a local server: it is on this machine,
#: so anything slower is as good as down for a marker on a page.
SERVER_CHECK_TIMEOUT = 1.5


def server_status() -> Dict[str, Dict[str, Any]]:
    """Whether the local model servers answer right now: Ollama, LM Studio
    and the hub's own runtime, checked at once with a short timeout. Each is
    ``{ok, url}``, plus ``error`` when it does not answer; the runtime is
    left out when the hub has none."""
    import httpx
    from concurrent.futures import ThreadPoolExecutor

    def probe(url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        try:
            resp = httpx.get(url, timeout=SERVER_CHECK_TIMEOUT, headers=headers or {})
            if resp.status_code < 400:
                return {"ok": True}
            return {"ok": False, "error": f"HTTP {resp.status_code}"}
        except httpx.HTTPError as exc:
            return {"ok": False, "error": type(exc).__name__}

    targets: Dict[str, Any] = {
        "ollama": (ollama_base_url(), "/api/tags"),
        "lmstudio": (lmstudio_base_url().removesuffix("/v1"), "/v1/models"),
    }
    cfg = runtime_settings()
    if cfg["url"]:
        targets[HUB_LOCAL_ID] = (cfg["url"], "/healthz")
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        futures = {name: pool.submit(probe, base + path) for name, (base, path) in targets.items()}
        return {name: {**futures[name].result(), "url": targets[name][0]} for name in targets}


# ── Jobs ─────────────────────────────────────────────────────────────────────

#: The database store the hub's jobs are kept in (common/docstore.py), so a
#: pull survives a restart of the API process and shows on every replica.
JOBS_STORE = "local_model_jobs"
#: Progress is written to the store at most this often per job; a status
#: change is written at once.
_JOBS_WRITE_INTERVAL = 0.5


class JobRegistry:
    """Long operations (a pull) and their progress, for the UI to poll.

    A job is driven by a stream of progress dicts in the shape Ollama's pull
    emits: ``status`` is the current step, ``digest``/``completed``/``total``
    describe one layer. Layers are summed, so the job's ``completed`` and
    ``total`` cover the whole model. The stream function is injected, which
    is what lets a test drive a job with a list of fake events.

    With a ``store`` name the registry mirrors every job into that DocStore
    and reads the list back on first use, so a restart does not lose it: a
    job that was running when the process died is closed as an error and
    marked ``resumable``, which for an Ollama pull means asking for the same
    pull again (Ollama keeps the layers it already has). Without a store the
    registry is in memory only, which is what the tests use.
    """

    def __init__(self, keep: int = JOBS_KEPT, *, store: Optional[str] = None) -> None:
        self.keep = keep
        self._jobs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._store_name = store
        self._store: Any = None
        self._loaded = store is None
        self._last_write: Dict[str, float] = {}

    # ── persistence ──────────────────────────────────────────────────────────

    def _docs(self) -> Any:
        if self._store is None and self._store_name:
            from common.docstore import DocStore
            self._store = DocStore(self._store_name)
        return self._store

    def _ensure_loaded(self) -> None:
        """Read the stored list once, closing whatever a dead process left
        open. Called under the lock by every reader and writer."""
        if self._loaded:
            return
        self._loaded = True
        try:
            docs = self._docs().values()
        except Exception:  # noqa: BLE001 - no database yet (a run container's snapshot): memory only
            log.debug("job store %s unreadable; jobs stay in memory", self._store_name, exc_info=True)
            return
        for j in docs:
            if not isinstance(j, dict) or not j.get("id"):
                continue
            j.setdefault("resumable", False)
            if j.get("status") in ("queued", "running"):
                j.update(status="error", finished_at=_now(), resumable=True,
                         error="interrupted by a restart of the service; start it again to resume")
                self._persist(j, force=True)
            self._jobs[str(j["id"])] = j
        self._trim()

    def _persist(self, job: Dict[str, Any], *, force: bool = False) -> None:
        if not self._store_name:
            return
        now = time.monotonic()
        if not force and now - self._last_write.get(job["id"], 0.0) < _JOBS_WRITE_INTERVAL:
            return
        self._last_write[job["id"]] = now
        try:
            self._docs().put(job["id"], dict(job))
        except Exception:  # noqa: BLE001 - a job must not fail because its record could not be written
            log.debug("could not persist job %s", job["id"], exc_info=True)

    def _forget(self, job_id: str) -> None:
        self._threads.pop(job_id, None)
        self._last_write.pop(job_id, None)
        if self._store_name:
            try:
                self._docs().delete(job_id)
            except Exception:  # noqa: BLE001 - see _persist
                log.debug("could not delete job record %s", job_id, exc_info=True)

    # ── the list ─────────────────────────────────────────────────────────────

    def create(self, kind: str, name: str, *, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        job = {"id": uuid.uuid4().hex[:12], "kind": kind, "name": name, "status": "queued",
               "completed": 0, "total": 0, "percent": 0.0, "message": "", "error": None,
               "resumable": False, "meta": dict(meta or {}),
               "started_at": _now(), "finished_at": None}
        with self._lock:
            self._ensure_loaded()
            self._jobs[job["id"]] = job
            self._trim()
            self._persist(job, force=True)
        return dict(job)

    def _trim(self) -> None:
        # Oldest finished jobs go first; a running job is never forgotten.
        while len(self._jobs) > self.keep:
            victim = next((jid for jid, j in self._jobs.items()
                           if j["status"] in ("done", "error")), None)
            if victim is None:
                break
            self._jobs.pop(victim, None)
            self._forget(victim)

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            self._ensure_loaded()
            job = self._jobs.get(job_id)
            if job is not None:
                changed = "status" in fields and fields["status"] != job.get("status")
                job.update(fields)
                self._persist(job, force=changed or job.get("status") in ("done", "error"))

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self) -> List[Dict[str, Any]]:
        """Newest first."""
        with self._lock:
            self._ensure_loaded()
            return [dict(j) for j in reversed(self._jobs.values())]

    def start(self, kind: str, name: str, stream_fn: Callable[[], Iterable[Dict[str, Any]]],
              *, background: bool = True, meta: Optional[Dict[str, Any]] = None) -> str:
        """Create a job and drive it with ``stream_fn()``: in a daemon thread,
        or inline when ``background`` is False (tests)."""
        job = self.create(kind, name, meta=meta)
        if not background:
            self.drive(job["id"], stream_fn)
            return job["id"]
        t = threading.Thread(target=self.drive, args=(job["id"], stream_fn),
                             name=f"local-model-job-{job['id']}", daemon=True)
        with self._lock:
            self._threads[job["id"]] = t
        t.start()
        return job["id"]

    def wait(self, job_id: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
        """Block until a background job finishes (tests)."""
        with self._lock:
            t = self._threads.get(job_id)
        if t is not None:
            t.join(timeout)
        return self.get(job_id)

    def drive(self, job_id: str, stream_fn: Callable[[], Iterable[Dict[str, Any]]]) -> None:
        self.update(job_id, status="running")
        layers: Dict[str, tuple] = {}
        try:
            for event in stream_fn():
                if event.get("error"):
                    raise LocalModelError(str(event["error"]))
                fields: Dict[str, Any] = {}
                if event.get("status"):
                    fields["message"] = str(event["status"])
                if event.get("total"):
                    key = str(event.get("digest") or "_")
                    layers[key] = (int(event.get("completed") or 0), int(event["total"]))
                    completed = sum(c for c, _ in layers.values())
                    total = sum(t for _, t in layers.values())
                    fields.update(completed=completed, total=total,
                                  percent=round(100.0 * min(completed, total) / total, 1))
                if fields:
                    self.update(job_id, **fields)
        except Exception as exc:  # noqa: BLE001 - a failed job records why, the thread never dies loudly
            self.update(job_id, status="error", error=str(exc)[:500], finished_at=_now())
            return
        job = self.get(job_id) or {}
        total = int(job.get("total") or 0)
        self.update(job_id, status="done", percent=100.0,
                    completed=total or int(job.get("completed") or 0), finished_at=_now())


#: The hub's own jobs (Ollama pulls), kept in the database. Downloads into
#: the runtime live in the runtime service's registry and are read through
#: ``RuntimeClient``.
JOBS = JobRegistry(store=JOBS_STORE)


def start_ollama_pull(name: str, *, registry: Optional[JobRegistry] = None,
                      stream_fn: Optional[Callable[[], Iterable[Dict[str, Any]]]] = None,
                      background: bool = True) -> str:
    """Start pulling ``name`` into Ollama; returns the job id."""
    reg = registry or JOBS
    fn = stream_fn or (lambda: ollama_pull_stream(name))
    return reg.start("ollama_pull", name, fn, background=background, meta={"name": name})


# ── The hub runtime (deploy/models) ──────────────────────────────────────────

def _token_file(path: str) -> str:
    try:
        return open(path, encoding="utf-8").read().strip() if path else ""
    except OSError:
        return ""


def runtime_settings() -> Dict[str, Any]:
    """URL, token and timeout of the runtime service: the one
    ``AGENTS_HUB_MODELS_URL`` names (with ``AGENTS_HUB_MODELS_TOKEN`` or the
    token file it wrote), else the one the hub runs itself on this host
    (providers/model_runtime_host.py); ``managed`` says which."""
    from common.config import settings
    timeout = float(getattr(settings, "models_timeout", 60.0) or 60.0)
    url = str(getattr(settings, "models_url", "") or "").strip().rstrip("/")
    if url:
        token = (str(getattr(settings, "models_token", "") or "").strip()
                 or _token_file(str(getattr(settings, "models_token_file", "") or "").strip()))
        return {"url": url, "token": token, "timeout": timeout, "managed": False}
    from providers import model_runtime_host as host
    if host.active():
        return {"url": host.url(), "token": host.token(), "timeout": timeout, "managed": True}
    return {"url": "", "token": "", "timeout": timeout, "managed": False}


def runtime_configured() -> bool:
    return bool(runtime_settings()["url"])


class RuntimeClient:
    """Thin HTTP client for deploy/models. Every method returns the service's
    JSON or raises ``LocalModelError`` (``status_code`` None when the service
    could not be reached)."""

    #: How long a load may take: the service itself waits up to 120 s for
    #: llama-server to answer its health check.
    LOAD_TIMEOUT = 150.0

    def __init__(self, url: Optional[str] = None, token: Optional[str] = None,
                 timeout: Optional[float] = None, transport: Any = None) -> None:
        cfg = runtime_settings()
        self.url = (url if url is not None else cfg["url"]).rstrip("/")
        self.token = token if token is not None else cfg["token"]
        self.timeout = float(timeout if timeout is not None else cfg["timeout"])
        self.transport = transport

    def _send(self, method: str, path: str, *, timeout: Optional[float] = None, **kwargs: Any) -> Any:
        """The runtime's answer to one call, a 4xx or 5xx raised as
        ``LocalModelError`` with the runtime's reason (FastAPI's ``detail``
        or the gateway's ``error.message``)."""
        import httpx
        if not self.url:
            raise LocalModelError("The model runtime is not configured (AGENTS_HUB_MODELS_URL).")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        headers.update(kwargs.pop("headers", None) or {})
        try:
            with httpx.Client(transport=self.transport, timeout=timeout or self.timeout) as client:
                resp = client.request(method, f"{self.url}{path}", headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise LocalModelError(
                f"The model runtime at {self.url} is unreachable: {type(exc).__name__}: {exc}")
        if resp.status_code >= 400:
            try:
                body = resp.json()
                detail = body.get("detail") or (body.get("error") or {}).get("message")
            except Exception:  # noqa: BLE001 - any non-JSON error body
                detail = None
            raise LocalModelError(str(detail or resp.text[:300] or f"HTTP {resp.status_code}"),
                                  resp.status_code)
        return resp

    def _request(self, method: str, path: str, *, timeout: Optional[float] = None,
                 **kwargs: Any) -> Any:
        resp = self._send(method, path, timeout=timeout, **kwargs)
        return resp.json() if resp.content else {}

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/healthz")

    def models(self) -> List[Dict[str, Any]]:
        return list(self.listing().get("models") or [])

    def listing(self) -> Dict[str, Any]:
        """``/models`` whole: ``models`` and which speech ``engines`` are
        installed."""
        return self._request("GET", "/models") or {}

    def memory(self) -> Dict[str, Any]:
        return self._request("GET", "/memory")

    def usage(self) -> Dict[str, Any]:
        """Every call through the runtime's gateway, per model, caller and
        kind: ``since``, ``totals``, ``rows`` and ``recent``."""
        return self._request("GET", "/usage") or {}

    def clear_usage(self) -> Dict[str, Any]:
        return self._request("DELETE", "/usage") or {}

    def download(self, repo: str, file: str = "", revision: str = "main", *,
                 package: str = "") -> Dict[str, Any]:
        """A GGUF ``file``, or the speech model ``package`` that
        :meth:`hf_files` listed for the repo."""
        return self._request("POST", "/download",
                             json={"repo": repo, "file": file, "package": package, "revision": revision})

    def engines(self) -> Dict[str, Any]:
        return self._request("GET", "/engines")

    def install_engine(self, engine: str) -> Dict[str, Any]:
        return self._request("POST", f"/engines/{engine}/install")

    def ollama_models(self) -> Dict[str, Any]:
        """The models Ollama keeps on the runtime's machine that it can take
        over: ``{dir, found, models}``."""
        return self._request("GET", "/ollama/models")

    def ollama_import(self, name: str) -> Dict[str, Any]:
        return self._request("POST", "/ollama/import", json={"name": name})

    def lmstudio_models(self) -> Dict[str, Any]:
        """LM Studio's models on the runtime's machine (GGUF files and MLX
        folders): ``{dir, found, models}``."""
        return self._request("GET", "/lmstudio/models")

    def lmstudio_import(self, name: str) -> Dict[str, Any]:
        return self._request("POST", "/lmstudio/import", json={"name": name})

    def jobs(self) -> List[Dict[str, Any]]:
        return list((self._request("GET", "/jobs") or {}).get("jobs") or [])

    def job(self, job_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/jobs/{job_id}")

    def load(self, file: str, context_length: int = 4096, gpu_layers: int = -1,
             threads: Optional[int] = None) -> Dict[str, Any]:
        return self._request("POST", "/load", timeout=max(self.timeout, self.LOAD_TIMEOUT),
                             json={"file": file, "context_length": context_length,
                                   "gpu_layers": gpu_layers, "threads": threads})

    def unload(self, file: str) -> Dict[str, Any]:
        # An unload first saves the model's prompt cache slots to disk.
        return self._request("POST", "/unload", timeout=max(self.timeout, self.LOAD_TIMEOUT),
                             json={"file": file})

    # ── prompt cache (deploy/models/app.py, "Prompt cache") ──

    def cache(self) -> Dict[str, Any]:
        """Settings, what each loaded chat model holds, what is kept on disk."""
        return self._request("GET", "/cache") or {}

    def set_cache_settings(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("PUT", "/cache/settings", json=changes) or {}

    def apply_cache(self) -> Dict[str, Any]:
        """Load again every model whose cache flags differ from the settings."""
        return self._request("POST", "/cache/apply", timeout=max(self.timeout, 2 * self.LOAD_TIMEOUT)) or {}

    def warm_cache(self, model: str) -> Dict[str, Any]:
        return self._request("POST", "/cache/warmup", json={"model": model}) or {}

    def clear_cache(self, model: Optional[str] = None) -> Dict[str, Any]:
        return self._request("DELETE", "/cache", params={"model": model} if model else None) or {}

    def delete(self, file: str) -> Dict[str, Any]:
        return self._request("DELETE", f"/models/{file}")

    def hf_files(self, repo: str, revision: str = "main") -> Dict[str, Any]:
        return self._request("GET", "/hf/files", params={"repo": repo, "revision": revision})

    def hf_search(self, q: str = "", purpose: str = "chat", license: str = "any",
                  sort: str = "downloads", limit: int = 20) -> Dict[str, Any]:
        """GGUF models on the Hub with what each needs here: ``results`` and
        the ``hardware`` the estimates assume."""
        return self._request("GET", "/hf/search", timeout=max(self.timeout, 60.0),
                             params={"q": q, "purpose": purpose, "license": license,
                                     "sort": sort, "limit": limit})

    def hardware(self) -> Dict[str, Any]:
        return self._request("GET", "/hardware")

    def structure(self, file: str) -> Dict[str, Any]:
        return self._request("GET", f"/models/{file}/structure")

    # ── Recorded voices (the cloning engines' samples) ───────────────────────

    def voices(self) -> List[Dict[str, Any]]:
        return list((self._request("GET", "/voices") or {}).get("voices") or [])

    def add_voice(self, name: str, audio: bytes, filename: str, content_type: str, *,
                  owner: str, language: str = "", gender: str = "", shared: bool = False,
                  replace: bool = False, cleanup: str = "none") -> Dict[str, Any]:
        """A recording of ``name``; the person confirmed they may record it
        (the runtime refuses one without ``consent``). ``cleanup`` other than
        ``none`` starts one on it, its job's id in ``job_id``."""
        return self._request("POST", "/voices", timeout=max(self.timeout, 60.0),
                             files={"file": (filename or "voice", audio, content_type or "application/octet-stream")},
                             data={"name": name, "owner": owner, "language": language, "gender": gender,
                                   "shared": "true" if shared else "false", "consent": "true",
                                   "replace": "true" if replace else "false", "cleanup": cleanup or "none"})

    def clean_voice(self, name: str, mode: str) -> Dict[str, Any]:
        """Take the room out of a recording: ``denoise``, ``restore`` (noise
        and echo) or ``none`` (back to the original). A job for the first
        two: ``{job_id, engine, voice}``."""
        return self._request("POST", f"/voices/{name}/cleanup", json={"mode": mode})

    def update_voice(self, name: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("PATCH", f"/voices/{name}", json=fields)

    def delete_voice(self, name: str) -> Dict[str, Any]:
        return self._request("DELETE", f"/voices/{name}")

    def voice_audio(self, name: str, original: bool = False) -> bytes:
        """The sample the engines use, or with ``original`` the recording
        before its cleanup."""
        return self._send("GET", f"/voices/{name}/audio", params={"original": "true"} if original else None).content

    def speech(self, model: str, text: str, voice: str, response_format: str = "mp3") -> Tuple[bytes, str]:
        """``text`` read by speech model ``model`` through the gateway, as
        ``(audio, content type)``; Chatterbox may take minutes on a CPU."""
        resp = self._send("POST", "/v1/audio/speech", timeout=max(self.timeout, 600.0),
                          json={"model": model, "input": text, "voice": voice, "response_format": response_format},
                          headers=source_headers())
        return resp.content, resp.headers.get("content-type") or "audio/mpeg"


#: Runtime engines whose voices are recordings people made.
CLONING_ENGINES = ("chatterbox", "chatterbox_mlx", "openvoice")


def voice_visible(record: Dict[str, Any], viewer: Any) -> bool:
    """Whether ``viewer`` may see and pick a recorded voice: their own, one
    shared, one nobody owns; every one for an administrator or the hub
    itself (no viewer)."""
    if viewer is None or getattr(viewer, "is_admin", False):
        return True
    owner = str(record.get("owner") or "")
    return not owner or owner == getattr(viewer, "id", None) or bool(record.get("shared"))


def voice_editable(record: Dict[str, Any], viewer: Any) -> bool:
    """Whether ``viewer`` may change or remove a recorded voice: the person
    who recorded it, or an administrator."""
    if viewer is None or getattr(viewer, "is_admin", False):
        return True
    owner = str(record.get("owner") or "")
    return bool(owner) and owner == getattr(viewer, "id", None)


def model_name(file: str) -> str:
    """The model id a runtime file is served under: the file name without
    ``.gguf``, the same rule the service uses for llama-server's alias."""
    name = str(file or "").rsplit("/", 1)[-1]
    return name[:-5] if name.lower().endswith(".gguf") else name


def ensure_hub_local_backend() -> Optional[Dict[str, Any]]:
    """Register (or refresh) the custom backend ``hub-local`` pointing at the
    runtime's OpenAI-compatible gateway. A no-op when the runtime is not
    configured, and a write only when something changed, since the status
    route calls this on every poll."""
    cfg = runtime_settings()
    if not cfg["url"]:
        return None
    from providers import registry
    wanted = {"id": HUB_LOCAL_ID, "label": HUB_LOCAL_LABEL, "adapter": "openai",
              "base_url": f"{cfg['url']}/v1", "api_key": cfg["token"]}
    current = registry.get_backend(HUB_LOCAL_ID)
    if current and all(current.get(k) == v for k, v in wanted.items()):
        return current
    merged = {**(current or {}), **wanted}
    return registry.upsert_backend(merged)


def catalog_set_enabled(name: str, enabled: bool, *, context_window: int = 0,
                        seed: Optional[Callable[[], dict]] = None) -> Dict[str, Any]:
    """Enable (after a load) or disable (after an unload) ``name`` under the
    provider ``hub-local`` in the model catalog, adding it on first load.

    ``seed`` builds the catalog when none has been saved yet (the Models
    route's loader, which seeds from .env); without it an empty catalog is
    started, which would lose that seed, so the routes always pass it.
    Returns the model's catalog row.
    """
    from providers.catalog import load_catalog_raw, save_catalog_raw
    raw = load_catalog_raw()
    if not isinstance(raw, dict):
        raw = seed() if seed else {}
    catalog = dict(raw)
    entry = catalog.get(HUB_LOCAL_ID)
    if not isinstance(entry, dict):
        entry = {"default": "", "models": []}
    models = [m for m in entry.get("models") or [] if isinstance(m, dict)]
    row = next((m for m in models if m.get("id") == name), None)
    if row is None:
        if not enabled:
            return {}
        # Local weights cost nothing per token; "auto" leaves the row free to
        # be refilled by a Discover.
        row = {"id": name, "enabled": True, "input_price": 0.0, "output_price": 0.0,
               "cached_input_price": 0.0, "price_source": "auto",
               "context_window": int(context_window or 0), "released_at": 0}
        models.append(row)
    row["enabled"] = bool(enabled)
    if enabled and context_window:
        row["context_window"] = int(context_window)
    default = str(entry.get("default") or "")
    if enabled and not default:
        default = name
    elif not enabled and default == name:
        # A disabled model cannot be the default; the next loaded one takes it.
        default = next((m["id"] for m in models if m.get("enabled") and m["id"] != name), "")
    catalog[HUB_LOCAL_ID] = {"default": default, "models": models}
    save_catalog_raw(catalog)
    return dict(row)


__all__ = [
    "HUB_LOCAL_ID", "JOBS", "JobRegistry", "SPEECH_KINDS", "IMAGE_KINDS", "SPECIAL_KINDS", "LocalModelError",
    "LocalModelNotFound",
    "RuntimeClient", "catalog_set_enabled", "ensure_hub_local_backend", "model_name",
    "ollama_base_url", "ollama_delete", "ollama_list", "ollama_models", "ollama_pull_stream",
    "ollama_show", "runtime_configured", "runtime_settings", "server_status", "start_ollama_pull",
]
