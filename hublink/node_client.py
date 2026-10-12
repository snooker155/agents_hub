"""
This hub as a node of the hub it is linked to.

A loop in a daemon thread (started by ``hublink/runtime.py`` when the link
says ``node.enabled``): say hello with what this machine offers, then long
poll ``/api/node-link/poll`` for requests and answer each one by calling this
hub's own HTTP server on the loopback address, streaming the response back
in pieces. Nothing listens; the node dials out, so a laptop behind NAT works.

What the remote may reach is an allowlist (:data:`ALLOWED`), and the models
it may name are those of the shared providers (``node.shared_providers`` in
the link record, the local ones by default): a request for
``openai/gpt-...`` through the node is refused here, before it costs this
hub's key anything. ``/api/node-link/self/exec`` runs a snippet in this
hub's own sandbox (whatever ``CODE_RUNNER_PROVIDER`` names), guarded by a
per-process secret the loop sets in the request, so nobody but this loop can
call it.
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
import socket
import threading
import time
from typing import Any, Dict, List, Optional

from hublink import store
from hublink.remote import POLL_TIMEOUT_SECONDS, Remote, RemoteError

log = logging.getLogger(__name__)

HELLO_EVERY_SECONDS = 300.0
POLL_WAIT_SECONDS = 25
BACKOFF_MAX_SECONDS = 60.0
CHUNK_BYTES = 16 * 1024

#: Paths the remote hub may reach through this node, with the methods.
ALLOWED = {
    "/v1/models": ("GET",),
    "/v1/chat/completions": ("POST",),
    "/v1/completions": ("POST",),
    "/v1/embeddings": ("POST",),
    "/api/node-link/self/exec": ("POST",),
}

#: The header and secret that let the loop, and only the loop, reach the exec
#: route on this hub.
SELF_HEADER = "X-Node-Link-Self"
_self_secret = secrets.token_urlsafe(24)


def self_secret() -> str:
    return _self_secret


def _hub_base() -> str:
    from common.hostnet import hub_base_url
    return hub_base_url()


def _hub_headers() -> Dict[str, str]:
    from common.auth import auth_headers
    headers = dict(auth_headers())
    if not headers:
        # ``multi`` mode without a shared token: the service credential this
        # process minted, which the identity layer accepts from a hub process.
        try:
            from common import identity
            if identity.current_mode() == "multi":
                headers["Authorization"] = f"Bearer {identity.service_token()}"
        except Exception:  # noqa: BLE001 - no credential means single mode, where none is needed
            log.debug("node client: no hub credential", exc_info=True)
    headers[SELF_HEADER] = _self_secret
    return headers


# ── what this node offers ────────────────────────────────────────────────────

def shared_models(doc: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The served models of the shared providers, as ``/v1/models`` objects."""
    doc = doc if doc is not None else store.get()
    if not doc or not (doc.get("node") or {}).get("share_models", True):
        return []
    providers = set(store.shared_providers(doc))
    try:
        from routes import openai_compat
        served, _ = openai_compat._served()
    except Exception:  # noqa: BLE001 - no catalog means nothing to share
        log.debug("node client: could not list served models", exc_info=True)
        return []
    out = []
    for m in served:
        if str(m.get("provider") or "") in providers:
            out.append({"id": m["id"], "context_window": int(m.get("context_window") or 0),
                        "label": str(m.get("model") or "")})
    return out


def capabilities(doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    doc = doc if doc is not None else store.get()
    node = (doc or {}).get("node") or {}
    caps: Dict[str, Any] = {"sandbox": False, "sandbox_provider": "", "docker": False}
    if node.get("sandbox", True):
        try:
            from sandbox import registry
            from common.config import settings
            name = registry.resolve(None, settings)
            ok, _ = registry.get_provider(name).is_available()
            caps["sandbox"] = bool(ok)
            caps["sandbox_provider"] = name
        except Exception:  # noqa: BLE001 - a sandbox that cannot be resolved is not offered
            log.debug("node client: sandbox probe failed", exc_info=True)
    try:
        from sandbox.docker import docker_available
        caps["docker"] = bool(docker_available())
    except Exception:  # noqa: BLE001 - no docker module or daemon
        caps["docker"] = False
    return caps


def announcement(doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    doc = doc if doc is not None else store.get()
    try:
        from common.members import app_version
        version = app_version()
    except Exception:  # noqa: BLE001 - version is informational
        version = ""
    return {
        "name": (doc or {}).get("name") or store.default_node_name(),
        "host": socket.gethostname(),
        "version": version,
        "models": shared_models(doc),
        "capabilities": capabilities(doc),
    }


# ── answering one request ────────────────────────────────────────────────────

def _model_allowed(doc: Dict[str, Any], body: bytes) -> Optional[str]:
    """None when the model named in a completion body is shared; else why not."""
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except ValueError:
        return "the request body is not JSON"
    model = str((data or {}).get("model") or "")
    if "/" not in model:
        return "name the model as provider/model; this node shares only its local providers"
    provider = model.split("/", 1)[0]
    if provider not in set(store.shared_providers(doc)):
        return f"provider '{provider}' is not shared by this node"
    return None


def _filter_models(doc: Dict[str, Any], content: bytes) -> bytes:
    try:
        data = json.loads(content.decode("utf-8"))
    except ValueError:
        return content
    providers = set(store.shared_providers(doc))
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        data["data"] = [m for m in data["data"]
                        if isinstance(m, dict) and str(m.get("provider") or str(m.get("id", "")).split("/")[0]) in providers]
    return json.dumps(data).encode("utf-8")


def _refusal(status: int, message: str) -> Dict[str, Any]:
    body = json.dumps({"error": {"message": message, "type": "node_refused"}}).encode("utf-8")
    return {"status": status, "headers": {"Content-Type": "application/json"}, "chunks": [body]}


class Job:
    """One request from the remote, as the poll handed it over."""

    def __init__(self, raw: Dict[str, Any]) -> None:
        self.id = str(raw.get("job_id") or "")
        self.method = str(raw.get("method") or "GET").upper()
        self.path = str(raw.get("path") or "")
        self.headers = {str(k): str(v) for k, v in (raw.get("headers") or {}).items()}
        self.body = base64.b64decode(raw.get("body_b64") or "")
        self.timeout = float(raw.get("timeout") or 120.0)


def answer(job: Job, doc: Dict[str, Any]) -> Dict[str, Any]:
    """Execute ``job`` against this hub. Returns ``{status, headers, chunks}``
    where ``chunks`` is an iterable of byte pieces (streamed when the hub
    streams)."""
    path = job.path.split("?", 1)[0]
    allowed = ALLOWED.get(path)
    if not allowed or job.method not in allowed:
        return _refusal(403, f"{job.method} {path} is not offered by this node")
    node = doc.get("node") or {}
    if path.startswith("/v1/") and not node.get("share_models", True):
        return _refusal(403, "this node does not share its models")
    if path == "/api/node-link/self/exec" and not node.get("sandbox", True):
        return _refusal(403, "this node does not offer its sandbox")
    if path in ("/v1/chat/completions", "/v1/completions", "/v1/embeddings"):
        why = _model_allowed(doc, job.body)
        if why:
            return _refusal(403, why)

    import requests
    headers = {k: v for k, v in job.headers.items()
               if k.lower() not in ("host", "authorization", "content-length", "connection",
                                    "transfer-encoding", SELF_HEADER.lower())}
    headers.update(_hub_headers())
    url = f"{_hub_base()}{job.path}"
    try:
        resp = requests.request(job.method, url, headers=headers, data=job.body or None,
                                stream=True, timeout=(10, job.timeout))
    except requests.exceptions.RequestException as exc:
        return _refusal(502, f"this node could not reach its own hub: {exc}")
    out_headers = {k: v for k, v in resp.headers.items()
                   if k.lower() in ("content-type", "cache-control", "x-request-id")}
    if path == "/v1/models":
        content = _filter_models(doc, resp.content)
        return {"status": resp.status_code, "headers": out_headers, "chunks": [content]}
    return {"status": resp.status_code, "headers": out_headers,
            "chunks": resp.iter_content(chunk_size=CHUNK_BYTES)}


# ── the loop ─────────────────────────────────────────────────────────────────

class NodeClient:
    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.state: Dict[str, Any] = {"running": False, "connected": False, "last_error": "",
                                      "last_hello_at": None, "answered": 0, "node_id": ""}

    # -- control --

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hublink-node", daemon=True)
        self._thread.start()
        self.state["running"] = True

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.state["running"] = False
        self.state["connected"] = False

    def hello_now(self) -> bool:
        """Say hello once, now, on the caller's thread. False when not linked
        as a node or the remote refused."""
        doc = store.get()
        if doc is None or not store.node_enabled(doc):
            return False
        try:
            self._hello(doc)
            return True
        except RemoteError as exc:
            self.state["last_error"] = str(exc)
            return False

    # -- the thread --

    def _remote(self, doc: Dict[str, Any]) -> Remote:
        return Remote(doc["url"], (doc.get("node") or {}).get("token") or "")

    def _hello(self, doc: Dict[str, Any]) -> None:
        reply = self._remote(doc).post_json("/api/node-link/hello", announcement(doc))
        self.state.update(connected=True, last_error="", last_hello_at=time.time(),
                          node_id=str((reply or {}).get("node_id") or ""))

    def _run(self) -> None:
        backoff = 1.0
        last_hello = 0.0
        while not self._stop.is_set():
            doc = store.get()
            if doc is None or not store.node_enabled(doc):
                self.state["connected"] = False
                self._wake.wait(5.0)
                self._wake.clear()
                continue
            try:
                if time.time() - last_hello > HELLO_EVERY_SECONDS:
                    self._hello(doc)
                    last_hello = time.time()
                jobs = self._remote(doc).get_json(
                    f"/api/node-link/poll?wait={POLL_WAIT_SECONDS}", timeout=POLL_TIMEOUT_SECONDS)
                backoff = 1.0
                self.state["connected"] = True
                for raw in (jobs or {}).get("jobs") or []:
                    job = Job(raw)
                    threading.Thread(target=self._serve, args=(job, doc), daemon=True,
                                     name=f"hublink-job-{job.id[-6:]}").start()
            except RemoteError as exc:
                self.state.update(connected=False, last_error=str(exc))
                if exc.status in (401, 403):
                    last_hello = 0.0  # the token changed or the node was removed; retry slowly
                    backoff = BACKOFF_MAX_SECONDS
                log.info("hub link node: %s (retry in %.0fs)", exc, backoff)
                self._wake.wait(backoff)
                self._wake.clear()
                backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)
            except Exception:  # noqa: BLE001 - the loop must survive anything
                log.warning("hub link node loop failed", exc_info=True)
                self._wake.wait(backoff)
                self._wake.clear()
                backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)

    def _serve(self, job: Job, doc: Dict[str, Any]) -> None:
        rm = self._remote(doc)
        try:
            result = answer(job, doc)
        except Exception as exc:  # noqa: BLE001 - reported to the remote as a failed job
            log.debug("hub link node: job %s failed", job.id, exc_info=True)
            result = _refusal(500, f"{type(exc).__name__}: {exc}")
        error: Optional[str] = None
        try:
            rm.post_json(f"/api/node-link/jobs/{job.id}/head",
                         {"status": int(result["status"]), "headers": dict(result.get("headers") or {})})
            for piece in result.get("chunks") or ():
                if not piece:
                    continue
                rm.post_json(f"/api/node-link/jobs/{job.id}/chunk",
                             {"data": base64.b64encode(piece).decode("ascii")})
        except RemoteError as exc:
            error = str(exc)
            self.state["last_error"] = error
        except Exception as exc:  # noqa: BLE001 - the hub's own stream broke
            error = f"{type(exc).__name__}: {exc}"
        try:
            rm.post_json(f"/api/node-link/jobs/{job.id}/end", {"error": error} if error else {})
        except RemoteError as exc:
            self.state["last_error"] = str(exc)
        self.state["answered"] = int(self.state.get("answered") or 0) + 1


__all__ = ["ALLOWED", "Job", "NodeClient", "SELF_HEADER", "announcement", "answer",
           "capabilities", "self_secret", "shared_models"]
