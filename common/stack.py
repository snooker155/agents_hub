"""The stack as an operator sees it: every shared service the hub talks to.

``common.health`` reports the hub's own state and ``common.members`` the hub's
own processes. What neither says is whether the things around them answer:
Postgres as a server, Redis, the object store, the identity provider, the
browser service, the model runtime, the egress proxy, and the compose
project's own containers. This module probes each one and returns the same
small shape, so the Cluster page's service map, the Health page's card and the
doctor's checks read one source instead of three copies of the same probe.

Each component is ``{id, kind, label, status, summary, detail, load,
capacity, unit, latency_ms}``. ``status`` is ``ok``, ``warn``, ``fail``,
``off`` (not configured, nothing to watch), or ``unknown`` (could not be
told, with the reason in ``summary``). ``load`` and ``capacity`` are numbers
or None; ``unit`` says what they count.

Every probe has a short timeout, runs concurrently with the others, and never
raises: an error is a ``fail`` or ``unknown`` result carrying its reason. The
database probe runs on the calling thread on purpose: connections are
per-thread, and a pool thread would leave one behind.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, cast

log = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 3.0
#: A pool thread that overruns this is reported unknown rather than awaited.
JOIN_TIMEOUT_SECONDS = PROBE_TIMEOUT_SECONDS + 1.5

#: Compose services that run once and exit; "not running" is their normal state.
ONE_SHOT_SERVICES = ("setup", "minio-init", "keycloak-db-init")
#: Compose service -> the setup wizard's .env key for how many should run.
WANTED_REPLICAS = {"backend-api": "AGENTS_HUB_SETUP_API_REPLICAS",
                   "worker": "AGENTS_HUB_SETUP_WORKER_REPLICAS"}


def _component(id_: str, kind: str, label: str, status: str, summary: str, *,
               detail: Optional[Dict[str, Any]] = None, load: Optional[float] = None,
               capacity: Optional[float] = None, unit: str = "",
               latency_ms: Optional[float] = None) -> Dict[str, Any]:
    return {"id": id_, "kind": kind, "label": label, "status": status, "summary": summary,
            "detail": detail or {}, "load": load, "capacity": capacity, "unit": unit,
            "latency_ms": None if latency_ms is None else round(latency_ms, 1)}


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}".rstrip(": ")


# ── database ─────────────────────────────────────────────────────────────────

def probe_database() -> Dict[str, Any]:
    from common import db
    started = time.perf_counter()
    try:
        dialect = db.dialect()
        conn = db.get_conn()
        if dialect != "postgres":
            from common.health import _database_bytes
            size, wal = _database_bytes()
            return _component("database", "database", "SQLite", "ok",
                              "SQLite file in the state directory.",
                              detail={"dialect": dialect, "size_bytes": size, "wal_bytes": wal},
                              latency_ms=_ms(started))
        version = str(conn.execute("SHOW server_version").fetchone()[0])
        max_conn = int(conn.execute("SHOW max_connections").fetchone()[0])
        used = int(conn.execute(
            "SELECT numbackends FROM pg_stat_database WHERE datname = current_database()"
        ).fetchone()[0] or 0)
        size = int(conn.execute("SELECT pg_database_size(current_database())").fetchone()[0] or 0)
        status, summary = "ok", f"PostgreSQL {version}, {used} of {max_conn} connections."
        if max_conn and used / max_conn >= 0.9:
            status, summary = "warn", f"PostgreSQL is near its connection limit ({used} of {max_conn})."
        return _component("database", "database", "PostgreSQL", status, summary,
                          detail={"dialect": dialect, "server_version": version, "pool_size": db.pool_size(),
                                  "size_bytes": size, "connections": used, "max_connections": max_conn},
                          load=used, capacity=max_conn, unit="connections", latency_ms=_ms(started))
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        log.debug("database probe failed", exc_info=True)
        return _component("database", "database", "Database", "fail",
                          f"The database did not answer: {_err(exc)}", detail={"error": _err(exc)})


# ── broker (Redis) ───────────────────────────────────────────────────────────

def _broker_url() -> str:
    from common.config import live_setting
    return live_setting("AGENTS_HUB_BROKER_URL").strip()


def _bridge_expected() -> bool:
    """Whether this process should hold a bridge to Redis.

    Only the API process starts one (dashboard/backend/main.py's lifespan),
    and that process, like the worker, runs a ``MemberBeat`` thread. A CLI
    process (``ah doctor`` over the direct backend) runs neither, so a missing
    bridge there is how it is meant to be, not a fault. The worker has a beat
    but never a bridge, so its role rules it out."""
    from common.members import MemberBeat
    return any(isinstance(t, MemberBeat) and t.is_alive() and t.role != "worker"
               for t in threading.enumerate())


def probe_broker() -> Dict[str, Any]:
    url = _broker_url()
    if not url:
        return _component("broker", "broker", "Redis", "off", "No broker configured (AGENTS_HUB_BROKER_URL).")
    try:
        from common import broker_bridge

        # Redis is pinged whether or not this process holds a bridge: the
        # question is whether Redis answers, and a CLI process never has one.
        bridge = broker_bridge.get_redis() is not None
        import redis  # pyright: ignore[reportMissingImports] - optional (requirements-broker.txt)
        # The bridge's own client is async and bound to its loop; a probe from
        # a worker thread uses a short-lived sync client on the same URL.
        client = redis.from_url(url, socket_timeout=PROBE_TIMEOUT_SECONDS,
                                socket_connect_timeout=PROBE_TIMEOUT_SECONDS)
        try:
            started = time.perf_counter()
            client.ping()
            latency = _ms(started)
            info: Dict[str, Any] = {}
            try:
                info = cast(Dict[str, Any], client.info()) or {}
            except Exception:  # noqa: BLE001 - INFO may be disabled; PING already answered
                log.debug("redis INFO failed", exc_info=True)
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                log.debug("redis close failed", exc_info=True)
        clients = info.get("connected_clients")
        status, summary = "ok", f"Redis answered in {latency:.0f} ms."
        if not bridge and _bridge_expected():
            # Redis is up, yet this replica never connected (it was down when
            # the backend started, and the bridge does not retry a failed
            # start): events published here stay on this replica.
            status = "warn"
            summary = (f"Redis answered in {latency:.0f} ms, but this replica's bridge is not "
                       "connected, so its live events do not reach other replicas.")
        return _component("broker", "broker", "Redis", status, summary,
                          detail={"bridge": bridge, "connected_clients": clients,
                                  "used_memory_human": info.get("used_memory_human"),
                                  "redis_version": info.get("redis_version")},
                          load=clients, unit="connections", latency_ms=latency)
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        log.debug("broker probe failed", exc_info=True)
        return _component("broker", "broker", "Redis", "fail", f"Redis did not answer: {_err(exc)}",
                          detail={"error": _err(exc)})


# ── object store ─────────────────────────────────────────────────────────────

def probe_blob_store() -> Dict[str, Any]:
    from common import blobs
    if not blobs.configured():
        return _component("blob_store", "blob_store", "Object store", "off",
                          "Files stay on local disk (AGENTS_HUB_BLOB_URL not set).")
    started = time.perf_counter()
    try:
        store = blobs.store()
        probe = getattr(store, "probe", None)
        if probe is None:
            return _component("blob_store", "blob_store", "Object store", "unknown",
                              "This store has no round-trip probe.")
        # The store's own timeout keeps a black-holed endpoint from holding
        # this thread for boto3's default minute per call.
        detail = probe(timeout=PROBE_TIMEOUT_SECONDS)
        latency = _ms(started)
        if not detail.get("roundtrip_ok"):
            return _component("blob_store", "blob_store", "Object store", "fail",
                              "The object store returned different bytes than were written.",
                              detail=detail, latency_ms=latency)
        return _component("blob_store", "blob_store", "Object store", "ok",
                          f"Write, read and delete worked in {latency:.0f} ms.",
                          detail=detail, latency_ms=latency)
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        log.debug("blob store probe failed", exc_info=True)
        return _component("blob_store", "blob_store", "Object store", "fail",
                          f"The object store did not answer: {_err(exc)}", detail={"error": _err(exc)})


# ── identity provider ────────────────────────────────────────────────────────

def probe_oidc() -> Dict[str, Any]:
    from common.edition import enterprise_available
    if not enterprise_available():
        return _component("oidc", "oidc", "Identity provider", "off",
                          "Single sign-on is not part of this edition.")
    try:
        from ee import oidc
        if not oidc.enabled():
            return _component("oidc", "oidc", "Identity provider", "off", "Single sign-on is not configured.")
        started = time.perf_counter()
        try:
            oidc.discovery(force=True)
        except Exception as exc:  # noqa: BLE001 - reported as the probe's result
            return _component("oidc", "oidc", "Identity provider", "fail",
                              f"The identity provider did not answer: {_err(exc)}",
                              detail={"issuer": oidc.issuer(), "internal_url": oidc.internal_url(),
                                      "error": _err(exc)})
        latency = _ms(started)
        return _component("oidc", "oidc", "Identity provider", "ok",
                          f"Discovery answered in {latency:.0f} ms.",
                          detail={"issuer": oidc.issuer(), "internal_url": oidc.internal_url()},
                          latency_ms=latency)
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        log.debug("oidc probe failed", exc_info=True)
        return _component("oidc", "oidc", "Identity provider", "unknown", _err(exc))


# ── browser service ──────────────────────────────────────────────────────────

def probe_browser() -> Dict[str, Any]:
    try:
        from common import browser_service
        url = browser_service.configured_url()
        if not url:
            return _component("browser", "browser", "Browser service", "off",
                              "No browser service configured (AGENTS_HUB_BROWSER_URL).")
        started = time.perf_counter()
        res = browser_service.healthz(url, PROBE_TIMEOUT_SECONDS)
        latency = _ms(started)
        if not res.get("reachable"):
            return _component("browser", "browser", "Browser service", "fail",
                              f"The browser service is unreachable: {res.get('error')}.",
                              detail={"url": url, "error": res.get("error")})
        sessions, cap = res.get("sessions"), res.get("max_sessions")
        status = "warn" if (isinstance(sessions, (int, float)) and isinstance(cap, (int, float))
                            and cap and sessions >= cap) else "ok"
        return _component("browser", "browser", "Browser service", status,
                          "All browser sessions are in use." if status == "warn"
                          else f"The browser service answered in {latency:.0f} ms.",
                          detail={"url": url}, load=sessions, capacity=cap, unit="sessions",
                          latency_ms=latency)
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        log.debug("browser probe failed", exc_info=True)
        return _component("browser", "browser", "Browser service", "unknown", _err(exc))


# ── model runtime ────────────────────────────────────────────────────────────

def probe_models_runtime() -> Dict[str, Any]:
    label = "Model runtime"
    try:
        from providers import local_models as lm
        cfg = lm.runtime_settings()
        url = cfg["url"]
        if not url:
            return _component("models_runtime", "models_runtime", label, "off",
                              "No model runtime (AGENTS_HUB_MODELS_URL unset, managed runtime off).")
        import httpx
        started = time.perf_counter()
        try:
            resp = httpx.get(f"{url}/healthz", timeout=PROBE_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - reported as the probe's result
            return _component("models_runtime", "models_runtime", label, "fail",
                              f"The model runtime is unreachable: {type(exc).__name__}.",
                              detail={"url": url, "managed": cfg.get("managed", False)})
        latency = _ms(started)
        if resp.status_code >= 400:
            return _component("models_runtime", "models_runtime", label, "fail",
                              f"The model runtime answered HTTP {resp.status_code}.",
                              detail={"url": url}, latency_ms=latency)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        loaded = body.get("loaded")
        count = len(loaded) if isinstance(loaded, (list, dict)) else (loaded if isinstance(loaded, int) else None)
        return _component("models_runtime", "models_runtime", label, "ok",
                          f"The model runtime answered in {latency:.0f} ms.",
                          detail={"url": url, "managed": cfg.get("managed", False),
                                  "mode": body.get("mode"), "loaded": loaded},
                          load=count, capacity=body.get("max_loaded"), unit="models", latency_ms=latency)
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        log.debug("models runtime probe failed", exc_info=True)
        return _component("models_runtime", "models_runtime", label, "unknown", _err(exc))


# ── egress proxy ─────────────────────────────────────────────────────────────

def probe_egress_proxy() -> Dict[str, Any]:
    label = "Egress proxy"
    try:
        from common.config import hub_role
        from environments import egress
        if not egress.enabled():
            return _component("egress_proxy", "egress_proxy", label, "off",
                              "The egress proxy is off (AGENTS_HUB_EGRESS_PROXY).")
        if egress.running() is not None:
            return _component("egress_proxy", "egress_proxy", label, "ok",
                              "The egress proxy is listening in this process.",
                              detail={"port": egress.port()})
        if hub_role() == "api":
            return _component("egress_proxy", "egress_proxy", label, "unknown",
                              "Api-only processes do not run the proxy; a worker or an all-in-one process does.",
                              detail={"role": "api"})
        return _component("egress_proxy", "egress_proxy", label, "warn",
                          "The egress proxy is on but not listening in this process.",
                          detail={"port": egress.port()})
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        log.debug("egress probe failed", exc_info=True)
        return _component("egress_proxy", "egress_proxy", label, "unknown", _err(exc))


# ── the compose project's own containers ─────────────────────────────────────

def _docker(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          timeout=PROBE_TIMEOUT_SECONDS, check=False)


def _parse_labels(raw: Any) -> Dict[str, str]:
    """``docker ps --format json`` gives Labels as one ``k=v,k=v`` string."""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    out: Dict[str, str] = {}
    for part in str(raw or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def parse_compose_ps(output: str, wanted: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    """Per compose service: how many containers run of how many exist, and
    how many the setup wizard asked for. ``docker ps --format json`` prints
    one JSON object per line."""
    wanted = wanted or {}
    services: Dict[str, Dict[str, Any]] = {}
    for line in (output or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        labels = _parse_labels(row.get("Labels"))
        name = labels.get("com.docker.compose.service") or str(row.get("Names") or "").split(",")[0]
        if not name:
            continue
        rec = services.setdefault(name, {"service": name, "running": 0, "total": 0, "states": []})
        rec["total"] += 1
        state = str(row.get("State") or "").lower()
        rec["states"].append(state)
        if state == "running":
            rec["running"] += 1
    out = []
    for name in sorted(services):
        rec = services[name]
        states = set(rec.pop("states"))
        rec["wanted"] = wanted.get(name)
        rec["state"] = "running" if rec["running"] and rec["running"] == rec["total"] else (
            "partial" if rec["running"] else (sorted(states)[0] if states else "unknown"))
        out.append(rec)
    return out


def _wanted_replicas() -> Dict[str, int]:
    from common.config import live_setting
    if "ha" not in [p.strip() for p in live_setting("COMPOSE_PROFILES").split(",")]:
        return {}
    out: Dict[str, int] = {}
    for service, key in WANTED_REPLICAS.items():
        try:
            n = int(live_setting(key))
        except ValueError:
            continue
        if n > 0:
            out[service] = n
    return out


def probe_compose() -> Dict[str, Any]:
    label = "Compose stack"

    def unknown(reason: str) -> Dict[str, Any]:
        return _component("compose", "compose", label, "unknown", reason)

    try:
        if not shutil.which("docker"):
            return unknown("No docker CLI in this process, so the compose containers cannot be listed.")
        host = os.environ.get("HOSTNAME") or socket.gethostname()
        me = _docker(["inspect", "--format", "{{json .Config.Labels}}", host])
        if me.returncode != 0:
            return unknown("This process is not a container the docker daemon knows, or no daemon answers.")
        try:
            project = (json.loads(me.stdout.strip() or "null") or {}).get("com.docker.compose.project")
        except ValueError:
            project = None
        if not project:
            return unknown("This process does not run in a compose project.")
        ps = _docker(["ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                      "--format", "json"])
        if ps.returncode != 0:
            return unknown(f"docker ps failed: {ps.stderr.strip()[:200]}")
        wanted = _wanted_replicas()
        services = parse_compose_ps(ps.stdout, wanted)
        problems = []
        for s in services:
            if s["wanted"] and s["running"] < s["wanted"]:
                problems.append(f"{s['service']}: {s['running']} of {s['wanted']} running")
            elif s["service"] not in ONE_SHOT_SERVICES and s["total"] and not s["running"]:
                problems.append(f"{s['service']} is not running")
        running = sum(s["running"] for s in services)
        total = sum(s["total"] for s in services)
        status = "warn" if problems else "ok"
        joined = "; ".join(problems) + "."
        summary = joined[:1].upper() + joined[1:] if problems else \
            f"{running} of {total} containers running in project {project}."
        return _component("compose", "compose", label, status, summary,
                          detail={"project": project, "services": services},
                          load=running, capacity=total, unit="containers")
    except subprocess.TimeoutExpired:
        return unknown("The docker daemon did not answer in time.")
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        log.debug("compose probe failed", exc_info=True)
        return unknown(_err(exc))


# ── all of them ──────────────────────────────────────────────────────────────

#: Everything but the database, which runs on the calling thread. Probes are
#: looked up by name at call time so a test can replace one.
_POOLED = [("broker", "Redis", "probe_broker"), ("blob_store", "Object store", "probe_blob_store"),
           ("oidc", "Identity provider", "probe_oidc"), ("browser", "Browser service", "probe_browser"),
           ("models_runtime", "Model runtime", "probe_models_runtime"),
           ("egress_proxy", "Egress proxy", "probe_egress_proxy"), ("compose", "Compose stack", "probe_compose")]


def components() -> List[Dict[str, Any]]:
    """Every probe's result, in a fixed order. Never raises."""
    pool = ThreadPoolExecutor(max_workers=len(_POOLED), thread_name_prefix="stack-probe")
    try:
        futures = [(id_, label, pool.submit(globals()[name])) for id_, label, name in _POOLED]
        out = [probe_database()]
        for id_, label, fut in futures:
            try:
                out.append(fut.result(timeout=JOIN_TIMEOUT_SECONDS))
            except Exception as exc:  # noqa: BLE001 - a slow or broken probe is unknown, not a crash
                out.append(_component(id_, id_, label, "unknown", f"The probe did not finish: {_err(exc)}"))
        return out
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def component(component_id: str) -> Optional[Dict[str, Any]]:
    """One probe by id, for the doctor checks that need only one.

    Bound by the same join timeout as :func:`components`: a probe whose
    client ignores its own timeout would otherwise hold ``ah doctor`` and
    GET /api/health/doctor for as long as the client cares to wait."""
    if component_id == "database":
        return probe_database()
    for id_, label, name in _POOLED:
        if id_ == component_id:
            pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stack-probe")
            try:
                return pool.submit(globals()[name]).result(timeout=JOIN_TIMEOUT_SECONDS)
            except Exception as exc:  # noqa: BLE001 - a slow or broken probe is unknown, not a crash
                return _component(id_, id_, label, "unknown", f"The probe did not finish: {_err(exc)}")
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
    return None


__all__ = ["components", "component", "parse_compose_ps", "probe_database", "probe_broker",
           "probe_blob_store", "probe_oidc", "probe_browser", "probe_models_runtime",
           "probe_egress_proxy", "probe_compose"]
