"""
Rename agent ids in stored configuration, once, when an agent is renamed.

``agents.registry.LEGACY_AGENT_IDS`` maps an old agent id to its new one
(``researcher_agent`` became ``researcher``). The alias there keeps every
lookup working; this module rewrites what an install has stored, so the old id
stops spreading: the registry record itself, other agents' ``delegates`` and
``handoffs``, workspace metadata (``allowed_agents``, ``default_chat_agent``,
per agent overrides and memory switches keyed by agent id), flows, scheduled
jobs, watchers, channels, teams, loops, scenarios, eval sets, live instances,
services, widgets and their open threads, open experiments, consent settings
and the agent's version history.

Historic records (runs, sessions, chats, task history, logs, results, memory
content) are left as they were written: the alias resolves them.

Run from :func:`common.bootstrap.ensure_initial_state` on every start, after a
fresh registry is seeded and before missing system agents are added (so an old
record is renamed rather than shadowed by a fresh seed copy). Idempotent: a
second run finds nothing to change and writes nothing. Before the first change
it writes a backup of every document and row it is about to touch, next to
where ``agents.json`` used to live, the way the system agent sync does.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

log = logging.getLogger(__name__)

#: Document collections (common/docstore.py) that hold configuration. Anything
#: else (logs, events, fires, notifications, memory content, chat threads) is
#: history and keeps the id it was written with.
CONFIG_STORES: Tuple[str, ...] = (
    "agents", "workspaces", "flows", "flow_entities", "plans", "watchers",
    "guardrails", "telegram", "environments", "project_deployments", "connections",
    "git_connectors", "tool_policy_decisions", "procedures", "user_context",
)
#: Collections named by prefix: one per chat channel (connectors/channels/store.py).
CONFIG_STORE_PREFIXES: Tuple[str, ...] = ("channel_",)


class _Table:
    """A relational table that holds configuration naming an agent.

    ``ids`` are columns holding a bare agent id, ``docs`` JSON columns walked
    for one. ``where`` limits the rewrite to live rows (an archived instance or
    an ended experiment is history). ``unique`` marks id columns where the new
    id must not already have rows (a primary key, version numbers per agent):
    those are left alone when it does, and the alias covers them.
    """

    def __init__(self, name: str, key: str, ids: Tuple[str, ...] = (), docs: Tuple[str, ...] = (),
                 where: str = "", unique: bool = False) -> None:
        self.name, self.key, self.ids, self.docs = name, key, ids, docs
        self.where, self.unique = where, unique


TABLES: Tuple[_Table, ...] = (
    _Table("teams", "team_id", ids=("leader_agent_id",), docs=("members", "config")),
    _Table("loops", "loop_id", docs=("config",)),
    _Table("scenarios", "scenario_id", docs=("roles", "config")),
    _Table("eval_sets", "eval_set_id", ids=("agent_id", "target_id"), docs=("graders",)),
    _Table("widgets", "widget_id", ids=("agent_id",)),
    _Table("widget_threads", "thread_id", ids=("agent_id",)),
    _Table("services", "service_id", ids=("agent_id",), docs=("extra",)),
    _Table("instances", "instance_id", ids=("agent_id",), docs=("extra",),
           where="archived_at IS NULL"),
    _Table("agent_experiments", "experiment_id", ids=("agent_id",), where="ended_at IS NULL",
           unique=True),
    _Table("consent_settings", "agent_id", ids=("agent_id",), unique=True),
    _Table("consent_requests", "request_id", ids=("agent_id",)),
    _Table("agent_versions", "id", ids=("agent_id",), docs=("spec_json",), unique=True),
)

#: Display names that changed with the id. A record still carrying the old
#: shipped name gets the new one; a name somebody chose is kept.
LEGACY_AGENT_NAMES: Dict[str, Tuple[str, str]] = {
    "researcher_agent": ("Researcher Agent", "Researcher"),
}


def _mapping() -> Dict[str, str]:
    from agents.registry import LEGACY_AGENT_IDS
    return dict(LEGACY_AGENT_IDS)


def rename_in(value: Any, mapping: Mapping[str, str]) -> Any:
    """``value`` with every agent id reference renamed: a string that is an old
    id (or ``agent:<old id>``, the ``/v1`` model id and flow entity form), and a
    dict key that is one. Everything else is returned as is."""
    if isinstance(value, str):
        if value in mapping:
            return mapping[value]
        if value.startswith("agent:") and value[len("agent:"):] in mapping:
            return "agent:" + mapping[value[len("agent:"):]]
        return value
    if isinstance(value, list):
        return [rename_in(v, mapping) for v in value]
    if isinstance(value, dict):
        out: Dict[Any, Any] = {}
        for k, v in value.items():
            nk = mapping.get(k, k) if isinstance(k, str) else k
            if nk != k and nk in value:
                # Both ids are keys already: the current one wins.
                continue
            out[nk] = rename_in(v, mapping)
        return out
    return value


def _like_params(mapping: Mapping[str, str]) -> List[str]:
    return [f"%{old}%" for old in mapping]


# ── reading what would change ──────────────────────────────────────────────


def _store_names(conn: Any) -> List[str]:
    rows = conn.execute("SELECT DISTINCT store FROM documents").fetchall()
    names = [str(r["store"]) for r in rows]
    return [n for n in names
            if n in CONFIG_STORES or any(n.startswith(p) for p in CONFIG_STORE_PREFIXES)]


def _plan_documents(mapping: Mapping[str, str]) -> List[Dict[str, Any]]:
    """Every config document that names an old id, with its rewritten form."""
    from common import db

    plan: List[Dict[str, Any]] = []
    likes = _like_params(mapping)
    clause = " OR ".join(["doc LIKE ?"] * len(likes))
    with db.transaction() as conn:
        for store in _store_names(conn):
            rows = conn.execute(
                f"SELECT key, doc FROM documents WHERE store = ? AND ({clause})",
                (store, *likes)).fetchall()
            existing_keys = None
            for r in rows:
                key = str(r["key"])
                before = db.loads(r["doc"])
                after = rename_in(before, mapping)
                new_key = key
                if store == "agents" and key in mapping:
                    new_key = mapping[key]
                    if isinstance(after, dict):
                        after["id"] = new_key
                        old_name, new_name = LEGACY_AGENT_NAMES.get(key, (None, None))
                        if old_name and after.get("name") == old_name:
                            after["name"] = new_name
                    if existing_keys is None:
                        existing_keys = {str(k["key"]) for k in conn.execute(
                            "SELECT key FROM documents WHERE store = ?", (store,)).fetchall()}
                    if new_key in existing_keys:
                        # The renamed record exists already (a fresh seed copy, or a
                        # half finished earlier run): keep it, unless only the old
                        # one carries the operator's edits.
                        current = db.loads(conn.execute(
                            "SELECT doc FROM documents WHERE store = ? AND key = ?",
                            (store, new_key)).fetchone()["doc"])
                        keep_old = (isinstance(after, dict) and after.get("user_modified")
                                    and not (isinstance(current, dict) and current.get("user_modified")))
                        plan.append({"store": store, "key": key, "new_key": new_key,
                                     "before": before, "after": after if keep_old else None,
                                     "replaces": current if keep_old else None})
                        continue
                if after != before or new_key != key:
                    plan.append({"store": store, "key": key, "new_key": new_key,
                                 "before": before, "after": after})
    return plan


def _plan_table(table: _Table, mapping: Mapping[str, str]) -> List[Dict[str, Any]]:
    from common import db

    cols = [table.key, *[c for c in (*table.ids, *table.docs) if c != table.key]]
    olds = list(mapping)
    conds: List[str] = []
    params: List[Any] = []
    for c in table.ids:
        conds.append(f"{c} IN ({', '.join('?' * len(olds))})")
        params.extend(olds)
    for c in table.docs:
        for like in _like_params(mapping):
            conds.append(f"{c} LIKE ?")
            params.append(like)
    if not conds:
        return []
    sql = f"SELECT {', '.join(cols)} FROM {table.name} WHERE ({' OR '.join(conds)})"
    if table.where:
        sql += f" AND {table.where}"
    with db.transaction() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        taken: Dict[str, bool] = {}
        if table.unique and rows:
            for c in table.ids:
                for new in set(mapping.values()):
                    q = f"SELECT 1 FROM {table.name} WHERE {c} = ?"
                    if table.where:
                        q += f" AND {table.where}"
                    taken[f"{c}:{new}"] = conn.execute(q, (new,)).fetchone() is not None
    plan: List[Dict[str, Any]] = []
    for r in rows:
        before = {c: r[c] for c in cols}
        sets: Dict[str, Any] = {}
        for c in table.ids:
            value = r[c]
            if value in mapping and not taken.get(f"{c}:{mapping[value]}"):
                sets[c] = mapping[value]
        for c in table.docs:
            raw = r[c]
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                continue
            renamed = rename_in(parsed, mapping)
            if renamed != parsed:
                sets[c] = db.dumps(renamed)
        if sets:
            plan.append({"table": table.name, "key_col": table.key, "key": r[table.key],
                         "before": before, "sets": sets})
    return plan


def _plan_tables(mapping: Mapping[str, str]) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = []
    for table in TABLES:
        try:
            plan.extend(_plan_table(table, mapping))
        except Exception:  # noqa: BLE001 - a table this install does not have is nothing to rename
            log.debug("legacy agent ids: skipped table %s", table.name, exc_info=True)
    return plan


# ── writing ────────────────────────────────────────────────────────────────


def _write_backup(docs: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> Optional[str]:
    from common.paths import AGENTS_FILE, ensure_agents_hub_root

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = AGENTS_FILE.parent / f"legacy-agent-ids.{stamp}.pre-rename-backup.json"
    payload = {
        "written_at": stamp,
        "mapping": _mapping(),
        "documents": [{"store": d["store"], "key": d["key"], "doc": d["before"]} for d in docs],
        "rows": [{"table": r["table"], "row": r["before"]} for r in rows],
    }
    ensure_agents_hub_root()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(path)


def _apply(docs: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> None:
    from common import db

    now = datetime.now(timezone.utc).isoformat()
    with db.transaction() as conn:
        for d in docs:
            store, key, new_key, after = d["store"], d["key"], d["new_key"], d["after"]
            if new_key != key and "replaces" in d:
                # The new id is taken: drop the old record, or let it replace the
                # fresh copy when it is the one carrying the operator's edits.
                conn.execute("DELETE FROM documents WHERE store = ? AND key = ?", (store, key))
                if after is not None:
                    conn.execute(
                        "UPDATE documents SET doc = ?, updated_at = ? WHERE store = ? AND key = ?",
                        (db.dumps(after), now, store, new_key))
                continue
            # Same row, same place in the collection's order: the key changes in place.
            conn.execute(
                "UPDATE documents SET key = ?, doc = ?, updated_at = ? WHERE store = ? AND key = ?",
                (new_key, db.dumps(after), now, store, key))
        for r in rows:
            cols = list(r["sets"])
            conn.execute(
                f"UPDATE {r['table']} SET {', '.join(f'{c} = ?' for c in cols)} "
                f"WHERE {r['key_col']} = ?",
                (*[r["sets"][c] for c in cols], r["key"]))


def _copy_definition_folders(mapping: Mapping[str, str]) -> List[str]:
    """An install whose definitions folder still has only the old id (a volume
    or a local edit that kept it) gets a copy under the new id. Never removes
    anything and never overwrites a folder that exists."""
    from agents.prompt_assembly import DEFINITIONS_DIR

    copied: List[str] = []
    for old, new in mapping.items():
        src, dst = DEFINITIONS_DIR / old, DEFINITIONS_DIR / new
        if src.is_dir() and not dst.exists():
            try:
                shutil.copytree(src, dst)
                copied.append(new)
            except OSError:
                log.warning("legacy agent ids: could not copy %s to %s", src, dst, exc_info=True)
    return copied


def migrate_legacy_agent_ids(mapping: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Rename every stored configuration reference to an old agent id.

    Returns what changed: ``documents`` (``store/key`` labels), ``rows``
    (``table/key``), ``definitions`` (folders copied) and ``backup`` (the file
    written first, or None when nothing changed). Never raises: a failure is
    logged and leaves the stored state as it was (the writes are one
    transaction), and the alias keeps the old id working meanwhile.
    """
    from common import snapshot

    result: Dict[str, Any] = {"documents": [], "rows": [], "definitions": [], "backup": None}
    if snapshot.in_snapshot_mode():
        return result
    mapping = dict(mapping if mapping is not None else _mapping())
    if not mapping:
        return result
    try:
        result["definitions"] = _copy_definition_folders(mapping)
        docs = _plan_documents(mapping)
        rows = _plan_tables(mapping)
        if not docs and not rows:
            return result
        result["backup"] = _write_backup(docs, rows)
        _apply(docs, rows)
    except Exception:  # noqa: BLE001 - startup must not raise; the alias keeps old ids resolving
        log.exception("legacy agent ids: could not rename stored references")
        return result
    try:
        from agents import registry
        registry._REGISTRY_CACHE["mtime"] = None
    except Exception:  # noqa: BLE001 - the cache also notices the store signature change
        log.debug("legacy agent ids: could not drop the registry cache", exc_info=True)
    result["documents"] = [f"{d['store']}/{d['key']}" for d in docs]
    result["rows"] = [f"{r['table']}/{r['key']}" for r in rows]
    log.warning(
        "legacy agent ids: renamed %s in %d document(s) and %d row(s); backup at %s",
        ", ".join(f"{o} -> {n}" for o, n in mapping.items()),
        len(docs), len(rows), result["backup"],
    )
    return result
