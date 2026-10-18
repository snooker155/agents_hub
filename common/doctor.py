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
PROVIDER_TIMEOUT_SECONDS = 5.0
MODELS_TIMEOUT_SECONDS = 3.0

Result = Tuple[str, str, Dict[str, Any]]


class Msg(str):
    """A summary sentence that also carries a message key and the values in it,
    so the dashboard can say it in the reader's language
    (``health.doctor.summaries.<key>`` in the frontend locales). Every other
    caller, ``ah doctor`` and the Service Agent among them, reads it as the
    plain English string it is. ``parts`` holds the pieces of a sentence
    joined from several findings; each piece is a Msg of its own."""

    key: Optional[str]
    params: Dict[str, Any]
    parts: List["Msg"]

    def __new__(cls, key: Optional[str], text: str, **params: Any) -> "Msg":
        obj = super().__new__(cls, text)
        obj.key = key
        obj.params = params
        obj.parts = []
        return obj

    @classmethod
    def joined(cls, parts: List["Msg"]) -> "Msg":
        """``a; b.`` with the first letter raised, the shape the warn sentences
        built from several problems have always had."""
        text = "; ".join(parts)
        obj = cls(None, text[:1].upper() + text[1:] + ".")
        obj.parts = list(parts)
        return obj

    def i18n(self) -> Dict[str, Any]:
        if self.parts:
            return {"parts": [p.i18n() for p in self.parts]}
        return {"key": self.key, "params": self.params}


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
        return ("fail", Msg("migrations.unknown",
                            f"The database has migration(s) {unknown} this build does not know; "
                            "it was written by a newer version.", versions=unknown), detail)
    if pending:
        return ("fail", Msg("migrations.pending", f"{len(pending)} migration(s) are pending: {pending}.",
                            count=len(pending), versions=pending), detail)
    return ("ok", Msg("migrations.ok", f"Schema is at version {top}, nothing pending.", version=top),
            detail)


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
        return ("skip", Msg("provider.none", "No default provider is configured."), {})
    probe = probe_provider(provider)
    detail = {"provider": provider, **{k: v for k, v in probe.items() if k != "skip"}}
    if "skip" in probe:
        return ("skip", Msg("provider.notProbed", f"Not probed: {probe['skip']}.",
                            reason=probe["skip"]), detail)
    if probe.get("ok"):
        return ("ok", Msg("provider.ok", f"{provider} answered in {probe.get('latency_ms')} ms.",
                          provider=provider, ms=probe.get("latency_ms")), detail)
    return ("fail", Msg("provider.fail", f"{provider} did not answer: {probe.get('error')}.",
                        provider=provider, error=probe.get("error")), detail)


# ── cors ─────────────────────────────────────────────────────────────────────

def check_cors(snap: Dict[str, Any]) -> Result:
    """``ALLOW_ORIGINS=*`` lets any web page call the API from a browser. The
    API asks for no credential in this edition, so any site the operator
    visits could then drive the hub: always a warning."""
    import os
    value = os.getenv("ALLOW_ORIGINS", "").strip()
    detail = {"allow_origins": value or "(local dev defaults)"}
    if value != "*":
        return ("ok", Msg("cors.list", "CORS allows a list of origins.") if value
                else Msg("cors.devOnly", "CORS allows the local dev origins only."), detail)
    return ("warn", Msg("cors.anyOpen", "ALLOW_ORIGINS is *: the API has no login, so any site open "
                                        "in your browser can call it; list the dashboard origins "
                                        "instead."), detail)


# ── security posture ─────────────────────────────────────────────────────────

def check_security(snap: Dict[str, Any]) -> Result:
    """The settings SECURITY.md's hardening list names, read together: the
    capability guard, the secret key against the secrets already stored, and
    the execution mode the runs use."""
    from agents.capability_guard import guard_mode
    from common import db, identity, secrets
    from common.config import agent_execution_mode, settings
    mode = identity.current_mode()
    execution = agent_execution_mode()
    guard = guard_mode()
    backend = str(getattr(settings, "secret_backend", "local") or "local").strip().lower()
    conn = db.get_conn()
    stored = 0
    if backend != "vault":
        stored = int(conn.execute("SELECT COUNT(*) FROM secrets").fetchone()[0])
    detail = {"auth_mode": mode, "execution_mode": execution, "capability_guard": guard,
              "secret_backend": backend, "secret_key": secrets.key_configured(),
              "secrets_stored": stored}
    problems = []
    if guard != "block":
        problems.append(Msg("security.guard", f"CAPABILITY_GUARD is {guard}: an agent may hold "
                                              "all three capabilities; set it back to block",
                            guard=guard))
    if backend != "vault" and stored and not secrets.key_configured():
        problems.append(Msg("security.secretKey", f"{stored} secret(s) stored but "
                                                  "AGENTS_HUB_SECRET_KEY is empty, so runs receive "
                                                  "none of them", count=stored))
    if problems:
        return ("warn", Msg.joined(problems), detail)
    return ("ok", Msg("security.ok", f"{mode} mode, {execution} runs, capability guard on block.",
                      mode=mode, execution=execution), detail)


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
    watchdog_alive = (snap.get("services") or {}).get("run_watchdog")
    detail = {"threshold_seconds": threshold, "stale_runs": len(stale), "runs": stale[:10],
              "run_watchdog": watchdog_alive}
    if stale and watchdog_alive is False:
        return ("fail", Msg("staleRuns.noWatchdog", f"{len(stale)} run(s) stopped beating and the "
                                                    "run watchdog is not running to fail them.",
                            count=len(stale)), detail)
    if stale:
        return ("warn", Msg("staleRuns.stale", f"{len(stale)} run(s) have not beaten for over "
                                               f"{int(threshold)} s",
                            count=len(stale), seconds=int(threshold)), detail)
    return ("ok", Msg("staleRuns.ok", "No stale runs."), detail)


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
        return ("fail", Msg("disk.fail", f"Only {gb:.2f} GB free under the state directory.",
                            gb=f"{gb:.2f}"), detail)
    if free < DISK_WARN_BYTES:
        return ("warn", Msg("disk.free", f"{gb:.2f} GB free under the state directory.",
                            gb=f"{gb:.2f}"), detail)
    return ("ok", Msg("disk.free", f"{gb:.1f} GB free under the state directory.", gb=f"{gb:.1f}"),
            detail)


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
        return ("ok", Msg("docker.ok", "Docker is available."), detail)
    if mode == "docker":
        return ("fail", Msg("docker.required", "Agents are set to run in docker, but no docker daemon "
                                               "answers."), detail)
    if fallback != "local":
        return ("warn", Msg("docker.noSandbox", "No docker daemon answers, so run_code cannot start "
                                                "its sandbox."), detail)
    return ("skip", Msg("docker.notNeeded", "Docker is not available, and nothing configured here "
                                            "requires it."), detail)


# ── sandbox providers ────────────────────────────────────────────────────────

def check_sandbox(snap: Dict[str, Any]) -> Result:
    """Every sandbox/registry.py provider's availability. ``fail`` when the
    resolved default provider (sandbox.registry.resolve) cannot run; ``ok``
    otherwise."""
    from sandbox import registry

    providers = registry.available()
    try:
        default_name = registry.resolve()
    except Exception as exc:  # noqa: BLE001 - reported as the check's result
        return ("fail", Msg("sandbox.unresolved", f"No default sandbox provider could be resolved: "
                                                  f"{exc}", error=str(exc)),
                {"providers": providers})

    detail = {"providers": providers, "default_provider": default_name}

    default_ok = bool(providers.get(default_name, {}).get("available"))
    if not default_ok:
        reason = providers.get(default_name, {}).get("reason") or "unavailable"
        return ("fail", Msg("sandbox.cannotRun", f"The default sandbox provider ({default_name}) "
                                                 f"cannot run: {reason}",
                            provider=default_name, reason=reason), detail)
    return ("ok", Msg("sandbox.ok", f"The default sandbox provider ({default_name}) is available.",
                      provider=default_name), detail)


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
        return ("skip", Msg("frontendBuild.none", "No production build (dashboard/frontend/dist); "
                                                  "the dev server serves the frontend."), {})
    built = index.stat().st_mtime
    newest, which = _newest_mtime(frontend / "src") if (frontend / "src").is_dir() else (0.0, None)
    detail = {"built_at": datetime.fromtimestamp(built, timezone.utc).isoformat(),
              "newest_source": which,
              "newest_source_at": (datetime.fromtimestamp(newest, timezone.utc).isoformat()
                                   if newest else None)}
    if newest > built:
        return ("warn", Msg("frontendBuild.stale", f"The frontend build is older than its sources "
                                                   f"({which} changed after it); rebuild it.",
                            file=which), detail)
    return ("ok", Msg("frontendBuild.ok", "The frontend build is newer than every source file."),
            detail)


# ── system workspace ─────────────────────────────────────────────────────────

def check_system_workspace(snap: Dict[str, Any]) -> Result:
    from common import system_workspace as sw
    if not sw.enabled():
        return ("skip", Msg("systemWorkspace.off", "The system workspace is turned off "
                                                   "(SYSTEM_WORKSPACE=false)."), {})
    from workspace import get_workspace_folder, get_workspace_metadata
    exists = bool(get_workspace_folder(sw.WORKSPACE)) and bool(get_workspace_metadata(sw.WORKSPACE))
    clone = sw.probe_clone()
    detail = {"workspace": exists, "repo_dir": clone.get("repo_dir"), "clone": clone.get("exists"),
              "head": clone.get("head"), "branch": clone.get("branch"),
              "synced_at": clone.get("synced_at")}
    if not exists:
        return ("warn", Msg("systemWorkspace.missing", "The system workspace has not been created; "
                                                       "it is seeded at startup."), detail)
    if clone.get("error"):
        return ("warn", Msg("systemWorkspace.cloneError", f"The repository copy has a problem: "
                                                          f"{clone['error']}", error=clone["error"]),
                detail)
    if not clone.get("exists"):
        return ("warn", Msg("systemWorkspace.notCloned", "The system workspace exists but its "
                                                         "repository copy has not been made yet; "
                                                         "sync it."), detail)
    return ("ok", Msg("systemWorkspace.ok", f"The repository copy is at {clone.get('head')} on "
                                            f"{clone.get('branch')}.",
                      head=clone.get("head"), branch=clone.get("branch")), detail)


# ── skills ───────────────────────────────────────────────────────────────────

def check_skills(snap: Dict[str, Any]) -> Result:
    """Skills from outside the team that the review flagged
    (memory/skill_review.py): a high flag on a skill attached to an agent is
    the thing to look at today, a flagged catalog entry before anyone
    attaches it. Also counts skills with scripts and published skills whose
    license is not open (the API refuses those, so a count here means an
    older record). Skip when there are no skills."""
    from memory.procedural import all_procedures
    from memory.skill_review import is_publishable

    procedures = all_procedures()
    if not procedures:
        return ("skip", Msg("skills.none", "No skills in any workspace."), {})
    attached_high: List[Dict[str, Any]] = []
    catalog_high: List[Dict[str, Any]] = []
    medium = 0
    with_scripts = 0
    unreviewed_repo = 0
    unpublishable_shared = 0
    for p in procedures:
        safety = p.safety or {}
        sev = safety.get("severity", "none")
        row = {"id": str(p.id), "name": p.name, "workspace": p.workspace, "agent_id": p.agent_id or ""}
        if sev == "high":
            (attached_high if p.agent_id else catalog_high).append(row)
        elif sev == "medium":
            medium += 1
        if safety.get("scripts"):
            with_scripts += 1
        if p.source == "repo" and p.safety is None:
            unreviewed_repo += 1
        if p.shared and not is_publishable(p):
            unpublishable_shared += 1
    detail = {
        "skills": len(procedures), "attached_high": attached_high[:20],
        "catalog_high": catalog_high[:20], "medium": medium, "with_scripts": with_scripts,
        "unreviewed_repo": unreviewed_repo, "unpublishable_shared": unpublishable_shared,
        "skill_scanner": shutil.which("skill-scanner") is not None,
    }
    if attached_high:
        names = ", ".join(r["name"] for r in attached_high[:3])
        return ("warn", Msg("skills.attachedHigh", f"{len(attached_high)} skill(s) attached to "
                                                   "agents carry a high safety flag "
                                                   f"({names}); review them on the Skills page.",
                            count=len(attached_high), names=names), detail)
    if unpublishable_shared:
        return ("warn", Msg("skills.unpublishable", f"{unpublishable_shared} published skill(s) carry "
                                                    "a license that is not open; withdraw them from "
                                                    "the global catalog.",
                            count=unpublishable_shared), detail)
    if catalog_high:
        return ("warn", Msg("skills.catalogHigh", f"{len(catalog_high)} catalog skill(s) carry a high "
                                                  "safety flag; review them before attaching them "
                                                  "to an agent.", count=len(catalog_high)), detail)
    if unreviewed_repo:
        return ("warn", Msg("skills.unreviewed", f"{unreviewed_repo} repository skill(s) have no "
                                                 "review yet; run Sync from repositories on their "
                                                 "Skills page.", count=unreviewed_repo), detail)
    if with_scripts:
        return ("ok", Msg("skills.okScripts", f"{len(procedures)} skills, none with a high flag, "
                                              f"{with_scripts} with scripts.",
                          count=len(procedures), scripts=with_scripts), detail)
    return ("ok", Msg("skills.ok", f"{len(procedures)} skills, none with a high flag.",
                      count=len(procedures)), detail)


# ── the list and the runner ──────────────────────────────────────────────────

CHECKS: List[Tuple[str, str, Callable[[Dict[str, Any]], Result]]] = [
    ("migrations", "Database migrations", check_migrations),
    ("provider", "Default model provider", check_provider),
    ("cors", "Cross-origin access", check_cors),
    ("security", "Security settings", check_security),
    ("stale_runs", "Stale runs", check_stale_runs),
    ("disk", "Free disk", check_disk),
    ("docker", "Docker", check_docker),
    ("sandbox", "Sandbox providers", check_sandbox),
    ("frontend_build", "Frontend build", check_frontend_build),
    ("system_workspace", "System workspace", check_system_workspace),
    ("skills", "Skill safety review", check_skills),
]


def _run_one(check_id: str, title: str, fn: Callable[[Dict[str, Any]], Result],
             snap: Dict[str, Any]) -> Dict[str, Any]:
    try:
        status, summary, detail = fn(snap)
    except Exception as exc:  # noqa: BLE001 - a check that errors is a failed check, never a crash
        log.debug("doctor check %s failed", check_id, exc_info=True)
        error = f"{type(exc).__name__}: {exc}"
        status, summary, detail = "fail", Msg("checkFailed", f"The check itself failed: {error}",
                                              error=error), {}
    if status not in ("ok", "warn", "fail", "skip"):
        status = "fail"
    out = {"id": check_id, "title": title, "status": status, "summary": str(summary),
           "detail": detail or {}, "doc": DOC, "anchor": anchor_for(check_id)}
    if isinstance(summary, Msg):
        # The dashboard translates by this; the English summary stays the fallback.
        out["summary_i18n"] = summary.i18n()
    return out


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


__all__ = ["Msg", "run_doctor", "overall_status", "anchor_for", "CHECKS", "probe_provider",
           "stale_leaf_runs"]
