"""
Remote state: how an agent process with no database reaches the hub's stores.

In docker mode an agent process (a task run, an instance carrier, a flow, loop,
team or scenario run) runs in its own container with no database URL, no
password and no writable state directory. It still calls the same store
functions as a process on the hub; the ones it needs are marked with
:func:`hub_call`, and inside such a container (``AGENTS_HUB_REMOTE_STATE=1``)
a marked function does not run its body: the call goes to the hub as one
``POST /api/run-state/call`` with the process's run token, the hub runs the
function and sends the result back.

Every marked function names its scope: what in its arguments says which
workspace it touches. The hub resolves that workspace and compares it with the
one the run token was minted for, so a container reaches its own workspace and
nothing else, whatever arguments it sends. A function that is not marked is not
callable at all, and :func:`common.db.get_conn` refuses inside a remote process,
so a store access nobody marked fails loudly instead of opening a database.

Scope rules (the ``scope`` argument of :func:`hub_call`; a tuple of them must
all pass):

- ``"workspace"`` or ``"workspace:<arg>"``: the argument is a workspace name.
  Left empty, the hub fills in the token's workspace; ``"workspace?:<arg>"``
  checks it only when given. The argument may sit in ``**kwargs``, and
  ``<arg>.<key>`` names a key of a dict argument or a field of a model
  argument (a record passed whole).
- ``"<kind>:<arg>"``: the argument is the id of a record of ``kind`` (``task``,
  ``run``, ``instance``, ...); a resolver registered with
  :func:`register_resolver` gives that record's workspace. A record that does
  not exist passes: the function finds nothing either.
- ``"admin_workspace:<arg>"``: like ``workspace``, and a run of the default
  workspace may name any workspace (where workspaces are managed from).
- ``"own_instance:<arg>"``: the argument is the instance the token was minted
  for (a carrier's own row), not merely one of its workspace;
  ``"own_message:<arg>"`` a message of that instance's mailbox.
- ``"global"``: the function reads nothing that belongs to a workspace (a
  price list, a model catalog).
- a callable ``(token_row, arguments) -> None`` that raises
  :class:`ScopeError` to refuse, for anything the shapes above cannot say.

Values cross as JSON. Dates, UUIDs, decimals, enums, tuples, sets, bytes, paths,
pydantic models and dataclasses are tagged so they come back as what they were; a model or
dataclass is rebuilt only from a class in this codebase's own packages.
Exceptions come back as the same class when it is a builtin or one of ours,
else as :class:`RemoteCallError`.
"""
from __future__ import annotations

import base64
import contextvars
import dataclasses
import functools
import importlib
import inspect
import logging
import os
import threading
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePath
from typing import Any, Callable, Dict, Iterator, Mapping, Optional, Tuple, Union
from uuid import UUID

log = logging.getLogger(__name__)

#: Set to "1" in the environment of a process that must not open the database.
ENV = "AGENTS_HUB_REMOTE_STATE"

#: The hub route a remote call goes to (under the relay prefix, so a run token
#: reaches it; common/auth.py RELAY_ROUTES).
CALL_PATH = "/api/run-state/call"

#: Seconds a remote call may take before the client gives up.
TIMEOUT_ENV = "AGENTS_HUB_REMOTE_STATE_TIMEOUT"
DEFAULT_TIMEOUT = 120.0

#: Top-level packages whose classes may be rebuilt from a tagged value, and
#: whose exceptions come back as themselves.
_OWN_PACKAGES = frozenset({
    "a2a", "agents", "chat", "common", "connections", "connectors", "declarative", "deployments",
    "environments", "evals", "files", "flow", "guardrails", "hublink", "hubsync", "instances",
    "kits", "loops", "managers", "mcp_client", "memory", "notify", "plans", "playground",
    "proactive", "projects", "providers", "reasoning", "runtime", "sandbox", "services", "tasks",
    "teams", "tools", "views", "watchers", "widgets", "workspace",
})

#: Modules whose marked functions the hub may run for a remote caller. The hub
#: imports them (and only them) to fill the registry; a function in a module
#: missing here is refused even when it is marked.
REMOTE_MODULES: Tuple[str, ...] = (
    "agents.prompt_assembly",
    "agents.registry",
    "agents.roles",
    "common.audit",
    "common.chat_store",
    "common.docstore",
    "common.entity_runs",
    "common.session_service",
    "common.steering",
    "connectors.channels.store",
    "evals.store",
    "connectors.consent.store",
    "files.service",
    "flow.registry",
    "flow.store",
    "guardrails.service",
    "hubsync.copies",
    "instances.carrier",
    "instances.history",
    "instances.inbox",
    "instances.registry",
    "instances.store",
    "loops.store",
    "managers.runs.lifecycle",
    "managers.runs.store",
    "managers.runs.task_finalize",
    "memory.episodic",
    "memory.graph",
    "memory.injection",
    "memory.personal",
    "memory.procedural",
    "memory.store",
    "plans.service",
    "projects.storage",
    "playground.store",
    "providers.catalog",
    "tasks.service",
    "teams.store",
    "tools.langchain_tools",
    "tools.permission_policy",
    "tools.web_log",
    "views.store",
    "workspace.storage",
)

ScopeRule = Union[str, Callable[[Dict[str, Any], Dict[str, Any]], None]]


class RemoteStateError(RuntimeError):
    """A process without a database tried to open one, or a remote call could
    not be made at all (the hub unreachable, an unmarked function)."""


class RemoteCallError(RuntimeError):
    """The hub ran the function and it raised something that cannot be
    rebuilt on this side; the message names the original type."""


class TokenRefused(RemoteStateError):
    """The hub no longer accepts this process's run token: its run was closed
    or its carrier written off (a restart, a stale heartbeat). The process
    has nothing left to do and should stop."""


class ScopeError(PermissionError):
    """The call reaches outside the workspace the run token was minted for."""


# ── where this process stands ────────────────────────────────────────────────

#: Overrides the environment for the current context: the hub side of an
#: in-process call (tests) runs with False, so the function body opens the
#: database there.
_FORCE: contextvars.ContextVar[Optional[bool]] = contextvars.ContextVar(
    "remote_state_force", default=None)


#: ``AGENTS_HUB_REMOTE_STATE=audit``: marked functions still go to the hub,
#: but a direct database access is written to this file (one line per distinct
#: call path) instead of failing, so one run of a process lists every store
#: access that still needs marking (tests/remote_harness.py).
AUDIT_FILE_ENV = "AGENTS_HUB_REMOTE_STATE_AUDIT_FILE"


def _mode() -> str:
    return os.environ.get(ENV, "").strip().lower()


def is_remote() -> bool:
    """Whether this process reaches state through the hub instead of the
    database."""
    forced = _FORCE.get()
    if forced is not None:
        return forced
    return _mode() in ("1", "audit")


@contextmanager
def hub_side() -> Iterator[None]:
    """Run a block as the hub: marked functions run their bodies and the
    database opens, even in a process that is remote otherwise."""
    token = _FORCE.set(False)
    try:
        yield
    finally:
        _FORCE.reset(token)


def refuse_database(what: str = "the database") -> None:
    """Raise when this process must not open ``what`` (common/db.py calls it
    before every connection)."""
    if is_remote():
        if _mode() == "audit" and _FORCE.get() is None:
            _audit(what)
            return
        message = (f"{what} opened in an agent process that has no database ({ENV}=1): "
                   "the store function on this path needs @remote_state.hub_call")
        # Logged as well as raised: many callers swallow every exception (a
        # best-effort write), and a store call that silently does nothing in a
        # container is the failure this has to make visible.
        log.warning("%s", message, stack_info=True)
        raise RemoteStateError(message)


_audit_seen: set = set()


def _audit(what: str) -> None:
    import traceback
    frames = [f for f in traceback.extract_stack()[:-3]
              if "/common/db.py" not in f.filename and "/common/remote_state.py" not in f.filename
              and "/site-packages/" not in f.filename and "<frozen" not in f.filename]
    root = str(Path(__file__).resolve().parents[1]) + "/"
    path = " <- ".join(f"{f.filename.replace(root, '')}:{f.lineno}:{f.name}" for f in reversed(frames[-8:]))
    if path in _audit_seen:
        return
    _audit_seen.add(path)
    target = os.environ.get(AUDIT_FILE_ENV)
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(path + "\n")


# ── registry ─────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Entry:
    key: str
    fn: Callable[..., Any]
    scope: Any
    signature: inspect.Signature
    result_scope: Any = None


_REGISTRY: Dict[str, Entry] = {}
_RESOLVERS: Dict[str, Callable[..., Any]] = {}
_loaded = False
_imported: set = set()
_load_lock = threading.Lock()


def _key(fn: Callable[..., Any]) -> str:
    return f"{fn.__module__}:{fn.__qualname__}"


def hub_call(scope: Union[ScopeRule, Tuple[str, ...]], *, cache_seconds: float = 0.0,
             result_scope: Any = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a store function as callable from a remote agent process.

    On the hub (and in any process with a database) the function runs as
    written. In a remote process the call is sent to the hub, which checks
    ``scope`` against the run token and runs it there. A tuple of rules must
    all pass.

    ``result_scope="workspace"`` filters a list the function returns down to
    the items of the token's workspace (an item's ``workspace`` attribute or
    key): for a listing with no workspace argument that callers on the hub
    filter themselves. ``"workspace_or_shared"`` also keeps items with no
    workspace at all (a memory pool shared between workspaces). A callable
    ``(token_row, result) -> result`` filters anything else.

    ``cache_seconds`` keeps a remote process's answer for that long, per
    arguments: for the reads a build repeats many times a second (a change
    marker, a workspace's settings), where an answer a moment old is what a
    process on the hub would have read from its own caches anyway.
    """
    rules = scope if isinstance(scope, tuple) else (scope,)
    for rule in rules:
        if isinstance(rule, str):
            _parse_scope(rule)  # a typo fails at import, not at the first remote call
    if result_scope not in (None, "workspace", "workspace_or_shared") and not callable(result_scope):
        raise ValueError(f"unknown result_scope {result_scope!r}")

    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        key = _key(fn)
        signature = inspect.signature(fn)
        for rule in rules:
            if isinstance(rule, str):
                kind, _optional, arg = _parse_scope(rule)
                if arg is not None and not _has_arg(signature, arg):
                    raise ValueError(f"{key}: scope {rule!r} names '{arg}', which is not an argument")
        entry = Entry(key=key, fn=fn, scope=rules, signature=signature, result_scope=result_scope)
        _REGISTRY[key] = entry

        @functools.wraps(fn)
        def call(*args: Any, **kwargs: Any) -> Any:
            if not is_remote():
                return fn(*args, **kwargs)
            if cache_seconds > 0:
                return _cached_call(key, args, kwargs, cache_seconds)
            return _client_call(key, args, kwargs)

        call.__hub_call__ = entry  # type: ignore[attr-defined]
        return call

    return wrap


def _var_keyword(signature: inspect.Signature) -> Optional[str]:
    for name, param in signature.parameters.items():
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            return name
    return None


def _has_arg(signature: inspect.Signature, arg: str) -> bool:
    top = arg.split(".", 1)[0]
    return top in signature.parameters or _var_keyword(signature) is not None


def _arg_get(entry: Entry, bound: inspect.BoundArguments, arg: str) -> Tuple[bool, Any]:
    """``arg`` may be ``name`` or ``name.key`` (a key of a dict argument)."""
    top, _, key = arg.partition(".")
    if top in entry.signature.parameters:
        given, value = top in bound.arguments, bound.arguments.get(top)
    else:
        extra = bound.arguments.get(_var_keyword(entry.signature) or "", {}) or {}
        given, value = top in extra, extra.get(top)
    if not key:
        return given, value
    if isinstance(value, Mapping):
        return key in value, value.get(key)
    if value is not None and hasattr(value, key):  # a model passed whole
        return True, getattr(value, key)
    return False, None


def _arg_set(entry: Entry, bound: inspect.BoundArguments, arg: str, value: Any) -> None:
    top, _, key = arg.partition(".")
    if key:
        _, holder = _arg_get(entry, bound, top)
        if isinstance(holder, dict):
            holder[key] = value
        elif holder is not None and hasattr(holder, key):
            setattr(holder, key, value)
        return
    if top in entry.signature.parameters:
        bound.arguments[top] = value
        return
    name = _var_keyword(entry.signature)
    if name is not None:
        bound.arguments.setdefault(name, {})[top] = value


def register_resolver(kind: str, resolver: Callable[..., Any]) -> None:
    """``resolver(record_id)`` gives the workspace of a ``kind`` record: None
    for the default workspace, :data:`MISSING` for a record that does not
    exist. A resolver that takes a second argument gets the token's workspace,
    for a record another workspace may share (a flow in ``allowed_flows``)."""
    _RESOLVERS[str(kind)] = resolver


def registered() -> Dict[str, Entry]:
    """Every marked function the hub knows, after importing REMOTE_MODULES."""
    _load_modules()
    return dict(_REGISTRY)


def _load_modules() -> None:
    """Import every module of REMOTE_MODULES not imported yet (their marks
    fill the registry), and register the default resolvers once."""
    global _loaded
    missing = [name for name in REMOTE_MODULES if name not in _imported]
    if _loaded and not missing:
        return
    with _load_lock:
        for name in missing:
            importlib.import_module(name)
            _imported.add(name)
        if not _loaded:
            _default_resolvers()
            _loaded = True


def _parse_scope(scope: str) -> Tuple[str, bool, Optional[str]]:
    """``(kind, optional, argument)`` of one rule: ``"<kind>[?]:<arg>"``.
    ``workspace?`` checks a workspace argument only when one is given (an
    update that may move a record) instead of filling it in."""
    kind, _, arg = scope.partition(":")
    kind = kind.strip()
    optional = kind.endswith("?")
    kind = kind.rstrip("?")
    if not kind:
        raise ValueError(f"empty hub_call scope {scope!r}")
    if kind == "global":
        return kind, False, None
    if kind in ("workspace", "admin_workspace"):
        return kind, optional, (arg.strip() or "workspace")
    if not arg.strip():
        raise ValueError(f"hub_call scope {scope!r} names no argument")
    return kind, optional, arg.strip()


# ── scope checks (hub side) ──────────────────────────────────────────────────

def _workspace_name_of(value: Optional[str]) -> Optional[str]:
    """A workspace name, or the workspace a path under the workspaces root
    belongs to (a project folder inside it included)."""
    from common.workspace_context import normalize_workspace_name
    raw = str(value).strip() if value is not None else ""
    if raw.startswith("/"):
        from common.paths import WORKSPACES_ROOT
        try:
            rel = Path(raw).resolve().relative_to(Path(WORKSPACES_ROOT).resolve())
        except (ValueError, OSError):
            rel = None
        if rel is not None and rel.parts:
            return rel.parts[0]
    return normalize_workspace_name(raw or None)


def _same_workspace(a: Optional[str], b: Optional[str]) -> bool:
    return (_workspace_name_of(a) or "default") == (_workspace_name_of(b) or "default")


#: What a resolver returns for a record that does not exist (None means the
#: default workspace).
MISSING: Any = object()


def _default_resolvers() -> None:
    """Resolvers for the record kinds every stage uses; a module can register
    more (or replace these) with :func:`register_resolver`."""

    def task(task_id: str) -> Any:
        from uuid import UUID
        from tasks.service import get_task
        try:
            found = get_task(UUID(str(task_id)))
        except ValueError:
            return MISSING
        return MISSING if found is None else getattr(found, "workspace", None)

    def run(run_id: str) -> Any:
        from managers.run_manager import get_run_by_id
        found = get_run_by_id(run_id)
        return MISSING if not found else found.get("workspace")

    def instance(instance_id: str) -> Any:
        from instances import store as instance_store
        found = instance_store.get(instance_id)
        return MISSING if not found else found.get("workspace")

    def job(job_id: str) -> Any:
        from plans.service import get_job
        found = get_job(job_id)
        return MISSING if found is None else getattr(found, "workspace", None)

    def notification(notification_id: str) -> Any:
        from plans.service import notification_store
        try:
            found = notification_store.get(UUID(str(notification_id)))
        except ValueError:
            return MISSING
        return MISSING if found is None else getattr(found, "workspace", None)

    def flow(flow_id: str, token_ws: Optional[str] = None) -> Any:
        from flow.store import get_flow
        found = get_flow(flow_id)
        if not found:
            return MISSING
        owner = found.get("workspace")
        if token_ws and not _same_workspace(owner, token_ws):
            # A workspace may run flows it was given (allowed_flows).
            from workspace.storage import get_workspace_metadata
            allowed = get_workspace_metadata(token_ws).get("allowed_flows") or []
            if str(flow_id) in {str(f) for f in allowed}:
                return token_ws
        return owner

    def chat(chat_id: str) -> Any:
        from common.chat_store import get_chat
        found = get_chat(chat_id)
        return MISSING if not found else found.get("workspace")

    def session(session_id: str) -> Any:
        from common.session_service import get_context_by_id
        found = get_context_by_id(session_id)
        return MISSING if not found else found.get("workspace")

    def steering(msg_id: str) -> Any:
        from common.steering import get as get_steer
        found = get_steer(msg_id)
        return MISSING if not found else run(str(found.get("run_id") or ""))

    def entity_run(run_id: str) -> Any:
        from common.entity_runs import get as get_entity_run
        found = get_entity_run(run_id)
        return MISSING if not found else found.get("workspace")

    def loop(loop_id: str) -> Any:
        from loops.store import get_loop
        found = get_loop(loop_id)
        return MISSING if found is None else getattr(found, "workspace", None)

    def team(team_id: str) -> Any:
        from teams.store import get_team
        found = get_team(team_id)
        return MISSING if found is None else getattr(found, "workspace", None)

    def scenario(scenario_id: str) -> Any:
        from playground.store import get_scenario
        found = get_scenario(scenario_id)
        return MISSING if found is None else getattr(found, "workspace", None)

    def world(world_id: str) -> Any:
        from playground.store import get_world
        found = get_world(world_id)
        return MISSING if found is None else getattr(found, "workspace", None)

    def pool(memory_id: str, token_ws: Optional[str] = None) -> Any:
        from memory.store import MemoryStore
        found = MemoryStore()._get(memory_id)
        if found is None:
            return MISSING
        # A pool with no workspace is shared: any workspace's agents may be
        # bound to it.
        return getattr(found, "workspace", None) or token_ws

    def project(project_id: str) -> Any:
        from projects.storage import ProjectStore
        found = ProjectStore().get(project_id)
        return MISSING if found is None else getattr(found, "workspace", None)

    def eval_set(eval_set_id: str) -> Any:
        from evals.store import get_eval_set
        found = get_eval_set(eval_set_id)
        return MISSING if found is None else getattr(found, "workspace", None)

    def eval_run(eval_run_id: str) -> Any:
        from evals.store import get_eval_run
        found = get_eval_run(eval_run_id)
        return MISSING if found is None else getattr(found, "workspace", None)

    def view(view_id: str) -> Any:
        from views.store import view_index_row
        found = view_index_row(view_id)
        return MISSING if not found else found.get("workspace")

    def file(file_id: str) -> Any:
        from files.service import get_file
        found = get_file(file_id, include_deleted=True)
        return MISSING if not found else found.get("workspace")

    _RESOLVERS.setdefault("project", project)
    _RESOLVERS.setdefault("eval_set", eval_set)
    _RESOLVERS.setdefault("eval_run", eval_run)
    _RESOLVERS.setdefault("view", view)
    _RESOLVERS.setdefault("file", file)
    _RESOLVERS.setdefault("pool", pool)
    _RESOLVERS.setdefault("entity_run", entity_run)
    _RESOLVERS.setdefault("loop", loop)
    _RESOLVERS.setdefault("team", team)
    _RESOLVERS.setdefault("scenario", scenario)
    _RESOLVERS.setdefault("world", world)
    _RESOLVERS.setdefault("chat", chat)
    _RESOLVERS.setdefault("session", session)
    _RESOLVERS.setdefault("steering", steering)
    _RESOLVERS.setdefault("task", task)
    _RESOLVERS.setdefault("run", run)
    _RESOLVERS.setdefault("instance", instance)
    _RESOLVERS.setdefault("job", job)
    _RESOLVERS.setdefault("notification", notification)
    _RESOLVERS.setdefault("flow", flow)


def check_scope(entry: Entry, token_row: Mapping[str, Any], bound: inspect.BoundArguments) -> None:
    """Refuse a call that reaches outside the token's workspace. Fills an empty
    workspace argument with the token's workspace."""
    for rule in entry.scope:
        if callable(rule):
            rule(dict(token_row), bound.arguments)
        else:
            _check_rule(entry, rule, token_row, bound)


def _check_rule(entry: Entry, rule: str, token_row: Mapping[str, Any],
                bound: inspect.BoundArguments) -> None:
    token_ws = token_row.get("workspace")
    kind, optional, arg = _parse_scope(rule)
    if kind == "global" or arg is None:
        return
    given, value = _arg_get(entry, bound, arg)
    if kind == "own_message":
        own = token_row.get("instance_id")
        if value in (None, ""):
            return
        from instances import inbox
        msg = inbox.get(str(value))
        if msg is None:
            return
        if not own or str(msg.get("instance_id") or "") != str(own):
            raise ScopeError(f"{entry.key}: message '{value}' is not this carrier's")
        return
    if kind == "own_instance":
        # The carrier's own row, nobody else's: a replica cannot steer
        # another replica of its workspace.
        own = token_row.get("instance_id")
        if not own or str(value or "") != str(own):
            raise ScopeError(f"{entry.key}: instance '{value}' is not this carrier's")
        return
    if kind in ("workspace", "admin_workspace"):
        if value in (None, ""):
            if not optional:
                _arg_set(entry, bound, arg, token_ws or None)
            return
        if _same_workspace(str(value), token_ws):
            return
        if kind == "admin_workspace" and _same_workspace(token_ws, None):
            # Workspaces are managed from a run of the default workspace
            # (common/workspace_scope.py WORKSPACE_ADMIN_TOOLS: the main agent
            # only, checked where the tool is built).
            return
        raise ScopeError(f"{entry.key}: workspace '{value}' is not this run's workspace")
    resolver = _RESOLVERS.get(kind)
    if resolver is None:
        raise ScopeError(f"{entry.key}: no resolver for record kind '{kind}'")
    if not given or value in (None, ""):
        return
    ids = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    takes_token = len(inspect.signature(resolver).parameters) > 1
    for record_id in ids:
        ws = resolver(str(record_id), token_ws) if takes_token else resolver(str(record_id))
        if ws is MISSING:
            continue  # the function finds nothing either
        if not _same_workspace(ws, token_ws):
            raise ScopeError(f"{entry.key}: {kind} '{record_id}' is not in this run's workspace")


def _item_workspace(item: Any) -> Any:
    if isinstance(item, Mapping):
        return item.get("workspace")
    return getattr(item, "workspace", None)


def _filter_result(entry: Entry, token_row: Mapping[str, Any], result: Any) -> Any:
    """A list keeps the items of the token's workspace; a single record of
    another workspace becomes None (not found, as the tools say for it)."""
    if entry.result_scope is None or result is None:
        return result
    if callable(entry.result_scope):
        return entry.result_scope(dict(token_row), result)
    token_ws = token_row.get("workspace")
    shared_ok = entry.result_scope == "workspace_or_shared"

    def keep(item: Any) -> bool:
        ws = _item_workspace(item)
        return (shared_ok and ws in (None, "")) or _same_workspace(ws, token_ws)

    if isinstance(result, list):
        return [item for item in result if keep(item)]
    return result if keep(result) else None


def dispatch(token_row: Mapping[str, Any], payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Run one remote call on the hub: the body of ``POST /api/run-state/call``.

    Returns ``{"ok": true, "result": ...}`` or ``{"ok": false, "error": ...}``.
    A refused scope or an unknown function raises (the route turns those into
    403 and 404); an exception inside the function is returned, so the caller
    gets it back as itself.
    """
    _load_modules()
    key = str(payload.get("fn") or "")
    entry = _REGISTRY.get(key)
    if entry is None or entry.fn.__module__ not in REMOTE_MODULES:
        raise LookupError(f"no remote function '{key}'")
    args = decode(payload.get("args") or [])
    kwargs = decode(payload.get("kwargs") or {})
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        raise ValueError("args must be a list and kwargs an object")
    try:
        bound = entry.signature.bind(*args, **kwargs)
    except TypeError as exc:
        raise ValueError(f"{key}: {exc}") from exc
    with hub_side():
        check_scope(entry, token_row, bound)
        try:
            result = _filter_result(entry, token_row, entry.fn(*bound.args, **bound.kwargs))
        except Exception as exc:  # noqa: BLE001 - the caller gets the exception back as itself
            log.debug("remote call %s raised", key, exc_info=True)
            return {"ok": False, "error": _encode_error(exc)}
    try:
        return {"ok": True, "result": encode(result)}
    except TypeError as exc:
        log.warning("remote call %s returned what cannot be sent back: %s", key, exc)
        return {"ok": False, "error": _encode_error(exc)}


# ── stores that are objects ──────────────────────────────────────────────────

class RemoteObject:
    """What an agent container gets instead of a store object bound to one
    record (a memory pool's episodes or graph): every public method call goes
    to the hub through ``call(key, method, *args, **kwargs)``, a function
    marked with :func:`hub_call` whose scope checks ``key``."""

    def __init__(self, call: Callable[..., Any], key: str, methods: frozenset,
                 attrs: Optional[Dict[str, Any]] = None) -> None:
        self._call = call
        self._key = key
        self._methods = methods
        for name, value in (attrs or {}).items():
            setattr(self, name, value)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name not in self._methods:
            raise AttributeError(name)

        def method(*args: Any, **kwargs: Any) -> Any:
            kwargs.pop("timeout", None)
            return self._call(self._key, name, *args, **kwargs)

        return method


def public_methods(cls: type) -> frozenset:
    """The names :class:`RemoteObject` forwards for ``cls``."""
    return frozenset(n for n, v in vars(cls).items()
                     if not n.startswith("_") and callable(v) and not isinstance(v, (staticmethod, classmethod)))


# ── codec ────────────────────────────────────────────────────────────────────

_TAG = "__rs"


def _own(module: str) -> bool:
    return module.split(".", 1)[0] in _OWN_PACKAGES


def encode(value: Any) -> Any:
    """``value`` as plain JSON, with the types JSON cannot carry tagged."""
    if isinstance(value, Enum):
        cls = type(value)
        if _own(cls.__module__):
            return {_TAG: "enum", "c": f"{cls.__module__}:{cls.__qualname__}", "v": encode(value.value)}
        value = value.value
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, UUID):
        return {_TAG: "uuid", "v": str(value)}
    if isinstance(value, Decimal):
        return {_TAG: "decimal", "v": str(value)}
    if isinstance(value, dict):
        if all(isinstance(k, str) for k in value) and _TAG not in value:
            return {k: encode(v) for k, v in value.items()}
        return {_TAG: "map", "v": [[encode(k), encode(v)] for k, v in value.items()]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, tuple) and not hasattr(value, "_fields"):
        return {_TAG: "tuple", "v": [encode(v) for v in value]}
    if isinstance(value, (set, frozenset)):
        return {_TAG: "set", "v": [encode(v) for v in value]}
    if isinstance(value, datetime):
        return {_TAG: "datetime", "v": value.isoformat()}
    if isinstance(value, date):
        return {_TAG: "date", "v": value.isoformat()}
    if isinstance(value, (bytes, bytearray)):
        return {_TAG: "bytes", "v": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, PurePath):
        return {_TAG: "path", "v": str(value)}
    cls = type(value)
    name = f"{cls.__module__}:{cls.__qualname__}"
    try:
        from pydantic import BaseModel
    except ImportError:  # pragma: no cover - pydantic is a hard dependency
        BaseModel = None  # type: ignore[assignment,misc]
    if BaseModel is not None and isinstance(value, BaseModel):
        return {_TAG: "model", "c": name, "v": encode(value.model_dump(mode="python"))}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
        return {_TAG: "dataclass", "c": name, "v": fields}
    if isinstance(value, tuple):  # a namedtuple
        return {_TAG: "tuple", "v": [encode(v) for v in value]}
    raise TypeError(f"cannot send a {name} to or from the hub")


def _own_class(name: str) -> type:
    module, _, qualname = str(name).partition(":")
    if not module or not qualname or not _own(module):
        raise ValueError(f"refusing to rebuild {name!r}")
    obj: Any = importlib.import_module(module)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    if not isinstance(obj, type):
        raise ValueError(f"{name!r} is not a class")
    return obj


def decode(value: Any) -> Any:
    """The inverse of :func:`encode`."""
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    tag = value.get(_TAG)
    if tag is None:
        return {k: decode(v) for k, v in value.items()}
    raw = value.get("v")
    if tag == "map":
        return {decode(k): decode(v) for k, v in raw}
    if tag == "tuple":
        return tuple(decode(v) for v in raw)
    if tag == "set":
        return {decode(v) for v in raw}
    if tag == "datetime":
        return datetime.fromisoformat(raw)
    if tag == "date":
        return date.fromisoformat(raw)
    if tag == "bytes":
        return base64.b64decode(raw)
    if tag == "path":
        return Path(raw)
    if tag == "uuid":
        return UUID(raw)
    if tag == "decimal":
        return Decimal(raw)
    if tag == "enum":
        cls = _own_class(value.get("c", ""))
        if not issubclass(cls, Enum):
            raise ValueError(f"{value.get('c')!r} is not an enum")
        return cls(decode(raw))
    if tag in ("model", "dataclass"):
        cls = _own_class(value.get("c", ""))
        fields = decode(raw) or {}
        if tag == "model":
            validate = getattr(cls, "model_validate", None)
            if validate is None:
                raise ValueError(f"{value.get('c')!r} is not a model")
            return validate(fields)
        if not dataclasses.is_dataclass(cls):
            raise ValueError(f"{value.get('c')!r} is not a dataclass")
        return cls(**fields)
    raise ValueError(f"unknown tag {tag!r}")


def _encode_error(exc: BaseException) -> Dict[str, Any]:
    cls = type(exc)
    return {"type": f"{cls.__module__}:{cls.__qualname__}", "message": str(exc)}


def _rebuild_error(error: Mapping[str, Any]) -> BaseException:
    name = str(error.get("type") or "")
    message = str(error.get("message") or "")
    module, _, qualname = name.partition(":")
    cls: Any = None
    if module == "builtins":
        import builtins
        cls = getattr(builtins, qualname, None)
    elif _own(module):
        try:
            cls = _own_class(name)
        except (ImportError, AttributeError, ValueError):
            cls = None
    if isinstance(cls, type) and issubclass(cls, Exception):
        try:
            return cls(message)
        except Exception:  # noqa: BLE001 - a constructor that wants more than a message
            log.debug("could not rebuild %s from its message", name, exc_info=True)
    return RemoteCallError(f"{name}: {message}")


# ── client (remote side) ─────────────────────────────────────────────────────

#: Replaced in tests by a function that takes the payload and returns the
#: response body; None means HTTP to the hub.
_transport: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
_session_lock = threading.Lock()
_session: Any = None


def _timeout() -> float:
    try:
        return max(1.0, float(os.environ.get(TIMEOUT_ENV, "") or DEFAULT_TIMEOUT))
    except ValueError:
        return DEFAULT_TIMEOUT


def _http_session() -> Any:
    global _session
    with _session_lock:
        if _session is None:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            session = requests.Session()
            # Only a connection that never opened is retried: once the request
            # is sent, the function may have run, and running it twice is not
            # safe for a write.
            retry = Retry(total=3, connect=3, read=0, status=0, other=0,
                          allowed_methods=None, backoff_factor=0.5)
            session.mount("http://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
            session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
            _session = session
        return _session


def _post(payload: Dict[str, Any]) -> Dict[str, Any]:
    if _transport is not None:
        return _transport(payload)
    from common.auth import auth_headers
    from common.hostnet import hub_base_url
    import requests
    try:
        resp = _http_session().post(hub_base_url() + CALL_PATH, json=payload,
                                    headers=auth_headers(), timeout=_timeout())
    except requests.RequestException as exc:
        raise RemoteStateError(f"the hub could not be reached for {payload.get('fn')}: {exc}") from exc
    if resp.status_code == 401:
        raise TokenRefused(f"{payload.get('fn')}: the hub no longer accepts this process's token")
    if resp.status_code == 403:
        raise ScopeError(_detail(resp) or f"{payload.get('fn')}: refused by the hub")
    if resp.status_code >= 400:
        raise RemoteStateError(f"{payload.get('fn')}: the hub answered {resp.status_code}: {_detail(resp)}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise RemoteStateError(f"{payload.get('fn')}: the hub answered with no JSON") from exc
    return body if isinstance(body, dict) else {}


def _detail(resp: Any) -> str:
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "")[:300]
    return str(body.get("detail") if isinstance(body, dict) else body)[:300]


_cache: Dict[str, Tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _cached_call(key: str, args: Tuple[Any, ...], kwargs: Dict[str, Any], seconds: float) -> Any:
    import copy
    import json
    import time
    try:
        slot = key + json.dumps([encode(list(args)), encode(kwargs)], sort_keys=True)
    except TypeError:
        return _client_call(key, args, kwargs)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(slot)
    if hit is not None and now - hit[0] < seconds:
        return copy.deepcopy(hit[1])
    value = _client_call(key, args, kwargs)
    with _cache_lock:
        if len(_cache) > 2048:
            _cache.clear()
        _cache[slot] = (now, value)
    return copy.deepcopy(value)


def forget_cached() -> None:
    """Drop every cached answer (a write this process made, or a test)."""
    with _cache_lock:
        _cache.clear()


#: The workspace folder's mount point in an agent container, and the
#: variable naming the folder as the hub sees it.
CONTAINER_WORKSPACE = "/workspace"
HOST_WORKSPACE_ENV = "AGENTS_HUB_HOST_WORKSPACE"


def _local_workspace_dir() -> Optional[Path]:
    """Where this process's own code looks for its workspace folder
    (WORKSPACES_ROOT/<name>), which in a container is a link to /workspace."""
    from common.paths import WORKSPACES_ROOT
    from common.workspace_context import normalize_workspace_name
    name = normalize_workspace_name(os.environ.get("AGENT_WORKSPACE"))
    return Path(WORKSPACES_ROOT) / name if name else None


def link_container_workspace() -> None:
    """In an agent container, make WORKSPACES_ROOT/<workspace> a link to the
    mounted /workspace, so code that finds the folder by the workspace's name
    (a view's files under .views, a project folder) finds the mounted one.
    Never raises."""
    if _mode() != "1" or not os.environ.get(HOST_WORKSPACE_ENV):
        return
    try:
        mount = Path(CONTAINER_WORKSPACE)
        local = _local_workspace_dir()
        if local is None or not mount.is_dir() or local.exists() or local.is_symlink():
            return
        local.parent.mkdir(parents=True, exist_ok=True)
        local.symlink_to(mount, target_is_directory=True)
    except OSError:
        log.debug("could not link the workspace folder", exc_info=True)


def _prefixes_to_hub() -> list:
    host = os.environ.get(HOST_WORKSPACE_ENV, "").strip().rstrip("/")
    if not host:
        return []
    out = [(CONTAINER_WORKSPACE, host)]
    local = _local_workspace_dir()
    if local is not None:
        out.append((str(local), host))
    return out


def _swap_prefix(value: Any, pairs: list) -> Any:
    if not isinstance(value, (str, PurePath)):
        return value
    text = str(value)
    for old, new in pairs:
        if text == old or text.startswith(old + "/"):
            out = new + text[len(old):]
            return Path(out) if isinstance(value, PurePath) else out
    return value


def _hub_path(value: Any) -> Any:
    """A path argument under the container's workspace folder (/workspace, or
    its link under WORKSPACES_ROOT) as the hub knows the folder."""
    return _swap_prefix(value, _prefixes_to_hub())


def _local_paths(value: Any, depth: int = 0) -> Any:
    """A result with the hub's paths of this workspace's folder turned into
    this container's: a returned Path, or a string that is one."""
    host = os.environ.get(HOST_WORKSPACE_ENV, "").strip().rstrip("/")
    if not host or depth > 6:
        return value
    if isinstance(value, (str, PurePath)):
        local = _local_workspace_dir()
        return _swap_prefix(value, [(host, str(local or CONTAINER_WORKSPACE))])
    if isinstance(value, list):
        return [_local_paths(v, depth + 1) for v in value]
    if isinstance(value, dict):
        return {k: _local_paths(v, depth + 1) for k, v in value.items()}
    return value


def _client_call(key: str, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Any:
    args = tuple(_hub_path(a) for a in args)
    kwargs = {k: _hub_path(v) for k, v in kwargs.items()}
    body = _post({"fn": key, "args": encode(list(args)), "kwargs": encode(kwargs)})
    if body.get("ok"):
        return _local_paths(decode(body.get("result")))
    error = body.get("error")
    if isinstance(error, dict):
        raise _rebuild_error(error)
    raise RemoteStateError(f"{key}: the hub answered without a result")


__all__ = [
    "CALL_PATH", "ENV", "Entry", "MISSING", "REMOTE_MODULES", "RemoteCallError",
    "RemoteObject", "RemoteStateError", "ScopeError", "TokenRefused", "check_scope", "decode", "dispatch", "encode", "hub_call",
    "forget_cached", "hub_side", "is_remote", "public_methods", "refuse_database", "register_resolver",
    "registered",
]


link_container_workspace()
