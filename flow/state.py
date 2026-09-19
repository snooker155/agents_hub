"""
Shared flow state + node execution contract.

A flow run carries a single mutable ``FlowState`` object. Nodes read the slice
of state declared in their ``input`` keys and write results back to their
``output`` keys. Edges carry control flow; state carries data.

Mutability rule (from the flow meta ``mutability`` field):
- ``mutability: true``  → nodes may overwrite existing keys freely.
- ``mutability: false`` → existing keys are write-once. Adding a NEW key is
  always allowed; overwriting an EXISTING key raises ``StateMutationError``,
  which the engine treats as a node failure (strict / fail-fast).

The uniform node callable contract is::

    run(state: FlowState, config: dict, ctx: RunContext) -> NodeResult

where NodeResult is one of:
- a ``dict`` mapping output-key → value (applied to state under the node's
  declared ``output`` keys; keys not declared are ignored),
- a ``NodeResult`` instance (carries outputs plus optional routing for
  condition nodes),
- ``None`` (no state change).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


class StateMutationError(Exception):
    """Raised when a write violates the flow's mutability policy."""


@dataclass
class FlowState:
    """A named, mutable bag of values shared across all nodes in a run."""

    data: Dict[str, Any] = field(default_factory=dict)
    mutable: bool = True

    @classmethod
    def from_flow(cls, flow: Dict[str, Any], *, seed: Optional[Dict[str, Any]] = None) -> "FlowState":
        """Build initial state from a flow's declared ``state`` defaults + seed.

        ``mutability`` defaults to ``True`` when the flow does not declare it,
        preserving the permissive behaviour of flows authored before the field
        existed.
        """
        declared = flow.get("state") if isinstance(flow.get("state"), dict) else {}
        mutable = bool(flow.get("mutability", True))
        data: Dict[str, Any] = dict(declared)
        if seed:
            data.update(seed)
        return cls(data=data, mutable=mutable)

    # -- reads --

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def slice(self, keys: Iterable[str]) -> Dict[str, Any]:
        """Return only the requested keys that currently exist in state."""
        return {k: self.data[k] for k in keys if k in self.data}

    def __contains__(self, key: str) -> bool:  # enables `key in state`
        return key in self.data

    # -- writes --

    def set(self, key: str, value: Any) -> None:
        """Set one key, enforcing the mutability policy."""
        if not self.mutable and key in self.data:
            raise StateMutationError(
                f"State key '{key}' already exists and the flow is immutable "
                f"(mutability=false); only new keys may be added."
            )
        self.data[key] = value

    def apply(self, output_keys: List[str], result: Any) -> Dict[str, Any]:
        """Write a node's result into state under its declared ``output_keys``.

        - ``result`` is a dict → only keys present in ``output_keys`` are written
          (extra keys in the dict are ignored). If a single output key is
          declared and the dict does not contain it, the whole dict is stored
          under that key.
        - ``result`` is a scalar/str and exactly one output key is declared →
          stored under that key.
        - No output keys declared → nothing is written (the node is a side-effect
          or its text output is handled by the legacy path).

        Returns the dict of keys actually written (for logging). Raises
        ``StateMutationError`` via ``set`` on an immutable-key violation.
        """
        if not output_keys:
            return {}

        written: Dict[str, Any] = {}
        if isinstance(result, dict):
            matched = {k: result[k] for k in output_keys if k in result}
            if not matched and len(output_keys) == 1:
                # Result dict didn't name the output key — store it whole.
                matched = {output_keys[0]: result}
            for k, v in matched.items():
                self.set(k, v)
                written[k] = v
        elif result is not None and len(output_keys) == 1:
            self.set(output_keys[0], result)
            written[output_keys[0]] = result
        return written

    def snapshot(self) -> Dict[str, Any]:
        """A shallow copy of current state (for recordability / logging)."""
        return dict(self.data)


@dataclass
class NodeResult:
    """Structured result a node may return instead of a bare dict.

    ``outputs`` is applied to state under the node's declared output keys.
    ``goto`` (optional) is the target node id(s) a condition node selected; the
    engine uses it to choose which outgoing edge(s) to follow.
    """

    outputs: Dict[str, Any] = field(default_factory=dict)
    goto: Optional[List[str]] = None
    text: str = ""  # human-readable summary for logs / downstream agents


@dataclass
class RunContext:
    """Ambient context passed to every node callable.

    Kept deliberately small; carries the run identity and workspace so a node
    (e.g. an agent) can create sub-resources without reaching into globals.
    """

    flow_id: str = ""
    run_id: str = ""
    task_id: str = ""
    session_id: str = ""
    workspace: str = ""
    node_id: str = ""
    overrides: Dict[str, Any] = field(default_factory=dict)
