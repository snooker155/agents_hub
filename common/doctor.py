"""
The doctor: a judgement over the health snapshot.

``common.health.snapshot`` reports state (row counts, liveness, sizes) and
leaves the reading to whoever looks. That is right for the Health page and
for an agent that wants the raw numbers, but "is anything wrong, and what do I
do about it" needs someone to have decided, once, what each number means.
This module is that decision: a fixed list of checks, each a small function
over the snapshot plus a probe of its own, each returning ``ok``, ``warn``,
``fail`` or ``skip`` with one sentence and a link to the docs section that
says how to fix it (docs/service-health.md, "Doctor").

Three callers share it: ``GET /api/health/doctor``, ``ah doctor`` and the
Service Agent's ``run_diagnostics`` tool; the system workspace's doctor agent
turns its findings into tasks (docs/system-workspace.md).

Never raises. A check that errors reports ``fail`` with the error as its
summary: a doctor that crashes on a broken install is useless exactly when it
is needed.
"""
from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

DOC = "service-health"

#: Status order for the overall result; ``skip`` never counts.
_RANK = {"ok": 0, "warn": 1, "fail": 2}

# Thresholds, named so the docs and the tests can refer to them.
DISK_WARN_BYTES = 2 * 1024 ** 3
DISK_FAIL_BYTES = 500 * 1024 ** 2
QUEUE_WARN_DEPTH = 10
QUEUE_WARN_AGE_SECONDS = 300.0
OUTBOX_WARN_PENDING = 100
PROVIDER_TIMEOUT_SECONDS = 5.0
BROWSER_TIMEOUT_SECONDS = 3.0
MODELS_TIMEOUT_SECONDS = 3.0

Result = Tuple[str, str, Dict[str, Any]]


def anchor_for(check_id: str) -> str:
    """The heading slug of a check's section: ``### Check: disk`` -> ``check-disk``."""
    return "check-" + check_id.replace("_", "-")


# ── migrations ───────────────────────────────────────────────────────────────

def check_migrations(snap: Dict[str, Any]) -> Result:
    from common import db
    from common import migrations

    conn = db.get_conn()
    dialect = db.dialect()
    applied = migrations.applied_versions(conn, dialect)
    available = [mg.version for mg in migrations.select_for(dialect)]
    top = max(applied) if applied else 0
    pending = [v for v in available if v > top]
    unknown = [v for v in applied if v not in set(available)]
    detail = {"dialect": dialect, "applied": len(applied), "latest": max(available or [0]),
              "pending": pending, "unknown": unknown}
    if unknown:
        return ("fail", f"The database has migration(s) {unknown} this build does not know; "
                        "it was written by a newer version.", detail)
    if pending:
        return ("fail", f"{len(pending)} migration(s) are pending: {pending}.", detail)
    return ("ok", f"Schema is at version {top}, nothing pending.", detail)


# ── provider ─────────────────────────────────────────────────────────────────

def probe_provider(provider: str, timeout: float = PROVIDER_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """List the provider's models, the same request the Settings page's
    "test provider" button makes (dashboard/backend/routes/settings.py),
    synchronously and with a short timeout. Returns ``{"ok", "error",
    "latency_ms", "models"}``, or ``{"skip": reason}`` when there is nothing to
    probe."""
    import httpx
    from common.config import settings

    started = time.monotonic()
    headers: Dict[str, str] = {}
    key = ""
    if provider == "openai":
        key = settings.openai_api_key or ""
        if not key:
            return {"skip": "no OpenAI API key is set"}
        import os
        base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        url, headers = f"{base}/models", {"Authorization": f"Bearer {key}"}
    elif provider == "anthropic":
        key = settings.anthropic_api_key or ""
        if not key:
            return {"skip": "no Anthropic API key is set"}
        url = "https://api.anthropic.com/v1/models"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif provider == "google":
        key = settings.google_api_key or ""
        if not key:
            return {"skip": "no Google API key is set"}
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
    elif provider in ("ollama", "lmstudio"):
        from common.hostnet import host_service_url
        default = "http://localhost:11434" if provider == "ollama" else "http://localhost:1234"
        field = "ollama_base_url" if provider == "ollama" else "lmstudio_base_url"
        base = host_service_url(str(getattr(settings, field, "") or default)).rstrip("/")
        url = f"{base}/api/tags" if provider == "ollama" else f"{base}/v1/models"
    else:
        return {"skip": f"'{provider}' is a custom backend; test it from the Models page"}
    try:
        resp = httpx.get(url, headers=headers, timeout=timeout)
        latency = int((time.monotonic() - started) * 1000)
        if resp.status_code >= 400:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "latency_ms": latency}
        body = resp.json()
        items = body.get("data") or body.get("models") or []
        return {"ok": True, "error": None, "latency_ms": latency, "models": len(items)}
    except Exception as exc:  # noqa: BLE001 - the probe reports, it does not raise
        error = f"{type(exc).__name__}: {exc}"
        if key:
            # The Google key travels in the URL, which an error message may echo.
            error = error.replace(key, "***")
        return {"ok": False, "error": error[:300],
                "latency_ms": int((time.monotonic() - started) * 1000)}


def check_provider(snap: Dict[str, Any]) -> Result:
    providers = snap.get("providers") or {}
    provider = str(providers.get("default_provider") or "").strip()
    if not provider:
        from common.config import settings
        provider = str(settings.default_provider or "").strip()
    if not provider:
        return ("skip", "No default provider is configured.", {})
    probe = probe_provider(provider)
    detail = {"provider": provider, **{k: v for k, v in probe.items() if k != "skip"}}
    if "skip" in probe:
        return ("skip", f"Not probed: {probe['skip']}.", detail)
    if probe.get("ok"):
        return ("ok", f"{provider} answered in {probe.get('latency_ms')} ms.", detail)
    return ("fail", f"{provider} did not answer: {probe.get('error')}.", detail)


# ── cors ─────────────────────────────────────────────────────────────────────

def check_cors(snap: Dict[str, Any]) -> Result:
    """``ALLOW_ORIGINS=*`` lets any web page call the API from a browser. The
    hub drops credentials for it (dashboard/backend/main.py cors_options),
    but a bearer token stolen from a person still works from any site, which
    matters when there are people: ``multi`` mode."""
    import os
    value = os.getenv("ALLOW_ORIGINS", "").strip()
    from common import identity
    mode = identity.current_mode()
    detail = {"allow_origins": value or "(local dev defaults)", "auth_mode": mode}
    if value != "*":
        return ("ok", "CORS allows a list of origins." if value
                else "CORS allows the local dev origins only.", detail)
    if mode == "multi":
        return ("warn", "ALLOW_ORIGINS is *: any site can call the API with a stolen "
                        "token; list the dashboard origins instead.", detail)
    return ("ok", f"ALLOW_ORIGINS is * without credentials, acceptable in {mode} mode.",
            detail)


# ── stale runs and leases ────────────────────────────────────────────────────

def _age_seconds(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


def stale_leaf_runs(threshold: float) -> List[Dict[str, Any]]:
    """Agent runs in ``running`` whose heartbeat is older than ``threshold``."""
    from common import db
    rows = db.get_conn().execute(
        "SELECT run_id, agent_id, heartbeat_at FROM runs "
        "WHERE status = 'running' AND heartbeat_at IS NOT NULL").fetchall()
    out = []
    for r in rows:
        rec = dict(r)
        age = _age_seconds(rec.get("heartbeat_at"))
        if age is not None and age > threshold:
            out.append({"run_id": rec.get("run_id"), "agent_id": rec.get("agent_id"),
                        "heartbeat_age_seconds": round(age)})
    return out


def check_stale_runs(snap: Dict[str, Any]) -> Result:
    try:
        from managers.run_watchdog import RUN_HEARTBEAT_STALE_SECONDS as threshold
    except Exception:  # noqa: BLE001 - the watchdog's own default when it cannot be imported
        threshold = 180.0
    stale = stale_leaf_runs(float(threshold))
    leases = (snap.get("cluster") or {}).get("leases") or []
    expired = [lease.get("role") for lease in leases if lease.get("expired")]
    watchdog_alive = (snap.get("services") or {}).get("run_watchdog")
    detail = {"threshold_seconds": threshold, "stale_runs": len(stale), "runs": stale[:10],
              "expired_leases": expired, "run_watchdog": watchdog_alive}
    if stale and watchdog_alive is False:
        return ("fail", f"{len(stale)} run(s) stopped beating and the run watchdog is not "
                        "running to fail them.", detail)
    if stale or expired:
        parts = []
        if stale:
            parts.append(f"{len(stale)} run(s) have not beaten for over {int(threshold)} s")
        if expired:
            parts.append(f"expired lease(s): {', '.join(str(r) for r in expired)}")
        return ("warn", "; ".join(parts).capitalize() + ".", detail)
    return ("ok", "No stale runs and no expired leases.", detail)


# ── run queue ────────────────────────────────────────────────────────────────

def _live_workers() -> Optional[int]:
    try:
        from common import members
        return sum(1 for m in members.list_members(include_stopped=False)
                   if m.get("role") == "worker" and m.get("status") == "live")
    except Exception:  # noqa: BLE001 - "could not tell" is reported as None
        log.debug("member lookup failed", exc_info=True)
        return None


def check_run_queue(snap: Dict[str, Any]) -> Result:
    cluster = snap.get("cluster") or {}
    queue = cluster.get("queue")
    if queue is None:
        return ("fail", f"The launch queue could not be read: {cluster.get('queue_error')}.",
                {"error": cluster.get("queue_error")})
    queued = int(queue.get("queued") or 0)
    oldest = float(queue.get("oldest_queued_seconds") or 0.0)
    workers = _live_workers()
    detail = {"queued": queued, "leased": queue.get("leased"), "running": queue.get("running"),
              "failed": queue.get("failed"), "oldest_queued_seconds": round(oldest),
              "live_workers": workers, "role": cluster.get("role")}
    if not workers and (queued > QUEUE_WARN_DEPTH or (queued and oldest > QUEUE_WARN_AGE_SECONDS)):
        return ("warn", f"{queued} launch(es) are waiting, the oldest for {int(oldest)} s, and no "
                        "worker is alive to claim them.", detail)
    return ("ok", f"{queued} launch(es) waiting.", detail)


# ── outbox ───────────────────────────────────────────────────────────────────

def check_outbox(snap: Dict[str, Any]) -> Result:
    cluster = snap.get("cluster") or {}
    box = cluster.get("outbox")
    if box is None:
        return ("fail", f"The outbox could not be read: {cluster.get('outbox_error')}.",
                {"error": cluster.get("outbox_error")})
    pending, dead = int(box.get("pending") or 0), int(box.get("dead") or 0)
    detail = {"pending": pending, "dead": dead}
    if dead:
        return ("warn", f"{dead} notification(s) were given up on after every retry.", detail)
    if pending > OUTBOX_WARN_PENDING:
        return ("warn", f"{pending} notification(s) are waiting to be delivered.", detail)
    return ("ok", f"{pending} notification(s) pending, none given up on.", detail)


# ── disk ─────────────────────────────────────────────────────────────────────

def check_disk(snap: Dict[str, Any]) -> Result:
    from common.paths import AGENTS_HUB_ROOT
    usage = shutil.disk_usage(str(AGENTS_HUB_ROOT if Path(AGENTS_HUB_ROOT).exists()
                                  else Path(AGENTS_HUB_ROOT).parent))
    free = int(usage.free)
    detail = {"path": str(AGENTS_HUB_ROOT), "free_bytes": free, "total_bytes": int(usage.total),
              "state_bytes": (snap.get("storage") or {}).get("agents_hub_bytes")}
    gb = free / 1024 ** 3
    if free < DISK_FAIL_BYTES:
        return ("fail", f"Only {gb:.2f} GB free under the state directory.", detail)
    if free < DISK_WARN_BYTES:
        return ("warn", f"{gb:.2f} GB free under the state directory.", detail)
    return ("ok", f"{gb:.1f} GB free under the state directory.", detail)


# ── browser ──────────────────────────────────────────────────────────────────

def check_browser(snap: Dict[str, Any]) -> Result:
    from common.config import settings
    url = str(getattr(settings, "browser_url", "") or "").strip().rstrip("/")
    if not url:
        return ("skip", "The browser service is not configured.", {})
    import httpx
    try:
        resp = httpx.get(f"{url}/healthz", timeout=BROWSER_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - reported as the check's result
        return ("fail", f"The browser service at {url} is unreachable: {type(exc).__name__}.",
                {"url": url, "error": str(exc)[:300]})
    detail = {"url": url, "status_code": resp.status_code}
    if resp.status_code >= 400:
        return ("fail", f"The browser service answered HTTP {resp.status_code}.", detail)
    if not str(getattr(settings, "browser_token", "") or "").strip():
        return ("warn", "The browser service answers but AGENTS_HUB_BROWSER_TOKEN is not set.",
                detail)
    return ("ok", "The browser service answers.", detail)


# ── model runtime ────────────────────────────────────────────────────────────

def check_models_runtime(snap: Dict[str, Any]) -> Result:
    from common.config import settings
    url = str(getattr(settings, "models_url", "") or "").strip().rstrip("/")
    if not url:
        return ("skip", "The model runtime is not configured.", {})
    import httpx
    try:
        resp = httpx.get(f"{url}/healthz", timeout=MODELS_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - reported as the check's result
        return ("fail", f"The model runtime at {url} is unreachable: {type(exc).__name__}.",
                {"url": url, "error": str(exc)[:300]})
    detail: Dict[str, Any] = {"url": url, "status_code": resp.status_code}
    if resp.status_code >= 400:
        return ("fail", f"The model runtime answered HTTP {resp.status_code}.", detail)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if isinstance(body, dict):
        detail["loaded"] = body.get("loaded")
        detail["mode"] = body.get("mode")
    if not str(getattr(settings, "models_token", "") or "").strip():
        return ("warn", "The model runtime answers but AGENTS_HUB_MODELS_TOKEN is not set.", detail)
    return ("ok", "The model runtime answers.", detail)


# ── docker ───────────────────────────────────────────────────────────────────

def check_docker(snap: Dict[str, Any]) -> Result:
    from common.config import agent_execution_mode, settings
    from tools.run_code import docker_available
    mode = str(agent_execution_mode() or "local").lower()
    fallback = str(getattr(settings, "code_runner_fallback", "") or "").strip().lower()
    detail = {"available": None, "agent_execution_mode": mode, "code_runner_fallback": fallback}
    available = docker_available()
    detail["available"] = available
    if available:
        return ("ok", "Docker is available.", detail)
    if mode == "docker":
        return ("fail", "Agents are set to run in docker, but no docker daemon answers.", detail)
    if fallback != "local":
        return ("warn", "No docker daemon answers, so run_code cannot start its sandbox.", detail)
    return ("skip", "Docker is not available, and nothing configured here requires it.", detail)


# ── sandbox providers ────────────────────────────────────────────────────────

def check_sandbox(snap: Dict[str, Any]) -> Result:
    """Every sandbox/registry.py provider's availability, and whether the
    enforced docker network policy (managers.container_manager, a ``limited``
    or ``none`` environment fenced onto a no-route-out network instead of the
    ordinary bridge) is actually active. ``fail`` when the resolved default
    provider (sandbox.registry.resolve with no environment) cannot run;
    ``warn`` when it can but the network enforcement is off; ``ok``
    otherwise."""
    from sandbox import registry

    providers = registry.available()
    try:
        default_name = registry.resolve(None)
    except Exception as exc:  # noqa: BLE001 - reported as the check's result
        return ("fail", f"No default sandbox provider could be resolved: {exc}", {"providers": providers})

    from environments import egress
    proxy_on = egress.enabled()
    detail = {"providers": providers, "default_provider": default_name, "egress_proxy": proxy_on}

    default_ok = bool(providers.get(default_name, {}).get("available"))
    if not default_ok:
        reason = providers.get(default_name, {}).get("reason") or "unavailable"
        return ("fail", f"The default sandbox provider ({default_name}) cannot run: {reason}", detail)
    if not proxy_on:
        return ("warn", (
            f"The default sandbox provider ({default_name}) is available, but the egress proxy is "
            "off (AGENTS_HUB_EGRESS_PROXY=1), so a limited/none network policy is enforced only by "
            "the hub's own tool checks, not by the container network itself."), detail)
    return ("ok", f"The default sandbox provider ({default_name}) is available; the enforced docker "
                  "network policy is active.", detail)


# ── frontend build ───────────────────────────────────────────────────────────

def _newest_mtime(root: Path) -> Tuple[float, Optional[str]]:
    newest, which = 0.0, None
    for p in root.rglob("*"):
        if "node_modules" in p.parts or not p.is_file():
            continue
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > newest:
            newest, which = m, str(p.relative_to(root))
    return newest, which


def check_frontend_build(snap: Dict[str, Any]) -> Result:
    from common.paths import PROJECT_ROOT
    frontend = Path(PROJECT_ROOT) / "dashboard" / "frontend"
    index = frontend / "dist" / "index.html"
    if not index.is_file():
        return ("skip", "No production build (dashboard/frontend/dist); the dev server serves the "
                        "frontend.", {})
    built = index.stat().st_mtime
    newest, which = _newest_mtime(frontend / "src") if (frontend / "src").is_dir() else (0.0, None)
    detail = {"built_at": datetime.fromtimestamp(built, timezone.utc).isoformat(),
              "newest_source": which,
              "newest_source_at": (datetime.fromtimestamp(newest, timezone.utc).isoformat()
                                   if newest else None)}
    if newest > built:
        return ("warn", f"The frontend build is older than its sources ({which} changed after "
                        "it); rebuild it.", detail)
    return ("ok", "The frontend build is newer than every source file.", detail)


# ── system workspace ─────────────────────────────────────────────────────────

def check_system_workspace(snap: Dict[str, Any]) -> Result:
    from common import system_workspace as sw
    if not sw.enabled():
        return ("skip", "The system workspace is turned off (SYSTEM_WORKSPACE=false).", {})
    from workspace import get_workspace_folder, get_workspace_metadata
    exists = bool(get_workspace_folder(sw.WORKSPACE)) and bool(get_workspace_metadata(sw.WORKSPACE))
    clone = sw.probe_clone()
    detail = {"workspace": exists, "repo_dir": clone.get("repo_dir"), "clone": clone.get("exists"),
              "head": clone.get("head"), "branch": clone.get("branch"),
              "synced_at": clone.get("synced_at")}
    if not exists:
        return ("warn", "The system workspace has not been created; it is seeded at startup.",
                detail)
    if clone.get("error"):
        return ("warn", f"The repository copy has a problem: {clone['error']}", detail)
    if not clone.get("exists"):
        return ("warn", "The system workspace exists but its repository copy has not been made "
                        "yet; sync it.", detail)
    return ("ok", f"The repository copy is at {clone.get('head')} on {clone.get('branch')}.",
            detail)


# ── the list and the runner ──────────────────────────────────────────────────

CHECKS: List[Tuple[str, str, Callable[[Dict[str, Any]], Result]]] = [
    ("migrations", "Database migrations", check_migrations),
    ("provider", "Default model provider", check_provider),
    ("cors", "Cross-origin access", check_cors),
    ("stale_runs", "Stale runs and leases", check_stale_runs),
    ("run_queue", "Launch queue", check_run_queue),
    ("outbox", "Outbound notifications", check_outbox),
    ("disk", "Free disk", check_disk),
    ("browser", "Browser service", check_browser),
    ("models_runtime", "Model runtime", check_models_runtime),
    ("docker", "Docker", check_docker),
    ("sandbox", "Sandbox providers", check_sandbox),
    ("frontend_build", "Frontend build", check_frontend_build),
    ("system_workspace", "System workspace", check_system_workspace),
]


def _run_one(check_id: str, title: str, fn: Callable[[Dict[str, Any]], Result],
             snap: Dict[str, Any]) -> Dict[str, Any]:
    try:
        status, summary, detail = fn(snap)
    except Exception as exc:  # noqa: BLE001 - a check that errors is a failed check, never a crash
        log.debug("doctor check %s failed", check_id, exc_info=True)
        status, summary, detail = "fail", f"The check itself failed: {type(exc).__name__}: {exc}", {}
    if status not in ("ok", "warn", "fail", "skip"):
        status = "fail"
    return {"id": check_id, "title": title, "status": status, "summary": summary,
            "detail": detail or {}, "doc": DOC, "anchor": anchor_for(check_id)}


def overall_status(checks: List[Dict[str, Any]]) -> str:
    """The worst status among the checks; ``skip`` does not count."""
    worst = "ok"
    for c in checks:
        s = c.get("status")
        if s in _RANK and _RANK[s] > _RANK[worst]:
            worst = s
    return worst


def run_doctor(app_state: Any = None) -> Dict[str, Any]:
    """Every check, and the overall status. Never raises."""
    try:
        from common.health import snapshot
        snap = snapshot(app_state)
    except Exception as exc:  # noqa: BLE001 - checks still run over an empty snapshot
        log.debug("health snapshot failed", exc_info=True)
        snap = {"error": str(exc)}
    checks = [_run_one(cid, title, fn, snap) for cid, title, fn in CHECKS]
    return {"status": overall_status(checks),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "checks": checks}


__all__ = ["run_doctor", "overall_status", "anchor_for", "CHECKS", "probe_provider",
           "stale_leaf_runs"]
