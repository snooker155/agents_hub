"""
The support bundle: one zip an operator attaches to a support request instead
of pasting logs by hand.

Six files plus a log tail, each JSON except the logs:

- ``version.json`` — the release version and commit (``common.version.info``),
  the Python and OS versions, this process's hub role, auth mode and database
  dialect.
- ``doctor.json`` — every self check, judged (``common.doctor.run_doctor``).
- ``health.json`` — the same state snapshot the Health page reads
  (``common.health.snapshot``).
- ``migrations.json`` — the schema ledger: applied, latest, pending, unknown.
- ``config.json`` — effective settings, every secret-looking field reduced to
  whether it is set (never its value).
- ``errors.json`` — failed runs and entity runs in the window: id, kind,
  agent, workspace, status, error text, timestamps.
- ``logs/<member>.log`` — the tail of each running process's own log
  (``common.members.SERVICE_LOGS_DIR``), when any exist.
- ``slo.json`` — the two SLO objectives (``common.slo.evaluate``).

Every one of those, :func:`scrub` runs over first: a single regex pass that
never trusts a caller to have already redacted a field correctly, because the
whole point of a bundle that leaves the building is that a mistake anywhere
above this line must not turn into a leaked key in someone's inbox.

Used two ways: ``GET /api/support/bundle`` (admin only, the running service's
own state) and ``ah support-bundle`` (direct mode: the same call in process;
against a remote backend, a plain HTTP GET of that route).
"""
from __future__ import annotations

import io
import json
import logging
import platform
import re
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

log = logging.getLogger(__name__)

DEFAULT_SINCE_SECONDS = 24 * 3600
DEFAULT_ERROR_LIMIT = 200
#: Lines kept from the end of each service log file.
LOG_TAIL_LINES = 500

_MASK = "***"


# ── the scrubber ─────────────────────────────────────────────────────────────
# Every pattern below is deliberately permissive (better a false positive that
# masks something harmless than a false negative that leaks something real).
# Order matters only where one pattern is a subset of another's match; there
# is no such overlap here, so patterns are applied independently.

_SECRET_PATTERNS = [
    # OpenAI-style and most "sk-..." vendor keys (OpenAI, Stripe test-mode, …).
    (re.compile(r"sk-[A-Za-z0-9_-]{10,}"), "sk-***"),
    # This hub's own API keys (common/api_keys.py KEY_PREFIX).
    (re.compile(r"ahk_[A-Za-z0-9_-]{10,}"), "ahk_***"),
    # GitHub personal access / OAuth / app / refresh tokens.
    (re.compile(r"gh[oprsu]_[A-Za-z0-9]{16,}"), "***"),
    # A bearer token in an Authorization header or a logged request.
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-_.=]{8,}"), "Bearer ***"),
    # AWS access key ids (the secret half is 40 opaque chars with no fixed
    # prefix, so it is left to the credentialed-URL and generic patterns).
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA***"),
    # Google API keys.
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), "AIza***"),
    # Slack tokens.
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "xox-***"),
    # GitLab personal, deploy and runner tokens (a remote URL carries them).
    (re.compile(r"gl(?:pat|dt|rt|cbt|ptt|oas)-[A-Za-z0-9_.-]{16,}"), "gl-***"),
    # Hugging Face tokens (HF_TOKEN for gated models).
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}"), "hf_***"),
    # A JSON web token: a signed session, a visitor token, an OIDC id token.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "jwt-***"),
    # key=value and key: value pairs a log line or an error message prints.
    (re.compile(r"(?i)\b([A-Za-z0-9_]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)"
                r"[A-Za-z0-9_]*)(\s*[=:]\s*)(?![\"'{\[]|\*\*\*|[\d.]+\b)([^\s,;&\"']{4,})"), r"\1\2***"),
]

# scheme://user[:password]@host — postgres://, mysql://, redis://, https:// (a
# git remote with an embedded PAT is exactly this shape: user is the token
# itself, or "oauth2", with the real secret after the colon or in its place).
_CREDENTIALED_URL = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^\s/:@]+)(?::(?P<password>[^\s/@]*))?@")


def scrub(text: str) -> str:
    """Redact every secret-looking substring of ``text``. Idempotent and
    total: run on plain text or on a JSON dump, applied to every file before
    it enters the zip."""
    if not text:
        return text
    out = text
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    out = _CREDENTIALED_URL.sub(lambda m: f"{m.group('scheme')}{_MASK}@", out)
    return out


def _scrub_json(payload: Any) -> bytes:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str, sort_keys=True)
    return scrub(text).encode("utf-8")


# ── version.json ─────────────────────────────────────────────────────────────

def _version_info() -> Dict[str, Any]:
    from common import version as version_mod
    from common.config import hub_role
    from common import db, identity

    info = dict(version_mod.info())
    info["python"] = platform.python_version()
    info["os"] = f"{platform.system()} {platform.release()}"
    try:
        info["hub_role"] = hub_role()
    except Exception:  # noqa: BLE001 - a bundle section failing must not break the others
        info["hub_role"] = "unknown"
    try:
        info["auth_mode"] = identity.current_mode()
    except Exception:  # noqa: BLE001 - see above
        info["auth_mode"] = "unknown"
    try:
        info["database_dialect"] = db.dialect()
    except Exception:  # noqa: BLE001 - see above
        info["database_dialect"] = "unknown"
    return info


# ── migrations.json ──────────────────────────────────────────────────────────

def _migrations_info() -> Dict[str, Any]:
    from common import db, migrations
    try:
        conn = db.get_conn()
        dialect = db.dialect()
        applied = migrations.applied_versions(conn, dialect)
        available = [mg.version for mg in migrations.select_for(dialect)]
        top = max(applied) if applied else 0
        return {
            "dialect": dialect,
            "applied_count": len(applied),
            "latest_available": max(available or [0]),
            "pending": [v for v in available if v > top],
            "unknown": [v for v in applied if v not in set(available)],
        }
    except Exception as exc:  # noqa: BLE001 - a bundle section failing must not break the others
        log.debug("support_bundle: migrations section failed", exc_info=True)
        return {"error": str(exc)}


# ── config.json ──────────────────────────────────────────────────────────────

#: A field name that ends with one of these (as a whole underscore-separated
#: segment) holds a credential, never a value worth keeping. Deliberately a
#: suffix match, not "contains": ``max_tokens`` and ``rate_limit_tokens_per_day``
#: both contain "token" but are counts, not secrets, and ``secret_backend`` names
#: which backend holds secrets rather than holding one itself.
_SECRET_FIELD_SUFFIXES = ("key", "secret", "token", "password", "credential")


def _is_secret_field(name: str) -> bool:
    segments = name.split("_")
    return bool(segments) and segments[-1] in _SECRET_FIELD_SUFFIXES


def _env_name(field_name: str, field_info: Any) -> str:
    alias = getattr(field_info, "validation_alias", None)
    choices = getattr(alias, "choices", None)
    if choices:
        for choice in choices:
            if isinstance(choice, str) and choice.upper() == choice and "_" in choice:
                return choice
        first = choices[0]
        if isinstance(first, str):
            return first
    if isinstance(alias, str):
        return alias
    return field_name.upper()


def _config_info() -> Dict[str, Any]:
    """Effective settings, secret fields reduced to "is it set" plus the env
    var name a fix would touch, never the value. Non-secret string fields
    still pass through the same URL-credential pattern the final scrub does,
    since a field like a database URL is not named "*_key" but can carry one."""
    try:
        from common.config import settings, Settings
    except Exception as exc:  # noqa: BLE001 - a bundle section failing must not break the others
        log.debug("support_bundle: config section failed", exc_info=True)
        return {"error": str(exc)}
    out: Dict[str, Any] = {}
    try:
        dumped = settings.model_dump()
        fields = Settings.model_fields
    except Exception as exc:  # noqa: BLE001 - see above
        log.debug("support_bundle: config dump failed", exc_info=True)
        return {"error": str(exc)}
    for name, value in dumped.items():
        if _is_secret_field(name):
            info = fields.get(name)
            out[name] = {
                "set": bool(value),
                "env": _env_name(name, info) if info is not None else name.upper(),
            }
        elif isinstance(value, str):
            out[name] = scrub(value)
        else:
            out[name] = value
    return out


# ── errors.json ──────────────────────────────────────────────────────────────

def _recent_errors(since: datetime, limit: int = DEFAULT_ERROR_LIMIT) -> List[Dict[str, Any]]:
    from common import db
    from common.run_status import normalize

    since_iso = since.isoformat()
    conn = db.get_conn()
    out: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT run_id, agent_id, workspace, status, error, created_at, started_at, "
            "finished_at FROM runs WHERE COALESCE(finished_at, started_at, created_at) >= ? "
            "ORDER BY COALESCE(finished_at, started_at, created_at) DESC LIMIT ?",
            (since_iso, limit),
        ).fetchall()
        for row in rows:
            rec = dict(row)
            if normalize(rec.get("status")) != "failed":
                continue
            out.append({
                "kind": "agent", "run_id": rec.get("run_id"), "agent": rec.get("agent_id"),
                "workspace": rec.get("workspace"), "status": rec.get("status"),
                "error": rec.get("error"), "created_at": rec.get("created_at"),
                "started_at": rec.get("started_at"), "finished_at": rec.get("finished_at"),
            })
    except Exception:  # noqa: BLE001 - a bundle section failing must not break the others
        log.debug("support_bundle: leaf run errors failed", exc_info=True)
    try:
        rows = conn.execute(
            "SELECT run_id, kind, entity_id, workspace, status, error, created_at, started_at, "
            "finished_at FROM entity_runs "
            "WHERE COALESCE(finished_at, started_at, created_at) >= ? "
            "ORDER BY COALESCE(finished_at, started_at, created_at) DESC LIMIT ?",
            (since_iso, limit),
        ).fetchall()
        for row in rows:
            rec = dict(row)
            if normalize(rec.get("status")) != "failed":
                continue
            out.append({
                "kind": rec.get("kind"), "run_id": rec.get("run_id"), "agent": rec.get("entity_id"),
                "workspace": rec.get("workspace"), "status": rec.get("status"),
                "error": rec.get("error"), "created_at": rec.get("created_at"),
                "started_at": rec.get("started_at"), "finished_at": rec.get("finished_at"),
            })
    except Exception:  # noqa: BLE001 - see above
        log.debug("support_bundle: entity run errors failed", exc_info=True)
    out.sort(key=lambda r: r.get("finished_at") or r.get("started_at") or r.get("created_at") or "",
              reverse=True)
    return out[:limit]


# ── doctor.json, health.json, slo.json ───────────────────────────────────────

def _doctor_info() -> Dict[str, Any]:
    from common.doctor import run_doctor
    return run_doctor()


def _health_info() -> Dict[str, Any]:
    from common.health import snapshot
    return snapshot()


def _slo_info() -> Dict[str, Any]:
    from common.slo import evaluate
    return evaluate()


# ── logs/ ────────────────────────────────────────────────────────────────────

def _log_tails() -> Dict[str, str]:
    try:
        from common.members import SERVICE_LOGS_DIR
    except Exception:  # noqa: BLE001 - a bundle section failing must not break the others
        return {}
    out: Dict[str, str] = {}
    try:
        if not SERVICE_LOGS_DIR.exists():
            return {}
        for path in sorted(SERVICE_LOGS_DIR.glob("*.log")):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
            except OSError:
                continue
            out[path.name] = "".join(lines[-LOG_TAIL_LINES:])
    except Exception:  # noqa: BLE001 - see above
        log.debug("support_bundle: log tail collection failed", exc_info=True)
    return out


# ── build ────────────────────────────────────────────────────────────────────

def build(*, since_seconds: float = DEFAULT_SINCE_SECONDS,
          error_limit: int = DEFAULT_ERROR_LIMIT) -> bytes:
    """The bundle as zip bytes. Never raises: a section that fails to gather
    is written as ``{"error": "..."}`` rather than aborting the whole bundle,
    the same "still useful with one probe down" rule ``common.doctor`` holds
    itself to."""
    since = datetime.now(timezone.utc) - timedelta(seconds=max(0.0, since_seconds))

    def _safe(builder, *args, **kwargs) -> Any:
        try:
            return builder(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - one section failing must not lose the rest
            log.debug("support_bundle: %s failed", getattr(builder, "__name__", "?"), exc_info=True)
            return {"error": str(exc)}

    version = _safe(_version_info)
    doctor = _safe(_doctor_info)
    health = _safe(_health_info)
    migrations = _safe(_migrations_info)
    config = _safe(_config_info)
    errors = _safe(_recent_errors, since, error_limit)
    slo = _safe(_slo_info)
    logs = _safe(_log_tails)
    if not isinstance(logs, dict):
        logs = {}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("version.json", _scrub_json(version))
        zf.writestr("doctor.json", _scrub_json(doctor))
        zf.writestr("health.json", _scrub_json(health))
        zf.writestr("migrations.json", _scrub_json(migrations))
        zf.writestr("config.json", _scrub_json(config))
        zf.writestr("errors.json", _scrub_json(errors))
        zf.writestr("slo.json", _scrub_json(slo))
        for name, text in logs.items():
            zf.writestr(f"logs/{name}", scrub(text).encode("utf-8"))
    return buf.getvalue()


def default_filename() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"agents-hub-support-{stamp}.zip"


__all__ = ["build", "scrub", "default_filename",
           "DEFAULT_SINCE_SECONDS", "DEFAULT_ERROR_LIMIT"]
