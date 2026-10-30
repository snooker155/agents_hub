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

Scope rules (the ``scope`` argument of :func:`hub_call`):

- ``"workspace"`` or ``"workspace:<arg>"``: the argument is a workspace name.
  Left empty, the hub fills in the token's workspace.
- ``"<kind>:<arg>"``: the argument is the id of a record of ``kind`` (``task``,
  ``run``, ``instance``, ...); a resolver registered with
  :func:`register_resolver` gives that record's workspace. A record that does
  not exist passes: the function finds nothing either.
- ``"global"``: the function reads nothing that belongs to a workspace (a
  price list, a model catalog).
- a callable ``(token_row, arguments) -> None`` that raises
  :class:`ScopeError` to refuse, for anything the shapes above cannot say.

Values cross as JSON. Dates, tuples, sets, bytes, paths, pydantic models and
dataclasses are tagged so they come back as what they were; a model or
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
from pathlib import Path, PurePath
from typing import Any, Callable, Dict, Iterator, Mapping, Optional, Tuple, Union

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
    "agents", "chat", "common", "connections", "connectors", "environments", "evals", "files",
    "flow", "hubsync", "instances", "loops", "managers", "memory", "notify", "plans",
    "playground", "projects", "providers", "runtime", "services", "tasks", "teams", "tools",
    "workspace",
})

#: Modules whose marked functions the hub may run for a remote caller. The hub
#: imports them (and only them) to fill the registry; a function in a module
#: missing here is refused even when it is marked.
REMOTE_MODULES: Tuple[str, ...] = ()

ScopeRule = Union[str, Callable[[Dict[str, Any], Dict[str, Any]], None]]


class RemoteStateError(RuntimeError):
    """A process without a database tried to open one, or a remote call could
    not be made at all (the hub unreachable, an unmarked function)."""


class RemoteCallError(RuntimeError):
    """The hub ran the function and it raised something that cannot be
    rebuilt on this side; the message names the original type."""


class ScopeError(PermissionError):
    """The call reaches outside the workspace the run token was minted for."""


# ── where this process stands ────────────────────────────────────────────────

#: Overrides the environment for the current context: the hub side of an
#: in-process call (tests) runs with False, so the function body opens the
#: database there.
_FORCE: contextvars.ContextVar[Optional[bool]] = contextvars.ContextVar(
    "remote_state_force", default=None)


def is_remote() -> bool:
    """Whether this process reaches state through the hub instead of the
    database."""
    forced = _FORCE.get()
    if forced is not None:
        return forced
    return os.environ.get(ENV, "").strip() == "1"


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
        raise RemoteStateError(
            f"{what} opened in an agent process that has no database ({ENV}=1): "
            "the store function on this path needs @remote_state.hub_call")


# ── registry ─────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Entry:
    key: str
    fn: Callable[..., Any]
    scope: ScopeRule
    signature: inspect.Signature


_REGISTRY: Dict[str, Entry] = {}
_RESOLVERS: Dict[str, Callable[[str], Any]] = {}
_loaded = False
_load_lock = threading.Lock()


def _key(fn: Callable[..., Any]) -> str:
    return f"{fn.__module__}:{fn.__qualname__}"


def hub_call(scope: ScopeRule) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a store function as callable from a remote agent process.

    On the hub (and in any process with a database) the function runs as
    written. In a remote process the call is sent to the hub, which checks
    ``scope`` against the run token and runs it there.
    """
    if isinstance(scope, str):
        _parse_scope(scope)  # a typo fails at import, not at the first remote call

    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        key = _key(fn)
        entry = Entry(key=key, fn=fn, scope=scope, signature=inspect.signature(fn))
        _REGISTRY[key] = entry

        @functools.wraps(fn)
        def call(*args: Any, **kwargs: Any) -> Any:
            if not is_remote():
                return fn(*args, **kwargs)
            return _client_call(key, args, kwargs)

        call.__hub_call__ = entry  # type: ignore[attr-defined]
        return call

    return wrap


def register_resolver(kind: str, resolver: Callable[[str], Any]) -> None:
    """``resolver(record_id)`` gives the workspace of a ``kind`` record: None
    for the default workspace, :data:`MISSING` for a record that does not
    exist."""
    _RESOLVERS[str(kind)] = resolver


def registered() -> Dict[str, Entry]:
    """Every marked function the hub knows, after importing REMOTE_MODULES."""
    _load_modules()
    return dict(_REGISTRY)


def _load_modules() -> None:
    global _loaded
    if _loaded:
        return
    with _load_lock:
        if _loaded:
            return
        for name in REMOTE_MODULES:
            importlib.import_module(name)
        _default_resolvers()
        _loaded = True


def _parse_scope(scope: str) -> Tuple[str, Optional[str]]:
    kind, _, arg = scope.partition(":")
    kind = kind.strip()
    if not kind:
        raise ValueError(f"empty hub_call scope {scope!r}")
    if kind == "global":
        return kind, None
    if kind == "workspace":
        return kind, (arg.strip() or "workspace")
    if not arg.strip():
        raise ValueError(f"hub_call scope {scope!r} names no argument")
    return kind, arg.strip()


# ── scope checks (hub side) ──────────────────────────────────────────────────

def _same_workspace(a: Optional[str], b: Optional[str]) -> bool:
    from common.workspace_context import normalize_workspace_name
    left = normalize_workspace_name(a) or "default"
    right = normalize_workspace_name(b) or "default"
    return left == right


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

    _RESOLVERS.setdefault("task", task)
    _RESOLVERS.setdefault("run", run)
    _RESOLVERS.setdefault("instance", instance)


def check_scope(entry: Entry, token_row: Mapping[str, Any], bound: inspect.BoundArguments) -> None:
    """Refuse a call that reaches outside the token's workspace. Fills an empty
    workspace argument with the token's workspace."""
    token_ws = token_row.get("workspace")
    scope = entry.scope
    if callable(scope):
        scope(dict(token_row), bound.arguments)
        return
    kind, arg = _parse_scope(scope)
    if kind == "global":
        return
    if arg not in entry.signature.parameters:
        raise ScopeError(f"{entry.key}: scope names '{arg}', which is not an argument")
    value = bound.arguments.get(arg)
    if kind == "workspace":
        if value in (None, ""):
            bound.arguments[arg] = token_ws or None
            return
        if not _same_workspace(str(value), token_ws):
            raise ScopeError(f"{entry.key}: workspace '{value}' is not this run's workspace")
        return
    resolver = _RESOLVERS.get(kind)
    if resolver is None:
        raise ScopeError(f"{entry.key}: no resolver for record kind '{kind}'")
    if value in (None, ""):
        return
    ids = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    for record_id in ids:
        ws = resolver(str(record_id))
        if ws is MISSING:
            continue  # the function finds nothing either
        if not _same_workspace(ws, token_ws):
            raise ScopeError(f"{entry.key}: {kind} '{record_id}' is not in this run's workspace")


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
            result = entry.fn(*bound.args, **bound.kwargs)
        except Exception as exc:  # noqa: BLE001 - the caller gets the exception back as itself
            log.debug("remote call %s raised", key, exc_info=True)
            return {"ok": False, "error": _encode_error(exc)}
    try:
        return {"ok": True, "result": encode(result)}
    except TypeError as exc:
        log.warning("remote call %s returned what cannot be sent back: %s", key, exc)
        return {"ok": False, "error": _encode_error(exc)}


# ── codec ────────────────────────────────────────────────────────────────────

_TAG = "__rs"


def _own(module: str) -> bool:
    return module.split(".", 1)[0] in _OWN_PACKAGES


def encode(value: Any) -> Any:
    """``value`` as plain JSON, with the types JSON cannot carry tagged."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
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
            pass
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


def _client_call(key: str, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Any:
    body = _post({"fn": key, "args": encode(list(args)), "kwargs": encode(kwargs)})
    if body.get("ok"):
        return decode(body.get("result"))
    error = body.get("error")
    if isinstance(error, dict):
        raise _rebuild_error(error)
    raise RemoteStateError(f"{key}: the hub answered without a result")


__all__ = [
    "CALL_PATH", "ENV", "Entry", "MISSING", "REMOTE_MODULES", "RemoteCallError",
    "RemoteStateError", "ScopeError", "check_scope", "decode", "dispatch", "encode", "hub_call",
    "hub_side", "is_remote", "refuse_database", "register_resolver", "registered",
]
