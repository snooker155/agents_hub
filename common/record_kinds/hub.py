"""Record kinds: hub (see common/record_kinds/__init__.py).

Hub-wide routes: accounts and keys, the model catalog and runtime, the
cluster, linked hubs and nodes, sync, settings, kits, the catalog of a linked
hub, the run relays, the event stream and the workspaces themselves. Most of
them name no workspace record and are exempt, each with the code that checks
them instead. The ones that do name one are resolved: a context reference,
a terminal's run or replica, a Blender engine's view, a Telegram chat
binding, a chat platform installation, and the local copy behind a linked
hub's catalog item (an agent, flow, team, loop or skill, through the kinds
core.py registers).
"""
from __future__ import annotations

from typing import Optional, Tuple

from common.record_access import Record
from common.record_kinds import in_workspace

#: The kinds core.py registers for what the linked hub's catalog installs.
_CATALOG_KINDS = {
    "agents": "agent_record", "flows": "flow_record", "teams": "team_record",
    "loops": "loop_record", "skills": "skill_record",
}

#: Context reference kinds (chat/references.py ``KINDS``) that core.py
#: resolves the way their own routes do; the others go through the rendered
#: reference's workspace.
_CONTEXT_KINDS = {
    "agent": "agent_record", "flow": "flow_record", "team": "team_record", "loop": "loop_record",
}


def _catalog(params: dict) -> Optional[Tuple[str, str]]:
    """``/{kind}/{local_id}/diff|update`` name the local copy of an installed
    item; ``/{kind}/{item_id}`` names an item on the linked hub, not here
    (plan and install check editor in their target workspace themselves)."""
    local_id = str(params.get("local_id") or "")
    kind = _CATALOG_KINDS.get(str(params.get("kind") or ""))
    return (kind, local_id) if kind and local_id else None


def _context(params: dict) -> Optional[Tuple[str, str]]:
    """``/{kind}/{entity_id}/preview`` names one record; ``/{kind}`` lists."""
    kind = str(params.get("kind") or "")
    entity_id = str(params.get("entity_id") or "")
    if not kind or not entity_id:
        return None
    if kind in _CONTEXT_KINDS:
        return _CONTEXT_KINDS[kind], entity_id
    return "context_entity", f"{kind}:{entity_id}"


def _terminal(params: dict) -> Optional[Tuple[str, str]]:
    kind = str(params.get("kind") or "")
    target_id = str(params.get("target_id") or "")
    if kind not in ("run", "replica") or not target_id:
        return None
    return "terminal_target", f"{kind}:{target_id}"


def _distribution(params: dict) -> Optional[Tuple[str, str]]:
    """``/{channel}/installs/{org_id}`` names one installation; the list
    and the add name none."""
    channel = str(params.get("channel") or "")
    org_id = str(params.get("org_id") or "")
    return ("channel_install", f"{channel}:{org_id}") if channel and org_id else None


def _context_entity(ref: str) -> Optional[Record]:
    """A context reference's record, by the workspace its rendering names
    (chat/references.py ``render_entity``, what routes/context_refs.py checks)."""
    kind, _, entity_id = ref.partition(":")
    try:
        from chat import references
        rendered = references.render_entity(kind, entity_id)
    except Exception:  # noqa: BLE001 - an unreadable reference is the route's 404 to give
        return None
    return in_workspace(rendered.get("workspace")) if rendered else None


def _terminal_target(ref: str) -> Optional[Record]:
    """The run or service replica a terminal opens on, by the workspace
    common/terminal.py ``authorize`` checks: the run's, or the replica's
    (its service's when it names none). Found whatever the run's state, so a
    finished run's ticket is refused by the route, not let through here."""
    kind, _, target_id = ref.partition(":")
    try:
        if kind == "run":
            from managers.run_manager import get_run_by_id
            run = get_run_by_id(target_id)
            if run is not None:
                return in_workspace(run.get("workspace"))
            from common import entity_runs
            run = entity_runs.get(target_id)
            if run is None:
                return None
            if str(run.get("workspace") or "").strip():
                return in_workspace(run.get("workspace"))
            from common.record_kinds import resolve
            by_kind = {"flow": "flow_run_record", "team": "team_run_record", "loop": "loop_run_record"}
            found = resolve(by_kind[run["kind"]], target_id) if run.get("kind") in by_kind else None
            return found or in_workspace(None)
        from instances import store as istore
        replica = istore.get(target_id)
        if replica is None:
            return None
        workspace = replica.get("workspace")
        if not str(workspace or "").strip() and replica.get("service_id"):
            from services import store as sstore
            workspace = (sstore.get(replica.get("service_id")) or {}).get("workspace")
        return in_workspace(workspace)
    except Exception:  # noqa: BLE001 - an unreadable target is the route's 4xx to give
        return None


def _blender_engine(key: str) -> Optional[Record]:
    """A Blender engine is keyed by the scene view it renders
    (tools/geometry.py ``pool.acquire(vid)``): that view's workspace. An
    engine under a key no view has passes."""
    try:
        from views import store
        row = store.view_index_row(key)
    except Exception:  # noqa: BLE001 - an unreadable view index is the route's 404 to give
        return None
    return in_workspace(row.get("workspace")) if row else None


def _telegram_binding(chat_id: str) -> Optional[Record]:
    """A chat bound to the default workspace's bot, the one that serves
    every workspace: the workspace the binding names. A binding of a
    workspace's own bot lives in that bot's store, which the route picks by
    ``?workspace=`` (checked by the middleware as the named workspace) and
    refuses outside it (routes/telegram.py ``_visible``)."""
    try:
        from connectors.telegram import telegram_store
        binding = telegram_store.get_binding(int(chat_id))
    except Exception:  # noqa: BLE001 - an unreadable or non-numeric binding is the route's 4xx to give
        return None
    return in_workspace(binding.get("workspace")) if binding else None


def _channel_install(ref: str) -> Optional[Record]:
    """An organisation that installed the default workspace's Slack or Teams
    bot: the workspace it was approved into (still pending: the default's,
    as routes/distribution.py ``_visible_installs`` lists it). The route
    also requires the owner role of the bot's workspace for every write."""
    channel, _, org_id = ref.partition(":")
    try:
        from connectors.channels import registry
        svc = registry.service_for(channel, "default", create=False)
        install = svc.store.get_install(org_id) if svc is not None else None
    except Exception:  # noqa: BLE001 - an unreadable store is the route's 4xx to give
        return None
    return in_workspace(install.get("workspace")) if install else None


ROUTES: dict = {
    ("/api/catalog/", "kind"): _catalog,
    ("/api/context/", "kind"): _context,
    ("/api/terminal/", "kind"): _terminal,
    ("/api/distribution/", "channel"): _distribution,
    ("/api/blender/daemons/", "key"): "blender_engine",
    ("/api/telegram/bindings/", "chat_id"): "telegram_binding",
}

RESOLVERS: dict = {
    "context_entity": _context_entity,
    "terminal_target": _terminal_target,
    "blender_engine": _blender_engine,
    "telegram_binding": _telegram_binding,
    "channel_install": _channel_install,
}

_MODELS = "the hub's model catalog and runtime, not workspace data; writes are admin only (common.auth.admin_only)"
_NODE_LINK = ("open path for a linked node (common.auth.is_open_path, /api/node-link is self-authenticating): "
              "the route checks the node's own token")
_RELAY = "a run's relay (common.auth.is_relay_route): a run token or the hub's credential, never a person"
_OPERATOR = "the cluster page, admin only (routes/cluster.py routers depend on _require_operator)"

EXEMPT: dict = {
    # Accounts, keys and sessions: a person's, not a workspace's.
    ("/api/auth/group-mappings/", "mapping_id"): "identity provider group mappings, admin only (routes/groups.py _admin)",
    ("/api/auth/groups/", "group_id"): "account groups, admin only (routes/groups.py _admin)",
    ("/api/auth/keys/", "key_id"): "the caller's own API key: routes/account.py passes user_id=principal.id",
    ("/api/auth/sessions/", "session_id"): "the caller's own session: identity.revoke_session(principal.id, ...)",
    ("/api/auth/users/", "user_id"): "an account, not workspace data; admin only (require_role(admin=True) in routes/auth.py and routes/account.py)",
    # The model catalog and the hub's model runtime.
    ("/api/models/discover/", "provider"): _MODELS,
    ("/api/models/local/jobs/", "job_id"): _MODELS,
    ("/api/models/local/ollama/", "name"): _MODELS,
    ("/api/models/local/runtime/engines/", "engine"): _MODELS,
    ("/api/models/local/runtime/jobs/", "job_id"): _MODELS,
    ("/api/models/local/runtime/models/", "file"): _MODELS,
    ("/api/models/local/runtime/voices/", "name"):
        "a person's own recorded voice: routes/local_models.py _voice checks its owner (voice_visible, voice_editable)",
    ("/api/settings/custom-backends/", "backend_id"):
        "a hub-wide provider backend; its one route is a DELETE, admin only (common.auth.admin_only)",
    # The cluster.
    ("/api/cluster/members/", "member_id"): _OPERATOR,
    ("/api/cluster/workers/", "member_id"): _OPERATOR,
    ("/api/deployment/members/", "member_id"): _OPERATOR,
    # Linked hubs, nodes and sync.
    ("/api/nodes/", "node_id"): "a person's own node: routes/hublink.py _own_or_admin checks its owner",
    ("/api/node-link/", "node_id"): _NODE_LINK,
    ("/api/node-link/jobs/", "job_id"): _NODE_LINK,
    ("/api/node-link/pool/v1/", "path"): _NODE_LINK,
    ("/api/hublink/sync/conflicts/", "conflict_id"): "this hub's sync client, admin only (routes/sync.py _admin)",
    ("/api/hublink/sync/workspaces/", "workspace"): "this hub's sync client, admin only (routes/sync.py _admin)",
    ("/api/sync/keys/", "key_id"): "the caller's own vault key, kept per owner (routes/sync.py _owner)",
    ("/api/sync/spaces/", "space"):
        "the space is a workspace's name: routes/sync.py _space_access requires viewer or editor in it",
    # Built in, open, or a relay.
    ("/api/kits/", "kit_id"): "a built-in kit (kits/), the same for everyone; install checks editor in its target workspace",
    ("/api/docs/", "doc_id"): "the product documentation, the same for everyone",
    ("/api/external/", "token"): "open path (common.auth.is_open_path): the token in the path is the credential",
    ("/api/run-state/llm/", "scheme"): _RELAY,
    ("/api/run-state/runs/", "run_id"): _RELAY,
    ("/api/run-state/tasks/", "task_id"): _RELAY,
    ("/api/run-state/tool-approvals/", "approval_id"): _RELAY,
    ("/api/run-state/tool-host/", "name"): _RELAY,
    ("/api/run-state/wake/", "instance_id"): _RELAY,
    ("/api/stream/", "client_id"):
        "a browser's event stream connection, not a workspace record (the channels it adds come in the body)",
    # The workspace itself.
    ("/api/workspaces/", "name"):
        "the path names the workspace: common.auth.workspace_from_request reads it and the middleware checks membership there",
}
