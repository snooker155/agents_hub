"""
Connection proposals: an agent prepares a connection, a person finishes it.

Setting up a connector used to be a page only a person could reach: an agent
could use Jira once somebody had typed the token on the Connectors page, but
could not help get there. This module lets the agent do the part that needs no
trust (which service, which URL, which project, which MCP command) and leaves
the part that does to the person:

* **Secrets never pass through the model.** A proposal carries the names of
  the secret fields, never their values; a value the agent tries to fill in is
  refused (:class:`ProposalError` ``secret_from_agent``). The person types the
  secret into the chat card, and it goes straight from the browser to the hub.
* **Nothing changes until a person says so.** The agent's tool
  (``tools/connection_setup.py``) records the proposal as a waiting tool call
  (``common/tool_approvals.py``, tool ``propose_connection``); the change is
  applied by ``POST /api/connection-proposals/{id}/apply``, which runs as the
  person who pressed Connect, with the same role checks as the pages.
* **The agent cannot widen what it is trusted with.** An MCP server proposed
  by an agent claims every capability (untrusted input, private reads,
  outbound sends), whatever the agent asked; only the person may narrow the
  claim in the card. A stdio server's command is shown as a warning, since
  connecting it runs that command on the hub's host.

Kinds: ``connector`` (the credential connectors of connectors/credentials.py:
Jira, Linear, Google, Microsoft, Notion, Confluence), ``channel`` (Slack,
Discord, Teams, mail), ``mcp_server``, ``database`` (a read-only connection),
``watcher`` (an IMAP or HTTP observer) and ``secret`` (a workspace secret).

A proposal is plain JSON (:func:`build`), shaped for the card: a flat list of
fields, nested keys spelled with a dot (``headers.Authorization``), workspace
secrets a watcher needs spelled ``secret:<NAME>``. :func:`apply` takes the
person's values and secrets in the same flat keys, validates the whole thing
again through :func:`build` and performs it.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger("connectors.proposals")

TOOL = "propose_connection"

KINDS: Tuple[str, ...] = ("connector", "channel", "mcp_server", "database", "watcher", "secret")
HUB_KINDS = frozenset({"connector", "channel"})

#: The capability claim of an MCP server an agent proposes, before the person
#: narrows it (see tools/capabilities.py for what each one means).
MCP_CAPABILITIES = ("ingests_untrusted", "reads_private", "can_exfiltrate")

#: Connectors handled by another kind: the databases connector has no config
#: of its own, its connections are the ``database`` kind.
_NOT_A_CONNECTOR = frozenset({"databases"})

#: Where the person finishes a connector that needs a browser sign in.
_SIGN_IN_HREF = {"google": "/connectors?tab=google", "microsoft": "/connectors?tab=microsoft"}

_SECRET_PREFIX = "secret:"


class ProposalError(ValueError):
    """A proposal that cannot be built or applied, with a short machine code."""

    def __init__(self, message: str, *, code: str = "bad_request", status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


# ── fields ───────────────────────────────────────────────────────────────────

def _field(key: str, *, kind: str = "text", secret: bool = False, required: bool = False,
           value: Any = "", placeholder: str = "", options: Iterable[str] = (),
           has_value: bool = False) -> Dict[str, Any]:
    if secret:
        kind = "password"
        value = ""
    return {"key": key, "kind": kind, "secret": bool(secret), "required": bool(required),
            "value": value, "placeholder": placeholder or "", "options": list(options),
            "has_value": bool(has_value)}


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == [] or value == {}


def _as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [p.strip() for p in re.split(r"[\n,]", value) if p.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _nest(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """``{"headers.X": 1}`` and ``{"headers": {"X": 1}}`` read the same."""
    out: Dict[str, Any] = {}
    for key, value in (config or {}).items():
        key = str(key)
        if "." in key and not key.startswith(_SECRET_PREFIX):
            head, tail = key.split(".", 1)
            if head in ("headers", "env", "capabilities"):
                out.setdefault(head, {})
                if isinstance(out[head], dict):
                    out[head][tail] = value
                continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key].update(value)
        else:
            out[key] = value
    return out


def _refuse_secret(key: str) -> None:
    raise ProposalError(
        f"'{key}' is a secret: leave it out. The person types it into the card, "
        "so it never passes through the conversation.", code="secret_from_agent")


# ── per kind ─────────────────────────────────────────────────────────────────

def _connector(target: str, config: Dict[str, Any], workspace: str, *, from_agent: bool) -> Dict[str, Any]:
    from connectors import credentials

    spec = credentials.get(target)
    if spec is None or spec.name in _NOT_A_CONNECTOR:
        names = [n for n in credentials.names() if n not in _NOT_A_CONNECTOR]
        hint = " (a database connection is the 'database' kind)" if target == "databases" else ""
        raise ProposalError(f"unknown connector '{target}'{hint}; one of: {', '.join(names)}",
                            code="unknown_target")
    visible = [f for f in spec.fields if f.kind != "hidden"]
    known = {f.key for f in visible}
    unknown = sorted(k for k in config if k not in known)
    if unknown:
        raise ProposalError(f"unknown fields for {spec.name}: {', '.join(unknown)}; "
                            f"fields are {', '.join(sorted(known))}", code="unknown_field")
    # The workspace's own connector (connectors/channels/store.py): what is
    # stored there, never the default's it would otherwise inherit.
    store = spec.store.for_workspace(workspace)
    current = _safe(store.public_config, {})
    fields = []
    for f in visible:
        value = config.get(f.key)
        if f.secret:
            if from_agent and not _blank(value):
                _refuse_secret(f.key)
            fields.append(_field(f.key, secret=True, required=f.required or f.key in spec.required,
                                 placeholder=f.placeholder,
                                 has_value=bool(current.get(f"has_{f.key}"))))
        else:
            if _blank(value):
                value = current.get(f.key, "")
            fields.append(_field(f.key, kind=f.kind if f.kind in ("text", "number", "textarea", "select")
                                 else "text", required=f.required or f.key in spec.required,
                                 value=value if value is not None else "", placeholder=f.placeholder,
                                 options=f.options))
    out = {"title": spec.name.capitalize(), "scope": _scope_of(workspace), "fields": fields,
           "replaces": bool(_safe(lambda: store.is_configured(*spec.required), False)),
           "warnings": []}
    if spec.name in _SIGN_IN_HREF:
        out["warnings"].append(f"After Connect, finish with the {spec.name.capitalize()} sign in "
                               "on the Connectors page.")
    return out


def _channel(target: str, config: Dict[str, Any], workspace: str, *, from_agent: bool) -> Dict[str, Any]:
    from connectors.channels import registry as channels

    spec = channels.get(target)
    if spec is None:
        raise ProposalError(f"unknown channel '{target}'; one of: "
                            f"{', '.join(c.name for c in channels.all_channels())}",
                            code="unknown_target")
    visible = [f for f in spec.fields if f.kind != "hidden"]
    known = {f.key for f in visible} | {"enabled", "allowed"}
    unknown = sorted(k for k in config if k not in known)
    if unknown:
        raise ProposalError(f"unknown fields for {spec.name}: {', '.join(unknown)}; "
                            f"fields are {', '.join(sorted(known))}", code="unknown_field")
    store = spec.store.for_workspace(workspace)
    current = _safe(store.public_config, {})
    required = set(getattr(spec.service, "required_fields", ()) or ())
    fields = []
    for f in visible:
        value = config.get(f.key)
        if f.secret:
            if from_agent and not _blank(value):
                _refuse_secret(f.key)
            fields.append(_field(f.key, secret=True, required=f.required or f.key in required,
                                 placeholder=f.placeholder,
                                 has_value=bool(current.get(f"has_{f.key}"))))
        else:
            if _blank(value):
                value = current.get(f.key, "")
            fields.append(_field(f.key, kind=f.kind if f.kind in ("text", "number", "textarea", "select")
                                 else "text", required=f.required or f.key in required,
                                 value=value if value is not None else "", placeholder=f.placeholder,
                                 options=f.options))
    allowed = config.get("allowed")
    if allowed is None:
        allowed = _safe(store.get_allowed, [])
    fields.append(_field("enabled", kind="bool", value=_as_bool(config.get("enabled"), True)))
    fields.append(_field("allowed", kind="list", value=_as_list(allowed)))
    return {"title": spec.name.capitalize(), "scope": _scope_of(workspace), "fields": fields,
            "replaces": bool(_safe(lambda: store.is_configured(*required), False)),
            "warnings": [] if _as_list(allowed) else
            ["The allowlist is empty: the channel will answer nobody until chats are added."]}


def _mcp_server(target: str, config: Dict[str, Any], workspace: str, *, from_agent: bool) -> Dict[str, Any]:
    from mcp_client import catalog
    from mcp_client import store as mcp_store

    server_id = str(config.get("id") or target or "").strip()
    try:
        server_id = mcp_store.validate_id(server_id)
    except ValueError as exc:
        raise ProposalError(str(exc), code="bad_id") from exc
    known = {"id", "name", "description", "transport", "command", "args", "url", "headers", "env",
             "tool_allowlist", "approval", "capabilities"}
    unknown = sorted(k for k in config if k not in known)
    if unknown:
        raise ProposalError(f"unknown fields for an MCP server: {', '.join(unknown)}; "
                            f"fields are {', '.join(sorted(known))}", code="unknown_field")
    transport = str(config.get("transport") or "stdio").strip()
    if transport not in mcp_store.TRANSPORTS:
        raise ProposalError(f"transport must be one of {', '.join(mcp_store.TRANSPORTS)}",
                            code="bad_transport")
    command = str(config.get("command") or "").strip()
    args = _as_list(config.get("args")) if not isinstance(config.get("args"), str) \
        else [a for a in str(config.get("args")).split("\n") if a.strip()]
    url = str(config.get("url") or "").strip()
    if transport == "stdio" and not command:
        raise ProposalError("a stdio MCP server needs 'command'", code="missing_field")
    if transport != "stdio":
        if not url:
            raise ProposalError(f"a {transport} MCP server needs 'url'", code="missing_field")
        try:
            mcp_store.validate_url(transport, url)
        except ValueError as exc:
            raise ProposalError(str(exc), code="bad_url") from exc
    from common import isolation
    if isolation.is_isolated(workspace):
        raise ProposalError(f"workspace '{workspace}' is isolated: an MCP server would be a way "
                            "around its perimeter", code="isolated_workspace", status=409)
    if _safe(lambda: mcp_store.get_server(workspace, server_id), None) is not None:
        raise ProposalError(f"MCP server '{server_id}' already exists in workspace '{workspace}'; "
                            "edit it on the MCP page", code="exists", status=409)

    fields = [
        _field("id", required=True, value=server_id),
        _field("name", value=str(config.get("name") or server_id)),
        _field("description", value=str(config.get("description") or "")),
        _field("transport", kind="select", required=True, value=transport,
               options=mcp_store.TRANSPORTS),
    ]
    if transport == "stdio":
        fields.append(_field("command", required=True, value=command))
        fields.append(_field("args", kind="list", value=args))
    else:
        fields.append(_field("url", required=True, value=url))
    for group in ("headers", "env"):
        raw = config.get(group) or {}
        if not isinstance(raw, dict):
            raise ProposalError(f"'{group}' must be an object of names to values", code="bad_field")
        for name, value in raw.items():
            key = f"{group}.{str(name).strip()}"
            if mcp_store.is_secret_name(name):
                if from_agent and not _blank(value):
                    _refuse_secret(key)
                fields.append(_field(key, secret=True, required=True))
            else:
                fields.append(_field(key, value="" if value is None else str(value)))
    fields.append(_field("tool_allowlist", kind="list", value=_as_list(config.get("tool_allowlist"))))
    approval = config.get("approval", "none")
    fields.append(_field("approval", kind="select", value=approval if approval in ("none", "all") else "none",
                         options=("none", "all")))
    claim = config.get("capabilities") if isinstance(config.get("capabilities"), dict) else {}
    for cap in MCP_CAPABILITIES:
        # An agent cannot vouch for a server: from the agent, every claim is on.
        on = True if from_agent else _as_bool(claim.get(cap), True)
        fields.append(_field(f"capabilities.{cap}", kind="bool", value=on))

    warnings = []
    if transport == "stdio":
        warnings.append(f"Connecting runs `{' '.join([command, *args]).strip()}` on the hub's host.")
    else:
        warnings.append(f"Tool calls go to {url}.")
    record = {"id": server_id, "command": command, "args": args, "url": url, "transport": transport}
    if _safe(catalog.allowlist_only, False) and not _safe(lambda: catalog.matches(record), False):
        raise ProposalError("this MCP server does not match an approved catalog entry, and this hub "
                            "only allows approved ones; ask an admin to approve it", code="not_approved",
                            status=403)
    return {"title": str(config.get("name") or server_id), "scope": "workspace", "fields": fields,
            "replaces": False, "warnings": warnings}


def _database(target: str, config: Dict[str, Any], workspace: str, *, from_agent: bool) -> Dict[str, Any]:
    from connectors.databases import store as db_store

    known = {"name", "kind", "dsn", "allowed_schemas", "row_limit", "timeout_seconds"}
    unknown = sorted(k for k in config if k not in known)
    if unknown:
        raise ProposalError(f"unknown fields for a database connection: {', '.join(unknown)}; "
                            f"fields are {', '.join(sorted(known))}", code="unknown_field")
    if from_agent and not _blank(config.get("dsn")):
        _refuse_secret("dsn")
    name = str(config.get("name") or target or "").strip()
    if not name:
        raise ProposalError("a database connection needs 'name'", code="missing_field")
    kind = str(config.get("kind") or "").strip().lower()
    if kind not in db_store.KINDS:
        raise ProposalError(f"'kind' must be one of {', '.join(db_store.KINDS)}", code="bad_kind")
    existing = _safe(lambda: db_store.list_connections(workspace), [])
    if any(str(c.get("name")) == name for c in existing):
        raise ProposalError(f"a database connection named '{name}' already exists in '{workspace}'",
                            code="exists", status=409)
    fields = [
        _field("name", required=True, value=name),
        _field("kind", kind="select", required=True, value=kind, options=db_store.KINDS),
        _field("dsn", secret=True, required=True,
               placeholder=f"{kind}://user:password@host:port/database" if kind != "sqlite" else "/path/to.db"),
        _field("allowed_schemas", kind="list", value=_as_list(config.get("allowed_schemas"))),
        _field("row_limit", kind="number", value=config.get("row_limit") or db_store.DEFAULT_ROW_LIMIT),
    ]
    if config.get("timeout_seconds") not in (None, ""):
        fields.append(_field("timeout_seconds", kind="number", value=config.get("timeout_seconds")))
    return {"title": name, "scope": "workspace", "fields": fields, "replaces": False,
            "warnings": ["Queries stay read only: the hub refuses anything but reads."]}


def _workspace_secret_names(workspace: str) -> set:
    from common import secrets as secret_store
    return {str(r.get("name")) for r in _safe(lambda: secret_store.list_secrets(workspace), [])}


def _watcher(target: str, config: Dict[str, Any], workspace: str, *, from_agent: bool) -> Dict[str, Any]:
    from common import secrets as secret_store
    from watchers import kinds as watcher_kinds
    from watchers.models import DEFAULT_INTERVAL_SECONDS, KINDS as WATCHER_KINDS

    kind = str(config.get("kind") or "").strip().lower()
    if kind not in WATCHER_KINDS:
        raise ProposalError(f"'kind' must be one of {', '.join(WATCHER_KINDS)}", code="bad_kind")
    specs = watcher_kinds.CONFIG_FIELDS[kind]
    known = {"name", "kind", "interval_seconds"} | {s["name"] for s in specs}
    unknown = sorted(k for k in config if k not in known and not k.startswith(_SECRET_PREFIX))
    if unknown:
        raise ProposalError(f"unknown fields for a {kind} watcher: {', '.join(unknown)}; "
                            f"fields are {', '.join(sorted(known))}", code="unknown_field")
    name = str(config.get("name") or target or "").strip()
    if not name:
        raise ProposalError("a watcher needs 'name'", code="missing_field")
    watcher_config = {s["name"]: config[s["name"]] for s in specs if s["name"] in config}
    try:
        validated = watcher_kinds.validate_config(kind, watcher_config)
    except ValueError as exc:
        raise ProposalError(str(exc), code="bad_config") from exc

    fields = [_field("name", required=True, value=name),
              _field("kind", kind="select", required=True, value=kind, options=WATCHER_KINDS),
              _field("interval_seconds", kind="number",
                     value=config.get("interval_seconds") or DEFAULT_INTERVAL_SECONDS)]
    for spec in specs:
        if spec.get("hidden_when") and any(validated.get(k) == v for k, v in spec["hidden_when"].items()):
            continue
        typ = spec["type"]
        value = validated.get(spec["name"], spec.get("default", ""))
        if typ == "secret":
            if value:
                try:
                    secret_store.validate_name(str(value))
                except secret_store.SecretsError as exc:
                    raise ProposalError(f"'{spec['name']}' names a workspace secret: {exc}",
                                        code="bad_secret_name") from exc
            fields.append(_field(spec["name"], value=value, required=bool(spec["required"]) and not any(
                validated.get(k) == v for k, v in (spec.get("optional_when") or {}).items())))
        else:
            fields.append(_field(spec["name"], kind={"bool": "bool", "int": "number"}.get(typ, "text"),
                                 required=bool(spec["required"]), value=value))
    # Each secret the watcher names and the workspace does not hold yet gets
    # an input of its own, so one card sets the watcher up completely.
    have = _workspace_secret_names(workspace)
    for spec in specs:
        secret_name = str(validated.get(spec["name"]) or "") if spec["type"] == "secret" else ""
        if secret_name and secret_name not in have:
            key = f"{_SECRET_PREFIX}{secret_name}"
            if from_agent and not _blank(config.get(key)):
                _refuse_secret(key)
            fields.append(_field(key, secret=True, required=True))
    warnings = []
    if validated.get("use_google"):
        warnings.append("Reads the hub's Google account mailbox; only an administrator may connect it.")
    return {"title": name, "scope": "workspace", "fields": fields, "replaces": False,
            "warnings": warnings}


def _secret(target: str, config: Dict[str, Any], workspace: str, *, from_agent: bool) -> Dict[str, Any]:
    from common import secrets as secret_store

    known = {"value", "allowed_hosts", "agent_id"}
    unknown = sorted(k for k in config if k not in known)
    if unknown:
        raise ProposalError(f"unknown fields for a secret: {', '.join(unknown)}; "
                            f"fields are {', '.join(sorted(known))}", code="unknown_field")
    if from_agent and not _blank(config.get("value")):
        _refuse_secret("value")
    try:
        name = secret_store.validate_name(target)
    except secret_store.SecretsError as exc:
        raise ProposalError(str(exc), code="bad_secret_name") from exc
    agent_id = str(config.get("agent_id") or "").strip()
    if agent_id:
        from agents.registry import get_agent
        if _safe(lambda: get_agent(agent_id), None) is None:
            raise ProposalError(f"unknown agent '{agent_id}'", code="unknown_agent")
    fields = [_field("value", secret=True, required=True),
              _field("allowed_hosts", kind="list", value=_as_list(config.get("allowed_hosts"))),
              _field("agent_id", value=agent_id)]
    exists = name in _workspace_secret_names(workspace)
    return {"title": name, "scope": "workspace", "fields": fields, "replaces": exists,
            "warnings": [] if _as_list(config.get("allowed_hosts")) else
            ["No host list: the value may be sent anywhere the agent's tools reach."]}


def _safe(fn, default):
    """Current state is a courtesy on the card (``has_value``, ``replaces``):
    a store this process cannot read leaves the default, never fails the build."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - see the docstring
        log.debug("proposals: state read failed", exc_info=True)
        return default


# ── the public API ───────────────────────────────────────────────────────────

def build(kind: str, target: str = "", config: Optional[Dict[str, Any]] = None, *,
          workspace: Optional[str] = None, from_agent: bool = True) -> Dict[str, Any]:
    """The proposal for ``kind``/``target`` with ``config``, or :class:`ProposalError`.

    ``from_agent`` refuses secret values and resets an MCP capability claim to
    "everything"; :func:`apply` rebuilds with ``from_agent=False`` from what the
    person sent.
    """
    kind = str(kind or "").strip().lower()
    if kind not in KINDS:
        raise ProposalError(f"kind must be one of {', '.join(KINDS)}", code="bad_kind")
    target = str(target or "").strip()
    cfg = _nest(config if isinstance(config, dict) else {})
    if kind in HUB_KINDS:
        # A connector lives in the workspace that defines it; the default
        # workspace's live everywhere (connectors/channels/store.py).
        ws = str(workspace or "").strip() or "default"
        workspace = ws
        body = (_connector if kind == "connector" else _channel)(target.lower(), cfg, ws,
                                                                 from_agent=from_agent)
    else:
        ws = str(workspace or "").strip()
        if not ws:
            raise ProposalError(f"a {kind} belongs to a workspace, and this run has none",
                                code="no_workspace")
        builder = {"mcp_server": _mcp_server, "database": _database, "watcher": _watcher,
                   "secret": _secret}[kind]
        body = builder(target, cfg, ws, from_agent=from_agent)
    if kind == "mcp_server":
        target = next(f["value"] for f in body["fields"] if f["key"] == "id")
    elif kind in ("database", "watcher"):
        target = next(f["value"] for f in body["fields"] if f["key"] == "name")
    return {"kind": kind, "target": target, "workspace": workspace, **body}


def _scope_of(workspace: Optional[str]) -> str:
    """``everywhere`` for the default workspace's connectors, else ``workspace``."""
    return "everywhere" if (workspace or "default") == "default" else "workspace"


def required_role(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """What applying needs from the person, as ``identity.require_role`` kwargs.

    The same bar the page for that thing sets: everything needs an editor of
    the workspace it lands in (a connector or a channel of the default
    workspace, which every workspace inherits, an editor of the default), and
    a secret, or a watcher that brings
    new secrets, needs the workspace owner. A watcher on the hub's Google
    mailbox needs an administrator, as on the Watchers page.
    """
    from common.auth import WS_EDITOR, WS_OWNER

    kind = proposal.get("kind")
    if kind in HUB_KINDS:
        return {"workspace": proposal.get("workspace") or "default", "role": WS_EDITOR}
    values = {f["key"]: f.get("value") for f in proposal.get("fields") or []}
    if kind == "watcher" and _as_bool(values.get("use_google")):
        return {"admin": True}
    owner = kind == "secret" or any(str(k).startswith(_SECRET_PREFIX) for k in values)
    return {"workspace": proposal.get("workspace"), "role": WS_OWNER if owner else WS_EDITOR}


def _merged(proposal: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    """The flat config the person settled on: their values over the proposal's.

    Secret fields stay in it, blank, so the rebuild still asks for them (an
    MCP header exists only because its name is in the config)."""
    out: Dict[str, Any] = {}
    for f in proposal.get("fields") or []:
        key = f["key"]
        if f.get("secret"):
            out[key] = ""
            continue
        out[key] = values[key] if key in (values or {}) else f.get("value")
    return out


def _secrets_for(proposal: Dict[str, Any], secrets: Dict[str, Any]) -> Dict[str, str]:
    """The person's secrets, checked against what the proposal asks for."""
    given = {str(k): str(v) for k, v in (secrets or {}).items() if not _blank(v)}
    asked = {f["key"]: f for f in proposal.get("fields") or [] if f.get("secret")}
    extra = sorted(k for k in given if k not in asked)
    if extra:
        raise ProposalError(f"not a secret of this proposal: {', '.join(extra)}", code="unknown_field")
    missing = [k for k, f in asked.items() if f.get("required") and not f.get("has_value") and k not in given]
    if missing:
        raise ProposalError(f"fill in: {', '.join(missing)}", code="missing_secret")
    return given


def prepare(proposal: Dict[str, Any], *, values: Optional[Dict[str, Any]] = None,
            secrets: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The proposal as the person settled it, validated again from scratch.

    Returns ``{"proposal", "values", "secrets"}`` for :func:`perform`, or
    raises :class:`ProposalError` before anything changes. The rebuilt
    proposal is what the role check reads (``required_role``): the person's
    edits may ask for more than the agent's did (a new secret a watcher
    names).
    """
    kind = proposal.get("kind")
    flat = _merged(proposal, values or {})
    fresh = build(kind, proposal.get("target") or "", flat, workspace=proposal.get("workspace"),
                  from_agent=False)
    given = _secrets_for(fresh, secrets or {})
    settled = {f["key"]: f.get("value") for f in fresh["fields"] if not f.get("secret")}
    return {"proposal": fresh, "values": settled, "secrets": given}


async def perform(prepared: Dict[str, Any], *, principal: Any = None) -> Dict[str, Any]:
    """Make the change :func:`prepare` validated.

    Returns ``{"ok", "summary", "test", "href", "proposal"}``: ``ok`` is the
    test's verdict when there is one (the change itself is made either way),
    ``summary`` the one line the agent reads back.
    """
    fresh = prepared["proposal"]
    performer = {"connector": _apply_connector, "channel": _apply_channel,
                 "mcp_server": _apply_mcp, "database": _apply_database,
                 "watcher": _apply_watcher, "secret": _apply_secret}[fresh["kind"]]
    outcome = await performer(fresh, dict(prepared["values"]), dict(prepared["secrets"]), principal)
    outcome["proposal"] = fresh
    return outcome


async def apply(proposal: Dict[str, Any], *, values: Optional[Dict[str, Any]] = None,
                secrets: Optional[Dict[str, Any]] = None, principal: Any = None) -> Dict[str, Any]:
    """:func:`prepare` then :func:`perform`, for a caller with no role to check."""
    return await perform(prepare(proposal, values=values, secrets=secrets), principal=principal)


def _test_line(test: Optional[Dict[str, Any]]) -> str:
    if not test:
        return ""
    if test.get("ok"):
        who = test.get("identity")
        if isinstance(who, dict):
            who = who.get("name") or who.get("email") or who.get("login") or ""
        return f" Test passed{f' as {who}' if who else ''}."
    return f" Test failed: {str(test.get('error') or 'unknown error')[:200]}"


async def _apply_connector(p, values, secrets, principal) -> Dict[str, Any]:
    from common.session_broker import notify_change
    from connectors import credentials

    spec = credentials.get(p["target"])
    ws = p.get("workspace") or "default"
    changes = {k: v for k, v in values.items() if v is not None}
    changes.update(secrets)
    await asyncio.to_thread(spec.store.for_workspace(ws).set_config, changes)
    notify_change(f"connector_{spec.name}", workspace=ws)
    from connectors.channels.store import in_workspace
    test = None
    if spec.test is not None and in_workspace(ws, spec.is_configured):
        try:
            test = await asyncio.to_thread(in_workspace, ws, spec.test)
        except Exception as exc:  # noqa: BLE001 - reported back, the config is saved
            test = {"ok": False, "error": str(exc)[:300]}
    href = _SIGN_IN_HREF.get(spec.name)
    where = "for every workspace" if ws == "default" else f"for workspace {ws}"
    summary = f"Connector {spec.name} saved {where}." + _test_line(test)
    if href:
        summary += f" The person still has to sign in at {href}."
    return {"ok": bool(test.get("ok")) if test else True, "summary": summary, "test": test, "href": href}


async def _apply_channel(p, values, secrets, principal) -> Dict[str, Any]:
    from common.session_broker import notify_change
    from connectors.channels import registry as channels

    spec = channels.get(p["target"])
    ws = p.get("workspace") or "default"
    store = spec.store.for_workspace(ws)
    enabled = _as_bool(values.pop("enabled", True), True)
    allowed = _as_list(values.pop("allowed", []))
    changes = {k: v for k, v in values.items() if v is not None}
    changes.update(secrets)
    await asyncio.to_thread(store.set_config, changes)
    await asyncio.to_thread(store.set_enabled, enabled)
    await asyncio.to_thread(store.set_allowed, allowed)
    # The loop serving this workspace's bot picks the change up now
    # (connectors/channels/registry.py ``resync``).
    service = await channels.resync(spec.name, ws)
    notify_change(f"channel_{spec.name}", workspace=ws)
    test = None
    if service is not None and store.is_configured(*spec.service.required_fields):
        test = await service.test()
    state = "enabled" if enabled else "saved, not enabled"
    summary = (f"Channel {spec.name} {state}, {len(allowed)} chat(s) on the allowlist."
               + _test_line(test))
    return {"ok": bool(test.get("ok")) if test else True, "summary": summary, "test": test,
            "href": f"/connectors?tab={spec.name}"}


async def _apply_mcp(p, values, secrets, principal) -> Dict[str, Any]:
    from mcp_client import store as mcp_store
    from mcp_client.client import discover, refresh

    ws = p["workspace"]
    record: Dict[str, Any] = {"headers": {}, "env": {}, "capabilities": {}}
    for key, value in values.items():
        head, _, tail = key.partition(".")
        if tail and head in record:
            record[head][tail] = _as_bool(value, True) if head == "capabilities" else str(value or "")
        else:
            record[key] = value
    for key, value in secrets.items():
        head, _, tail = key.partition(".")
        record[head][tail] = value
    record["args"] = _as_list(record.get("args")) if not isinstance(record.get("args"), list) else record["args"]
    record["tool_allowlist"] = _as_list(record.get("tool_allowlist"))
    record["enabled"] = True
    try:
        saved = await asyncio.to_thread(mcp_store.create_server, ws, record)
    except (ValueError, FileNotFoundError) as exc:
        raise ProposalError(str(exc), code="bad_request") from exc
    try:
        tools = await asyncio.to_thread(discover, saved)
        mcp_store.record_status(ws, saved["id"], error="", tool_names=[t["name"] for t in tools])
        test = {"ok": True, "count": len(tools), "tools": [t["name"] for t in tools][:50]}
    except Exception as exc:  # noqa: BLE001 - the server is attached; the failure is the test's answer
        message = str(exc) or exc.__class__.__name__
        mcp_store.record_status(ws, saved["id"], error=message)
        test = {"ok": False, "error": message[:300]}
    refresh(ws, saved["id"])
    summary = f"MCP server {saved['id']} attached to workspace {ws}."
    summary += (f" It offers {test['count']} tool(s): {', '.join(test['tools'][:10])}."
                f" Agents get them once their tool list names them."
                if test.get("ok") else _test_line(test))
    return {"ok": bool(test.get("ok")), "summary": summary, "test": test, "href": "/mcp"}


async def _apply_database(p, values, secrets, principal) -> Dict[str, Any]:
    from common.session_broker import notify_change
    from connectors.databases import drivers, store as db_store

    dsn = secrets["dsn"]
    if not db_store.kind_matches_dsn(values["kind"], dsn):
        raise ProposalError(f"the connection string does not look like a {values['kind']} one",
                            code="bad_dsn")
    record = await asyncio.to_thread(
        db_store.create_connection, workspace=p["workspace"], name=values["name"], kind=values["kind"],
        dsn=dsn, allowed_schemas=_as_list(values.get("allowed_schemas")),
        row_limit=int(values["row_limit"]) if values.get("row_limit") not in (None, "") else None,
        timeout_seconds=int(values["timeout_seconds"]) if values.get("timeout_seconds") not in (None, "") else None)
    notify_change("databases")
    try:
        test = await asyncio.to_thread(drivers.test_connection, record)
    except Exception as exc:  # noqa: BLE001 - the connection is saved; the failure is the test's answer
        test = {"ok": False, "error": str(exc)[:300]}
    summary = (f"Database connection '{record['name']}' ({record['kind']}) added to workspace "
               f"{p['workspace']}." + _test_line(test))
    return {"ok": bool(test.get("ok")), "summary": summary, "test": test,
            "href": "/connectors?tab=databases"}


def _set_secret(workspace: str, name: str, value: str, principal: Any, *,
                allowed_hosts: Optional[List[str]] = None, agent_id: Optional[str] = None) -> Dict[str, Any]:
    from common import audit
    from common import secrets as secret_store

    try:
        row = secret_store.set_secret(workspace, name, value, agent_id=agent_id or None,
                                      created_by=getattr(principal, "id", None),
                                      allowed_hosts=allowed_hosts)
    except secret_store.SecretsError as exc:
        raise ProposalError(str(exc), code="bad_secret") from exc
    audit.record("secret.set", principal=principal, object_type="secret", object_id=name,
                 workspace=workspace, details={"name": name, "agent_id": agent_id or "",
                                               "user_id": "", "via": TOOL,
                                               "allowed_hosts": row.get("allowed_hosts")})
    return row


def _host_of(value: Any) -> List[str]:
    from urllib.parse import urlparse
    host = urlparse(str(value or "")).hostname if "://" in str(value or "") else str(value or "").strip()
    return [host] if host else []


async def _apply_watcher(p, values, secrets, principal) -> Dict[str, Any]:
    from watchers import service

    ws = p["workspace"]
    kind = values["kind"]
    # The secrets the watcher names, bound to the host it reads.
    hosts = _host_of(values.get("host") if kind == "imap" else values.get("url"))
    for key, value in secrets.items():
        if key.startswith(_SECRET_PREFIX):
            await asyncio.to_thread(_set_secret, ws, key[len(_SECRET_PREFIX):], value, principal,
                                    allowed_hosts=hosts or None)
    config = {k: v for k, v in values.items() if k not in ("name", "kind", "interval_seconds")}
    try:
        watcher = await asyncio.to_thread(
            service.create, ws, {"name": values["name"], "kind": kind, "config": config,
                                 "interval_seconds": values.get("interval_seconds")},
            created_by=getattr(principal, "username", None))
    except service.WatcherError as exc:
        raise ProposalError(str(exc), code="bad_watcher") from exc
    try:
        test = await asyncio.to_thread(service.probe_once, watcher.id, dry_run=True)
    except Exception as exc:  # noqa: BLE001 - the watcher is saved; the failure is the test's answer
        test = {"ok": False, "error": str(exc)[:300]}
    summary = (f"Watcher '{watcher.name}' ({kind}) created in workspace {ws}." + _test_line(test)
               + " It wakes an agent once a Pulse rule on that agent listens to it.")
    return {"ok": bool(test.get("ok")), "summary": summary, "test": test, "href": "/watchers"}


async def _apply_secret(p, values, secrets, principal) -> Dict[str, Any]:
    ws = p["workspace"]
    hosts = _as_list(values.get("allowed_hosts"))
    row = await asyncio.to_thread(_set_secret, ws, p["target"], secrets["value"], principal,
                                  allowed_hosts=hosts or None, agent_id=values.get("agent_id") or None)
    scope = f" for agent {row.get('agent_id')}" if row.get("agent_id") else ""
    bound = f", sent only to {', '.join(hosts)}" if hosts else ""
    return {"ok": True, "summary": f"Secret {p['target']} stored in workspace {ws}{scope}{bound}. "
                                   "An agent receives it once its secrets list names it.",
            "test": None, "href": f"/workspaces/{ws}?tab=secrets"}


# ── what the agent may propose ───────────────────────────────────────────────

def options(kind: Optional[str] = None, *, workspace: Optional[str] = None) -> Dict[str, Any]:
    """The kinds, their targets and fields, and what exists already.

    Names and flags only: whether a connector is configured, which MCP
    servers, database connections, watchers and secrets a workspace has.
    Never a value, and no URL or command of an existing item either.
    """
    wanted = [str(kind).strip().lower()] if kind else list(KINDS)
    bad = [k for k in wanted if k not in KINDS]
    if bad:
        raise ProposalError(f"kind must be one of {', '.join(KINDS)}", code="bad_kind")
    out: Dict[str, Any] = {}
    for k in wanted:
        try:
            out[k] = _options_of(k, workspace)
        except Exception as exc:  # noqa: BLE001 - one unreadable kind must not hide the rest
            log.debug("proposals: options for %s failed", k, exc_info=True)
            out[k] = {"error": str(exc)[:200]}
    return out


def _field_brief(f: Any) -> Dict[str, Any]:
    return {"key": f.key, "secret": bool(f.secret), "required": bool(f.required),
            **({"options": list(f.options)} if f.options else {}),
            **({"placeholder": f.placeholder} if f.placeholder else {})}


def _options_of(kind: str, workspace: Optional[str]) -> Dict[str, Any]:
    if kind == "connector":
        from connectors import credentials
        return {"scope": _scope_of(workspace), "targets": [
            {"target": s.name, "configured": bool(_safe(s.is_configured, False)),
             "defined_here": bool(_safe(lambda s=s: s.store.defines(workspace), False)),
             "fields": [_field_brief(f) for f in s.fields if f.kind != "hidden"]}
            for s in credentials.all_specs() if s.name not in _NOT_A_CONNECTOR]}
    if kind == "channel":
        from connectors.channels import registry as channels
        return {"scope": _scope_of(workspace), "targets": [
            {"target": c.name,
             "defined_here": bool(_safe(lambda c=c: c.store.defines(workspace), False)),
             "configured": bool(_safe(lambda c=c: c.store.is_configured(*c.service.required_fields), False)),
             "enabled": bool(_safe(c.store.is_enabled, False)),
             "fields": [_field_brief(f) for f in c.fields if f.kind != "hidden"]
             + [{"key": "enabled", "secret": False, "required": False},
                {"key": "allowed", "secret": False, "required": False}]}
            for c in channels.all_channels()]}
    ws = str(workspace or "").strip()
    if kind == "mcp_server":
        from mcp_client import store as mcp_store
        return {"scope": "workspace", "workspace": ws or None,
                "transports": list(mcp_store.TRANSPORTS),
                "fields": ["id", "name", "description", "transport", "command", "args", "url",
                           "headers", "env", "tool_allowlist", "approval"],
                "secret_rule": "header and env names containing TOKEN, KEY, SECRET, PASSWORD or "
                               "AUTHORIZATION are secrets: give the name with an empty value",
                "existing": [r.get("id") for r in _safe(lambda: mcp_store.list_servers(ws), [])] if ws else []}
    if kind == "database":
        from connectors.databases import store as db_store
        return {"scope": "workspace", "workspace": ws or None, "kinds": list(db_store.KINDS),
                "fields": ["name", "kind", "allowed_schemas", "row_limit", "timeout_seconds"],
                "secret_fields": ["dsn"],
                "existing": [c.get("name") for c in _safe(lambda: db_store.list_connections(ws), [])] if ws else []}
    if kind == "watcher":
        from watchers import kinds as watcher_kinds
        from watchers import service as watcher_service
        return {"scope": "workspace", "workspace": ws or None,
                "kinds": {k: [{"key": s["name"], "type": s["type"], "required": s["required"]}
                              for s in v] for k, v in watcher_kinds.CONFIG_FIELDS.items()},
                "secret_rule": "a field of type secret holds the NAME of a workspace secret; when it "
                               "does not exist yet, the card asks the person for its value",
                "existing": [w.name for w in _safe(lambda: watcher_service.list_watchers(ws), [])] if ws else []}
    # secret
    return {"scope": "workspace", "workspace": ws or None,
            "fields": ["allowed_hosts", "agent_id"], "secret_fields": ["value"],
            "existing": sorted(_workspace_secret_names(ws)) if ws else []}


__all__ = ["HUB_KINDS", "KINDS", "MCP_CAPABILITIES", "ProposalError", "TOOL", "apply", "build",
           "options", "perform", "prepare", "required_role"]
