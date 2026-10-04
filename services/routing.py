"""
Which service, and which replica, answers a message.

Two entry points, one rule for the service:

- :func:`service_for` picks the service a turn of ``agent_id`` in
  ``workspace`` belongs to: the agent's own active service there, else the
  workspace's runner (created on first use, services/store.py ensure_runner).
  A flow's or a team's turn has no agent and always goes to the runner.
- :func:`dispatch_turn` and :func:`dispatch_message` then choose the replica
  (services/replicas.py pick) and write into its mailbox: a ``turn`` carrying
  the whole chat request, or a plain message the replica answers with the
  service conversation's history.

Everything here is synchronous database and process work; async callers run
it in a thread.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from instances import inbox
from services import replicas, store

log = logging.getLogger(__name__)

# ``kind`` of a turn: which chat pipeline the replica runs.
TURN_AGENT = "agent"
TURN_FLOW = "flow"
TURN_TEAM = "team"


def default_environment(workspace: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``(id, name)`` of the environment a turn in the workspace runs in
    without one being named: the workspace's default, else none."""
    try:
        from environments import service as env_service
        env = env_service.default_for(workspace)
    except Exception:  # noqa: BLE001 - no environments: turns run without one
        log.debug("default environment lookup failed for %s", workspace, exc_info=True)
        return None, None
    if env is None:
        return None, None
    return str(env.id), str(env.name)


def service_for(workspace: Optional[str], agent_id: Optional[str]) -> Dict[str, Any]:
    env_id, env_name = default_environment(workspace)
    if agent_id:
        own = store.find_agent_service(workspace, agent_id, env_id)
        if own is not None:
            return own
    return store.ensure_runner(workspace, env_id, env_name)


def turn_payload(request: Any, kind: str, *, user_id: Optional[str] = None,
                 key_id: Optional[str] = None) -> Dict[str, Any]:
    """What a ``turn`` message carries: the request as JSON, which pipeline
    runs it, and who it acts as (the run is stamped and charged to them)."""
    dump = request.model_dump(mode="json") if hasattr(request, "model_dump") else dict(request)
    payload = {"kind": kind, "request": dump, "user_id": user_id, "key_id": key_id}
    # The end user of a widget or channel turn (common/secrets.py), so the
    # replica acts on their consent token (docs/consent.md), not the hub's.
    from common.secrets import current_end_user
    if current_end_user():
        payload["end_user"] = current_end_user()
    return payload


def dispatch_turn(request: Any, kind: str, *, user_id: Optional[str] = None,
                  key_id: Optional[str] = None,
                  service: Optional[Dict[str, Any]] = None,
                  replica: Optional[Dict[str, Any]] = None,
                  mailbox_conversation_id: Optional[str] = None,
                  ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], str]:
    """Write a chat turn into a replica's mailbox. Returns
    ``(service, replica, msg_id)``. ``service`` and ``replica`` may be chosen
    beforehand (:func:`choose`), so a caller can subscribe to the replica's
    channel before the message exists; a replica given without a service (a
    standalone runner instance written to from its page) gets no cap or pin.
    ``mailbox_conversation_id`` is the conversation the message is filed under
    on the replica when it is not the request's own (a public caller's
    conversation, whose chat conversation id is namespaced)."""
    if replica is None:
        service, replica = choose(request.workspace,
                                  request.agent_id if kind == TURN_AGENT else None,
                                  conversation_id=request.conversation_id,
                                  service=service)
    payload = turn_payload(request, kind, user_id=user_id, key_id=key_id)
    payload["budget_usd"] = (service or {}).get("budget_usd")
    payload["agent_version"] = (service or {}).get("agent_version")
    msg_id = inbox.enqueue(
        str(replica["instance_id"]), str(getattr(request, "message", "") or "")[:4000],
        origin=str(getattr(request, "source", None) or "chat"),
        conversation_id=(mailbox_conversation_id if mailbox_conversation_id is not None
                         else getattr(request, "conversation_id", None)),
        kind=inbox.KIND_TURN, payload=payload,
    )
    return service, replica, msg_id


def choose(workspace: Optional[str], agent_id: Optional[str], *,
           conversation_id: Optional[str] = None,
           service: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The service and the replica a message goes to (a replica may be
    started here). Raises services.replicas.ServiceUnavailable."""
    svc = service or service_for(workspace, agent_id)
    replica = replicas.pick(svc, conversation_id=conversation_id, allow_start=True)
    return svc, replica


def dispatch_message(service: Optional[Dict[str, Any]], body: str, *,
                     conversation_id: Optional[str], origin: str = "web",
                     replica: Optional[Dict[str, Any]] = None,
                     agent_id: Optional[str] = None) -> Tuple[Dict[str, Any], str]:
    """A plain message to a service (its page, its public address): written
    into the chosen replica's mailbox, answered with the service
    conversation's history. Returns ``(replica, msg_id)``.

    A runner (a service with no agent, or a standalone runner replica given
    with ``service`` None) answers for the agent the message names in
    ``agent_id``: the message becomes a chat turn of that agent with the
    conversation's earlier turns as history, filed under the same
    conversation, so its reply is read the same way. Raises ValueError when a
    runner is given no agent.
    """
    if replica is None:
        if service is None:
            raise ValueError("a replica or a service is needed")
        replica = replicas.pick(service, conversation_id=conversation_id, allow_start=True)
    if service is not None and service.get("agent_id"):
        msg_id = inbox.enqueue(str(replica["instance_id"]), body, origin=origin,
                               conversation_id=conversation_id)
        return replica, msg_id
    agent = (agent_id or "").strip()
    if not agent:
        raise ValueError("this is a runner: name the agent that answers in agent_id")
    from chat.models import ChatRequest
    from instances.history import build_instance_history

    cid = inbox.normalize_conversation(conversation_id)
    public_cid = inbox.public_conversation(cid)
    service_id = str((service or {}).get("service_id") or replica.get("service_id") or "")
    if service_id:
        history = build_instance_history(None, service_id=service_id, conversation_id=cid)
        owner = f"svc:{service_id}"
        title = f"{(service or {}).get('name') or 'runner'} · {public_cid}"
    else:
        history = build_instance_history(str(replica["instance_id"]), conversation_id=cid)
        owner = f"inst:{replica['instance_id']}"
        title = f"{replica.get('label') or replica['instance_id']} · {public_cid}"
    request = ChatRequest(
        agent_id=agent, message=body, workspace=(service or replica).get("workspace"),
        conversation_id=f"{owner}:{public_cid}", conversation_title=title,
        history=history, source=origin,
    )
    _svc, _rep, msg_id = dispatch_turn(request, TURN_AGENT, service=service, replica=replica,
                                       mailbox_conversation_id=conversation_id)
    return replica, msg_id


def agent_for_runner(agent_id: Optional[str], workspace: Optional[str]) -> Any:
    """The agent a runner is asked to answer for: it must exist and be usable
    in the workspace (the chat's own visibility rule). Raises ValueError with
    the reason otherwise."""
    from agents import registry as agent_registry

    agent = (agent_id or "").strip()
    if not agent:
        raise ValueError("this is a runner: name the agent that answers in agent_id")
    spec = agent_registry.get_agent(agent)
    if spec is None:
        raise ValueError(f"Agent '{agent}' not found")
    try:
        from widgets.agents import usable_in
        if not usable_in(spec, store.normalize_workspace(workspace)):
            raise ValueError(f"Agent '{agent}' is not available in workspace '{workspace or 'default'}'")
    except ImportError:
        pass
    return spec
