"""
Node-type classification and entity-node execution for flow runs.

Every node in a flow graph references a registered *flow entity* (agent,
processor, condition, transform, ...). This module turns a raw node dict into a
**FlowEntity** object — a thin, behavioural wrapper around the catalog's
``FlowEntitySpec`` — and runs it through one uniform method::

    entity = FlowEntity.for_node(node)        # classify + resolve from registry
    result = entity.run(node, state, ctx)     # -> DispatchResult

Agent nodes are still executed in-process by the flow runner (``runtime.flow_run``);
their ``FlowEntity`` subclass therefore reports ``runs_in_process`` and leaves
``run()`` to the runner. Entity nodes (processor, condition, transform, ...)
execute inline here: their callable honours the uniform ``run(state, config,
ctx)`` contract, with results applied to ``FlowState`` under the node's declared
``output`` keys.

Both paths report results through the uniform ``DispatchResult``.

A node references an entity by ``entity_id`` (preferred) or, for legacy/agent
nodes, by ``agent_id``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type

from flow import registry as flow_registry
from flow.state import FlowState, NodeResult, RunContext


# -------------------- helpers --------------------

def node_field(node: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Read a field from a node, checking both top-level and node['data']."""
    data = node.get("data", {}) if isinstance(node.get("data"), dict) else {}
    if key in node:
        return node[key]
    return data.get(key, default)


@dataclass
class DispatchResult:
    ok: bool
    text: str = ""             # human-readable summary (for logs / downstream)
    written: Dict[str, Any] = None  # state keys actually written
    goto: Optional[List[str]] = None
    error: str = ""
    # Agent-node extras (None/empty for entity nodes):
    output: str = ""           # raw agent output (distinct from the log `text`)
    run_id: str = ""           # per-node run record id, when one was opened
    duration_ms: int = 0

    def __post_init__(self):
        if self.written is None:
            self.written = {}


# -------------------- entity class system --------------------

# Maps a spec ``category`` to the FlowEntity subclass that handles it. Populated
# by the ``@register_category`` decorator below; ``FlowEntity.for_node`` consults
# it to pick the right behaviour for a node.
_CATEGORY_CLASSES: Dict[str, Type["FlowEntity"]] = {}


def register_category(*categories: str):
    """Class decorator: bind a FlowEntity subclass to one or more categories."""
    def _wrap(cls: Type["FlowEntity"]) -> Type["FlowEntity"]:
        for c in categories:
            _CATEGORY_CLASSES[c] = cls
        return cls
    return _wrap


class FlowEntity:
    """Behavioural wrapper around a ``FlowEntitySpec``.

    A ``FlowEntity`` is the runtime face of a registry spec: it knows how to
    classify a node and how to execute it. Categories with distinct execution
    semantics subclass this and override ``run`` (and ``runs_in_process`` when
    the flow runner, not this module, performs execution).
    """

    #: True when the flow runner executes this node in-process (agents) rather
    #: than via ``run()`` here.
    runs_in_process: bool = False

    def __init__(self, spec: flow_registry.FlowEntitySpec):
        self.spec = spec

    # -- identity / convenience proxies to the underlying spec --

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def category(self) -> str:
        return self.spec.category

    # -- classification / resolution --

    @staticmethod
    def resolve_spec(node: Dict[str, Any]) -> Optional[flow_registry.FlowEntitySpec]:
        """Return the FlowEntitySpec a node references, or None.

        Looks up ``entity_id`` first, then falls back to ``agent_id`` (so a plain
        agent node still resolves to its federated entity).
        """
        entity_id = node_field(node, "entity_id")
        if entity_id:
            return flow_registry.get_entity(str(entity_id))
        agent_id = node_field(node, "agent_id")
        if agent_id:
            return flow_registry.get_entity(str(agent_id))
        return None

    @classmethod
    def for_node(cls, node: Dict[str, Any]) -> Optional["FlowEntity"]:
        """Resolve a node to the FlowEntity subclass that should run it.

        Returns ``None`` when the node references no entity that exists in the
        registry. There is no fallback synthesis: an ``entity``/``agent_id`` that
        is not a registered entity is an error the caller must surface (see
        ``flow.validate.resolve_entities`` and the load-time check in
        ``flow.store``).
        """
        spec = cls.resolve_spec(node)
        if spec is None:
            return None
        impl = _CATEGORY_CLASSES.get(spec.category, EntityNode)
        return impl(spec)

    # -- execution (overridden per category) --

    def run(self, node: Dict[str, Any], state: FlowState, ctx: RunContext) -> DispatchResult:
        raise NotImplementedError(
            f"entity category '{self.category}' has no inline run()"
        )


@register_category("agent")
class AgentEntity(FlowEntity):
    """An agent node. Executed in-process by the flow runner, not here."""

    runs_in_process = True

    def run(self, node: Dict[str, Any], state: FlowState, ctx: RunContext) -> DispatchResult:
        # The runner (``runtime.flow_run``) owns agent execution; reaching here means a
        # caller dispatched an agent through the inline path by mistake.
        return DispatchResult(
            ok=False,
            error=f"agent entity '{self.id}' must be run in-process by the flow runner",
        )


class EntityNode(FlowEntity):
    """Default handler for inline (callable) entities: processor / condition /
    transform / any future category whose entrypoint is a ``run(state, config,
    ctx)`` callable.

    This carries the uniform execution method that used to live in the
    standalone ``run_entity_node`` function.
    """

    def _resolve_io(self, node: Dict[str, Any]) -> tuple[List[str], List[str], Dict[str, Any]]:
        """Resolve the node's effective input/output keys and merged config.

        The node's declared ``input``/``output`` key lists come from the node
        data (``input``/``inputs`` and ``output``/``outputs``); when absent they
        default to the entity spec's declared contract. Config merges the
        spec's ``config_schema`` defaults under any explicit node config.
        """
        spec = self.spec
        inputs = node_field(node, "input") or node_field(node, "inputs") or spec.inputs
        outputs = node_field(node, "output") or node_field(node, "outputs") or spec.outputs
        config = node_field(node, "config") or {}
        if not isinstance(config, dict):
            config = {}
        merged_config: Dict[str, Any] = {}
        for k, meta in (spec.config_schema or {}).items():
            if isinstance(meta, dict) and "default" in meta:
                merged_config[k] = meta["default"]
        merged_config.update(config)
        return list(inputs), list(outputs), merged_config

    def run(self, node: Dict[str, Any], state: FlowState, ctx: RunContext) -> DispatchResult:
        """Execute the entity inline and apply its result to state."""
        _inputs, outputs, merged_config = self._resolve_io(node)

        try:
            fn = self.spec.load_callable()
        except Exception as e:  # noqa: BLE001
            return DispatchResult(ok=False, error=f"entity '{self.id}' not callable: {e}")

        try:
            result = fn(state, merged_config, ctx)
        except Exception as e:  # noqa: BLE001 - node body errors are reported, not raised
            return DispatchResult(ok=False, error=f"{type(e).__name__}: {e}")

        goto: Optional[List[str]] = None
        text = ""
        payload: Any = result
        if isinstance(result, NodeResult):
            payload = result.outputs
            goto = result.goto
            text = result.text

        try:
            if not outputs and isinstance(payload, dict):
                # Dynamic-output node (e.g. a transform whose keys come from
                # config): write every returned key. Mutability is still
                # enforced per key.
                written = {}
                for k, v in payload.items():
                    state.set(k, v)
                    written[k] = v
            else:
                written = state.apply(outputs, payload)
        except Exception as e:  # StateMutationError or similar → fail the node
            return DispatchResult(ok=False, error=str(e))

        if not text:
            if written:
                text = "; ".join(f"{k}={_preview(v)}" for k, v in written.items())
            else:
                text = _preview(payload)
        return DispatchResult(ok=True, text=text, written=written, goto=goto)


# -------------------- backward-compatible module API --------------------
# These wrappers preserve the original function names used by runtime.flow_run and
# flow.validate while delegating to the class system above.

def resolve_entity(node: Dict[str, Any]) -> Optional[flow_registry.FlowEntitySpec]:
    """Return the FlowEntitySpec a node references, or None."""
    return FlowEntity.resolve_spec(node)


def is_agent_node(node: Dict[str, Any]) -> bool:
    """True when this node should run through the runner's agent path."""
    entity = FlowEntity.for_node(node)
    return bool(entity and entity.runs_in_process)


def run_entity_node(
    node: Dict[str, Any],
    spec: flow_registry.FlowEntitySpec,
    state: FlowState,
    ctx: RunContext,
) -> DispatchResult:
    """Execute a non-agent entity node inline and apply its result to state.

    Thin wrapper over the entity class system: builds the FlowEntity for the
    spec's category and calls its uniform ``run``.
    """
    impl = _CATEGORY_CLASSES.get(spec.category, EntityNode)
    return impl(spec).run(node, state, ctx)


def _preview(value: Any, limit: int = 300) -> str:
    s = value if isinstance(value, str) else repr(value)
    return s[:limit]
