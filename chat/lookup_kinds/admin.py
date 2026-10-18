"""Lookup kinds: admin (see chat/lookup_kinds/__init__.py).

Every kind here is service-wide (``admin=True``): the hub's own health,
containers, the web access log, and non-secret settings.
``chat.lookup.lookup`` only reads them from a service thread, so a
function below never re-checks who is asking; it only has to stay cheap
(no network, no subprocess beyond a cached local probe) and to keep content
out of what it returns (log text, secret values, page snippets), the same
rule every other kind follows.

No actions: an administrator reads these, nothing here changes anything.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from chat import lookup

# ── shared: a safe slice of an audit row's free-form ``details`` ────────────

#: ``details`` holds whatever the writer thought worth a byte: a role, a
#: before/after flag, but also notes, error strings and exception text. Only
#: these keys are ever plain codes short enough to be safe without reading
#: every call site that writes one; anything else (reason, note, message,
#: error, text, body, comment, ...) is free text someone else could have
#: written and stays out.
_SAFE_DETAIL_KEYS = frozenset({
    "role", "kind", "disabled", "enabled", "isolated", "replace", "via", "status", "test_ok",
})


def _plain(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return True
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return len(value) <= 100
    return False


def _safe_details(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in _SAFE_DETAIL_KEYS and _plain(v)}


# ── health (common/health.py snapshot + common/doctor.py's cheap checks) ────

#: Checks that reach the network (a provider) or otherwise probe something
#: outside this process. The health
#: kind never runs these on its own: a lookup is read on every turn, and
#: paying a provider round trip for that would make "how's the service"
#: slow and, for the provider check, billed. There is no cached last-result
#: to fall back to (``run_doctor`` always runs the full list itself, in a
#: worker thread, only for ``GET /api/health/doctor``), so this one is
#: simply left out of what this kind lists or runs; asked for by id, it
#: answers "skip" rather than probing live or pretending not to exist.
_NETWORKED_CHECKS = frozenset({"provider"})

#: The id of the one row that is the overall snapshot rather than a check.
_SNAPSHOT_ID = "snapshot"


def _cheap_checks():
    from common import doctor
    return [(cid, title, fn) for cid, title, fn in doctor.CHECKS if cid not in _NETWORKED_CHECKS]


def _run_check(cid: str, title: str, fn, snap: Dict[str, Any]) -> Dict[str, Any]:
    from common import doctor
    try:
        status, summary, _detail = fn(snap)
    except Exception as exc:  # noqa: BLE001 - a check that errors is a failed check
        status, summary = "fail", f"The check itself failed: {type(exc).__name__}: {exc}"
    if status not in ("ok", "warn", "fail", "skip"):
        status = "fail"
    return {"id": cid, "title": title, "status": status, "summary": summary,
            "doc": doctor.DOC, "anchor": doctor.anchor_for(cid)}


def _list_health(ctx: Any, query: str, limit: int) -> List[Dict[str, Any]]:
    from common.health import snapshot
    snap = snapshot()
    rows = []
    if lookup.matches(query, "snapshot", "service health"):
        rows.append(lookup.row(_SNAPSHOT_ID, "Service health snapshot",
                                f"status: {snap.get('status')}", "/health"))
    for cid, title, fn in _cheap_checks():
        check = _run_check(cid, title, fn, snap)
        if not lookup.matches(query, check["id"], check["title"], check["status"]):
            continue
        rows.append(lookup.row(check["id"], check["title"],
                                f"{check['status']}: {lookup.first_line(check['summary'], 140)}",
                                "/health"))
    return rows[:limit]


def _health_card(ctx: Any, entity_id: str) -> Optional[Dict[str, Any]]:
    from common import doctor
    from common.health import snapshot
    if entity_id == _SNAPSHOT_ID:
        return {"title": "Service health snapshot", "fields": snapshot(), "url": "/health"}
    if entity_id in _NETWORKED_CHECKS:
        title = next((t for cid, t, _fn in doctor.CHECKS if cid == entity_id), entity_id)
        return {"title": title, "fields": {
            "id": entity_id, "title": title, "status": "skip",
            "summary": "Not probed from a lookup: this check calls out over the network. "
                      "Open the Health page to run it.",
            "doc": doctor.DOC, "anchor": doctor.anchor_for(entity_id),
        }, "url": "/health"}
    found = next(((cid, title, fn) for cid, title, fn in _cheap_checks() if cid == entity_id), None)
    if found is None:
        return None
    snap = snapshot()
    check = _run_check(*found, snap)
    return {"title": check["title"], "fields": check, "url": "/health"}


# ── container ────────────────────────────────────────────────────────────────

def _containers() -> List[Dict[str, Any]]:
    try:
        from managers import container_manager
        return container_manager.list_containers()
    except Exception:  # noqa: BLE001 - no docker, no daemon: an empty list, never a crash
        return []


def _list_containers(ctx: Any, query: str, limit: int) -> List[Dict[str, Any]]:
    rows = []
    for c in _containers():
        if not lookup.matches(query, c.get("name"), c.get("image"), c.get("agent_id")):
            continue
        subtitle = " · ".join(x for x in [c.get("status") or c.get("state"), c.get("image"),
                                          c.get("agent_id")] if x)
        rows.append(lookup.row(c.get("name"), c.get("name"), subtitle, "/containers"))
    return rows[:limit]


def _container_card(ctx: Any, entity_id: str) -> Optional[Dict[str, Any]]:
    c = next((x for x in _containers() if x.get("name") == entity_id), None)
    if c is None:
        return None
    fields = {k: c.get(k) for k in
              ("name", "image", "status", "state", "created", "agent_id", "host", "remote")
              if c.get(k) not in (None, "")}
    return {"title": c.get("name"), "fields": fields, "url": "/containers"}


# ── web_log ──────────────────────────────────────────────────────────────────

def _host_path(url: Optional[str]) -> tuple:
    """The host, and how long the rest was. A path is as free as a query
    string: a page can link to one that reads like an instruction, an agent
    follows it, and the text would come back here. The page shows it."""
    if not url:
        return "", ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "", ""
    return parsed.hostname or "", len(parsed.path or "")


def _list_web_log(ctx: Any, query: str, limit: int) -> List[Dict[str, Any]]:
    from tools import web_log
    page = web_log.query(limit=limit, search=query or None)
    rows = []
    for r in page.get("items", []):
        host, _ = _host_path(r.get("final_url") or r.get("url"))
        if not host:
            continue
        subtitle = " · ".join(x for x in [
            r.get("kind"), r.get("status"), f"sev {r.get('max_severity') or 'none'}",
            lookup.short_time(r.get("ts")),
        ] if x)
        rows.append(lookup.row(r["id"], host, subtitle, "/web-logs", at=r.get("ts")))
    return rows[:limit]


def _web_log_card(ctx: Any, entity_id: str) -> Optional[Dict[str, Any]]:
    from tools import web_log
    r = web_log.get(entity_id)
    if r is None:
        return None
    host, path_chars = _host_path(r.get("final_url") or r.get("url"))
    fields = {
        "host": host, "path_chars": path_chars, "tool": r.get("kind"), "status": r.get("status"),
        "http_status": r.get("http_status"), "severity": r.get("max_severity") or "none",
        "time": r.get("ts"), "agent_id": r.get("agent_id") or None, "workspace": r.get("workspace") or None,
        "flag_count": len(r.get("flags") or []),
    }
    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    return {"title": host or entity_id, "fields": fields, "url": "/web-logs"}


# ── setting ──────────────────────────────────────────────────────────────────

#: Fields the Settings page keeps only in ``.env``, never on the live
#: ``Settings`` object (RAG, the secondary model names and the task
#: assignment mode): read the same environment variables it reads, which is
#: as cheap as reading the live object and, unlike re-parsing the ``.env``
#: file, sees a value a deployment set directly in its environment too.
_ENV_ONLY = {
    "anthropic_model": "ANTHROPIC_MODEL", "google_model": "GOOGLE_MODEL",
    "openai_base_url": "OPENAI_BASE_URL", "task_assignment_mode": "TASK_ASSIGNMENT_MODE",
}
_ENV_ONLY_SECRETS = {}
_RAG_ENV = {
    "rag_vector_db": ("RAG_VECTOR_DB", "none"), "rag_vector_db_url": ("RAG_VECTOR_DB_URL", ""),
    "rag_vector_db_collection": ("RAG_VECTOR_DB_COLLECTION", "agents_hub_rag"),
    "rag_embedding_provider": ("RAG_EMBEDDING_PROVIDER", "none"),
    "rag_embedding_model": ("RAG_EMBEDDING_MODEL", ""),
    "rag_embedding_base_url": ("RAG_EMBEDDING_BASE_URL", ""),
}
_RAG_ENV_SECRETS = {
    "rag_vector_db_api_key_set": "RAG_VECTOR_DB_API_KEY", "rag_embedding_api_key_set": "RAG_EMBEDDING_API_KEY",
}


def _bare_url(value: Any) -> str:
    """A base URL without credentials or a query: ``https://user:key@host``
    is how some deployments pass a key."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if not parsed.scheme or not parsed.hostname:
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path or ''}"


def _docker_available() -> Optional[bool]:
    try:
        from sandbox.docker import docker_available
        return bool(docker_available())
    except Exception:  # noqa: BLE001 - "could not tell" rather than a crash
        return None


def _settings_categories() -> Dict[str, Dict[str, Any]]:
    from common.config import agent_execution_mode
    from common.config import settings as cfg

    providers: Dict[str, Any] = {
        "default_provider": cfg.default_provider, "model": cfg.model,
        "temperature": cfg.temperature, "max_tokens": cfg.max_tokens,
        "ollama_base_url": _bare_url(cfg.ollama_base_url), "ollama_model": cfg.ollama_model,
        "lmstudio_base_url": _bare_url(cfg.lmstudio_base_url), "lmstudio_model": cfg.lmstudio_model,
        "openai_api_key_set": bool(cfg.openai_api_key),
        "anthropic_api_key_set": bool(cfg.anthropic_api_key),
        "google_api_key_set": bool(cfg.google_api_key),
    }
    for field, env_key in _ENV_ONLY.items():
        if field != "task_assignment_mode":  # that one lives under execution below
            value = os.environ.get(env_key, "")
            providers[field] = _bare_url(value) if field.endswith("_url") else value
    for field, env_key in _ENV_ONLY_SECRETS.items():
        providers[field] = bool(os.environ.get(env_key))

    rag: Dict[str, Any] = {field: os.environ.get(env_key, default)
                           for field, (env_key, default) in _RAG_ENV.items()}
    for field in ("rag_vector_db_url", "rag_embedding_base_url"):
        rag[field] = _bare_url(rag[field])
    for field, env_key in _RAG_ENV_SECRETS.items():
        rag[field] = bool(os.environ.get(env_key))

    web: Dict[str, Any] = {
        "web_fetch_max_chars": cfg.web_fetch_max_chars, "web_fetch_timeout": cfg.web_fetch_timeout,
        "web_fetch_max_redirects": cfg.web_fetch_max_redirects,
        "web_domain_policy_enabled": bool(cfg.web_domain_policy_enabled),
        "web_allow_domains": list(cfg.web_allow_domains or ()),
        "web_deny_domains": list(cfg.web_deny_domains or ()),
    }

    execution: Dict[str, Any] = {
        "agent_execution_mode": agent_execution_mode(),
        "agent_docker_image": cfg.agent_docker_image, "agent_docker_network": cfg.agent_docker_network,
        # Extra docker arguments can carry ``-e KEY=value``: whether any are set.
        "agent_docker_extra_args_set": bool(cfg.agent_docker_extra_args),
        "task_assignment_mode": os.environ.get("TASK_ASSIGNMENT_MODE", "any"),
        "agent_streaming": bool(cfg.agent_streaming), "capability_guard": cfg.capability_guard,
        "capability_override_requires_container": bool(cfg.capability_override_requires_container),
        "code_runner_provider": cfg.code_runner_provider, "code_runner_fallback": cfg.code_runner_fallback,
        "code_runner_docker_available": _docker_available(),
    }
    return {"providers": providers, "rag": rag, "web": web, "execution": execution}


_SETTING_SUMMARY = {
    "providers": lambda f: f"default: {f.get('default_provider')} / {f.get('model')}",
    "rag": lambda f: f.get("rag_vector_db") or "none",
    "web": lambda f: ("domain policy on" if f.get("web_domain_policy_enabled") else "open web"),
    "execution": lambda f: f.get("agent_execution_mode") or "local",
}


def _list_settings(ctx: Any, query: str, limit: int) -> List[Dict[str, Any]]:
    categories = _settings_categories()
    rows = []
    for name, fields in categories.items():
        if not lookup.matches(query, name):
            continue
        rows.append(lookup.row(name, name, _SETTING_SUMMARY[name](fields), "/settings"))
    return rows[:limit]


def _setting_card(ctx: Any, entity_id: str) -> Optional[Dict[str, Any]]:
    categories = _settings_categories()
    fields = categories.get(entity_id)
    if fields is None:
        return None
    return {"title": f"Settings: {entity_id}", "fields": fields, "url": "/settings"}


# ── registration ─────────────────────────────────────────────────────────────

lookup.register(lookup.LookupKind(
    "health", "the service's own health: a snapshot and the doctor's checks (id 'snapshot' or a "
              "check id)", _list_health, _health_card, ("/health",), admin=True))
lookup.register(lookup.LookupKind(
    "container", "docker containers this hub manages", _list_containers, _container_card,
    ("/containers",), admin=True))
lookup.register(lookup.LookupKind(
    "web_log", "what fetch_url read: the host only, never the path or the page content",
    _list_web_log, _web_log_card, ("/web-logs",), admin=True))
lookup.register(lookup.LookupKind(
    "setting", "non-secret hub settings by category (providers, rag, web, execution); a secret "
              "reads as set true/false", _list_settings, _setting_card, ("/settings",), admin=True))
