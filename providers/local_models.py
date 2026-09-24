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
  loaded usable as the provider ``hub-local``.
"""
from __future__ import annotations

import json
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

#: The custom backend id the hub runtime is registered under.
HUB_LOCAL_ID = "hub-local"
HUB_LOCAL_LABEL = "Hub runtime"

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


# ── Jobs ─────────────────────────────────────────────────────────────────────

class JobRegistry:
    """Long operations (a pull) and their progress, for the UI to poll.

    A job is driven by a stream of progress dicts in the shape Ollama's pull
    emits: ``status`` is the current step, ``digest``/``completed``/``total``
    describe one layer. Layers are summed, so the job's ``completed`` and
    ``total`` cover the whole model. The stream function is injected, which
    is what lets a test drive a job with a list of fake events.
    """

    def __init__(self, keep: int = JOBS_KEPT) -> None:
        self.keep = keep
        self._jobs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def create(self, kind: str, name: str) -> Dict[str, Any]:
        job = {"id": uuid.uuid4().hex[:12], "kind": kind, "name": name, "status": "queued",
               "completed": 0, "total": 0, "percent": 0.0, "message": "", "error": None,
               "started_at": _now(), "finished_at": None}
        with self._lock:
            self._jobs[job["id"]] = job
            self._trim()
        return dict(job)

    def _trim(self) -> None:
        # Oldest finished jobs go first; a running job is never forgotten.
        while len(self._jobs) > self.keep:
            victim = next((jid for jid, j in self._jobs.items()
                           if j["status"] in ("done", "error")), None)
            if victim is None:
                break
            self._jobs.pop(victim, None)
            self._threads.pop(victim, None)

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update(fields)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self) -> List[Dict[str, Any]]:
        """Newest first."""
        with self._lock:
            return [dict(j) for j in reversed(self._jobs.values())]

    def start(self, kind: str, name: str, stream_fn: Callable[[], Iterable[Dict[str, Any]]],
              *, background: bool = True) -> str:
        """Create a job and drive it with ``stream_fn()``: in a daemon thread,
        or inline when ``background`` is False (tests)."""
        job = self.create(kind, name)
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


#: The hub's own jobs (Ollama pulls). Downloads into the runtime live in the
#: runtime service's registry and are read through ``RuntimeClient``.
JOBS = JobRegistry()


def start_ollama_pull(name: str, *, registry: Optional[JobRegistry] = None,
                      stream_fn: Optional[Callable[[], Iterable[Dict[str, Any]]]] = None,
                      background: bool = True) -> str:
    """Start pulling ``name`` into Ollama; returns the job id."""
    reg = registry or JOBS
    fn = stream_fn or (lambda: ollama_pull_stream(name))
    return reg.start("ollama_pull", name, fn, background=background)


# ── The hub runtime (deploy/models) ──────────────────────────────────────────

def runtime_settings() -> Dict[str, Any]:
    """URL, token and timeout of the runtime service, from settings."""
    from common.config import settings
    return {
        "url": str(getattr(settings, "models_url", "") or "").strip().rstrip("/"),
        "token": str(getattr(settings, "models_token", "") or "").strip(),
        "timeout": float(getattr(settings, "models_timeout", 60.0) or 60.0),
    }


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

    def _request(self, method: str, path: str, *, timeout: Optional[float] = None,
                 **kwargs: Any) -> Any:
        import httpx
        if not self.url:
            raise LocalModelError("The model runtime is not configured (AGENTS_HUB_MODELS_URL).")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            with httpx.Client(transport=self.transport, timeout=timeout or self.timeout) as client:
                resp = client.request(method, f"{self.url}{path}", headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise LocalModelError(
                f"The model runtime at {self.url} is unreachable: {type(exc).__name__}: {exc}")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except Exception:  # noqa: BLE001 - any non-JSON error body
                detail = None
            raise LocalModelError(str(detail or resp.text[:300] or f"HTTP {resp.status_code}"),
                                  resp.status_code)
        return resp.json() if resp.content else {}

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/healthz")

    def models(self) -> List[Dict[str, Any]]:
        return list((self._request("GET", "/models") or {}).get("models") or [])

    def memory(self) -> Dict[str, Any]:
        return self._request("GET", "/memory")

    def download(self, repo: str, file: str, revision: str = "main") -> Dict[str, Any]:
        return self._request("POST", "/download",
                             json={"repo": repo, "file": file, "revision": revision})

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
        return self._request("POST", "/unload", json={"file": file})

    def delete(self, file: str) -> Dict[str, Any]:
        return self._request("DELETE", f"/models/{file}")

    def hf_files(self, repo: str, revision: str = "main") -> Dict[str, Any]:
        return self._request("GET", "/hf/files", params={"repo": repo, "revision": revision})

    def structure(self, file: str) -> Dict[str, Any]:
        return self._request("GET", f"/models/{file}/structure")


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
    "HUB_LOCAL_ID", "JOBS", "JobRegistry", "LocalModelError", "LocalModelNotFound",
    "RuntimeClient", "catalog_set_enabled", "ensure_hub_local_backend", "model_name",
    "ollama_base_url", "ollama_delete", "ollama_list", "ollama_models", "ollama_pull_stream",
    "ollama_show", "runtime_configured", "runtime_settings", "start_ollama_pull",
]
