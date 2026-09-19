"""
The environment contract.

Mirrors the shape of ``views/compute`` runtimes (``step()`` / ``frame()``) but
is a separate, server-side implementation: a tick here is an LLM round, not a
numeric integration step.

Three things every environment declares, so the UI stays generic:

* ``PARAM_SCHEMA``   — its own configuration, in ``ToolSpec.parameters`` shape.
* ``ACTIONS``        — the typed action API agents may call.
* ``renderer``       — which world view the frontend should draw, plus the
                       frame shape it emits.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from playground.models import ActionResult


def speech_event(sender: str, recipient: str, text: str) -> str:
    """One delivered message as a line of the world's log.

    A function rather than an f-string at the call site because two readers
    depend on the exact wording: the event feed prints it, and the chronicle
    filters it back out — a scene that quotes the dialogue and then narrates
    the same words as an event says everything twice. Both sides call this, so
    the format can change without the two drifting apart.
    """
    flat = " ".join(str(text or "").split())
    return f"{sender} said to {recipient}: {flat[:80]}"


class Environment(ABC):
    """A deterministic world.

    Subclasses must be pure Python: given the same seed and the same sequence of
    actions, the world replays exactly. That is what makes an environment bug
    distinguishable from emergent behaviour, and what makes the world unit
    testable without any agent involved.
    """

    # ── Declared metadata (the UI reads these; do not hardcode them anywhere else)
    env_id: str = ""
    env_name: str = ""
    description: str = ""
    renderer: str = "table"          # which world view the frontend draws
    PARAM_SCHEMA: List[Dict[str, Any]] = []
    ACTIONS: List[Dict[str, Any]] = []
    # Named scored objectives a role may target, evaluated over final state.
    OBJECTIVES: List[str] = []
    # The actions that mean "I have nothing to do". Declared rather than
    # guessed because the triggered loop reads them as an answer: an agent that
    # acted is still in the middle of something and keeps its turn, an agent
    # that chose to do nothing has said the world may go quiet. Every
    # environment ships exactly one of these; a world that offered none could
    # never end except on a cap.
    IDLE_ACTIONS: Tuple[str, ...] = ("observe",)

    def __init__(self, params: Optional[Dict[str, Any]] = None, seed: int = 42):
        self.params = self._coerce_params(params or {})
        self.seed = int(seed)
        self.tick = 0
        # Messages queued for delivery on the *next* tick. Nothing an agent says
        # is visible within the tick it said it — which is what makes markets
        # and social dynamics interesting rather than a turn-based chat.
        self._inbox: Dict[str, List[Dict[str, Any]]] = {}
        self._events: List[str] = []
        # Why an agent should be woken next tick, when the scenario runs in
        # triggered mode. Messages produce one of these on their own; anything
        # else the world does *to* an agent (its order filled, an item handed
        # over, someone walking into its room) is reported by the environment
        # calling ``poke``. Ignored entirely in synchronous mode.
        self._pokes: Dict[str, List[str]] = {}

    # ── Parameters ───────────────────────────────────────────────────────────

    @classmethod
    def _coerce_params(cls, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Apply defaults and types from ``PARAM_SCHEMA``; ignore unknown keys."""
        out: Dict[str, Any] = {}
        for spec in cls.PARAM_SCHEMA:
            name = spec["name"]
            value = raw.get(name, spec.get("default"))
            kind = spec.get("type", "string")
            try:
                if kind == "integer":
                    value = int(value)
                elif kind == "number":
                    value = float(value)
                elif kind == "boolean":
                    value = bool(value)
                elif kind == "list":
                    if isinstance(value, str):
                        value = [v.strip() for v in value.split(",") if v.strip()]
                    value = list(value or [])
                else:
                    value = str(value) if value is not None else ""
            except (TypeError, ValueError):
                value = spec.get("default")
            out[name] = value
        return out

    @classmethod
    def describe(cls) -> Dict[str, Any]:
        return {
            "env_id": cls.env_id,
            "env_name": cls.env_name,
            "description": cls.description,
            "renderer": cls.renderer,
            "params": list(cls.PARAM_SCHEMA),
            "actions": list(cls.ACTIONS),
            "objectives": list(cls.OBJECTIVES),
        }

    def action_help(self, agent: str = "") -> str:
        """The action API rendered for a prompt.

        ``agent`` is the character the prompt is being built for. Shipped
        environments offer everyone the same API and ignore it; a world whose
        author restricted an action to one role answers per character, because
        listing an action somebody may not take spends context to buy refusals.

        Agents in a playground get **environment actions only** — no filesystem,
        no shell, no web. Agent A's output is agent B's input and agents may be
        *designed* to deceive, so the content flowing between them is untrusted
        by construction; a closed toolset is the cleanest possible instance of
        the capability model.
        """
        lines = []
        for a in self.ACTIONS:
            args = ", ".join(
                f'"{p["name"]}": <{p.get("type", "string")}>' for p in a.get("args", [])
            )
            lines.append(f'- {a["name"]}: {a.get("description", "")}\n  {{"action": "{a["name"]}", "args": {{{args}}}}}')
        return "\n".join(lines)

    def world_brief(self, agent: str = "") -> str:
        """What this world tells a character about itself, above its actions.

        Empty for the shipped environments: a market's rules *are* its actions,
        and prose repeating them is prose an agent has to reconcile. An authored
        world is the case that needs it — the etiquette, the stakes and the
        setting are exactly the parts its author could not express as a
        requirement, and nothing else in the prompt carries them.
        """
        return ""

    # ── Setup ────────────────────────────────────────────────────────────────

    def register_agents(self, agents: List[str]) -> None:
        """Put the cast into the world, by in-world name."""

    def register_cast(self, cast: List[Dict[str, str]]) -> None:
        """Put the cast in, names *and* the roles they were cast in.

        Two entry points because roles are a scenario's idea, not every world's:
        a market does not care that one of its traders is "the nervous one".
        The default drops the roles; a world that places characters by role, or
        restricts what a role may do, overrides this one instead.
        """
        self.register_agents([str(c.get("name") or "") for c in cast
                              if str(c.get("name") or "")])

    # ── The three phases of a tick ───────────────────────────────────────────

    @abstractmethod
    def observe(self, agent: str) -> Dict[str, Any]:
        """Build ``agent``'s **private** view of the world.

        Partial observation is the feature, not an optimisation: each agent sees
        only its own state, the public state, and messages addressed to it.
        Information asymmetry is what generates emergence — trade needs
        disagreement, drama needs secrets — and it is also a hard context-window
        constraint.
        """

    @abstractmethod
    def apply(self, agent: str, action: str, args: Dict[str, Any]) -> ActionResult:
        """Validate and apply one action. Never raises for a bad action —
        an invalid action is a normal outcome and comes back as ``ok=False``."""

    @abstractmethod
    def frame(self) -> Dict[str, Any]:
        """A snapshot of public world state for the renderer."""

    def resolve(self, submissions: List[Dict[str, Any]]) -> List[ActionResult]:
        """Apply every agent's action for this tick in one atomic pass.

        Order is the environment's own deterministic rule, not arrival order:
        real concurrency where it costs money (the LLM calls), a consistent
        world when it matters (here). The default sorts by agent name so a run
        is reproducible; environments with real priority rules (price-time in a
        market) override this.
        """
        results: List[ActionResult] = []
        for sub in sorted(submissions, key=lambda s: str(s.get("agent", ""))):
            results.append(
                self.apply(sub.get("agent", ""), sub.get("action", ""), sub.get("args") or {})
            )
        return results

    # ── Messaging (shared by every environment) ──────────────────────────────

    def queue_message(self, sender: str, recipient: str, text: str) -> ActionResult:
        """Deliver a message on the next tick, not this one.

        Every delivery is also a line in the world's log. Speech used to be
        left out of it on the grounds that the log is for things nobody said —
        but the rule was never actually kept: ``announce`` wrote a line and
        ``speak_to`` did not, so talking to the room was visible in the log and
        talking to one person was visible nowhere. Saying something to somebody
        is an event in the world, and this is the one place every environment's
        messages pass through, so it is the one place it has to be said.
        """
        self._inbox.setdefault(recipient, []).append(
            {"from": sender, "text": str(text)[:2000], "tick": self.tick}
        )
        self.log_event(speech_event(sender, recipient, text))
        return ActionResult(
            # The text belongs in the result, not only in the inbox: the
            # resolution is what the tick log and the speaker's own journal are
            # built from, and "message queued for Bob" is a delivery receipt
            # rather than a record of what was said.
            agent=sender, action="speak_to",
            args={"agent": recipient, "text": str(text)[:2000]},
            ok=True, message=f"message queued for {recipient}",
        )

    def drain_inbox(self, agent: str) -> List[Dict[str, Any]]:
        return self._inbox.pop(agent, [])

    def restore_inbox(self, agent: str, messages: List[Dict[str, Any]]) -> None:
        """Put drained mail back, oldest first.

        Observation empties an inbox before the agent's model has said a word,
        so a turn that then fails — a timeout, an unparseable reply — used to
        throw away everything that was addressed to it. The answer to the
        question it asked last tick would vanish, and the agent, seeing no
        reply, would ask again. Mail an agent never got to read is mail it has
        not read: it goes back, and in triggered mode it wakes them again.
        """
        if not messages:
            return
        self._inbox.setdefault(agent, [])[:0] = list(messages)

    # ── Triggers (what wakes an agent when the world is not synchronous) ─────

    def poke(self, agent: str, reason: str) -> None:
        """Record that the world did something to ``agent`` worth reacting to.

        Environments call this from ``apply`` when an action lands on someone
        other than the actor. Without it a triggered world only ever wakes on
        speech, and a market where your order filled would leave you asleep.
        """
        if not agent:
            return
        self._pokes.setdefault(agent, []).append(str(reason))

    def pending_triggers(self, agent: str) -> List[str]:
        """Why ``agent`` should act next tick — a *peek*, never a drain.

        The runner reads this before observing anyone, and observation is what
        empties the inbox. An agent that is not woken keeps its mail: nothing
        addressed to it is lost because it stayed quiet for a tick.
        """
        reasons: List[str] = []
        for msg in self._inbox.get(agent) or ():
            reasons.append(f"{msg.get('from') or 'someone'} spoke to you")
        reasons.extend(self._pokes.get(agent) or ())
        return reasons

    def clear_triggers(self, agent: str) -> None:
        """Forget the pokes for an agent that has now acted on them. The inbox
        is not touched here — ``observe`` drains that as it builds the view."""
        self._pokes.pop(agent, None)

    def log_event(self, text: str) -> None:
        self._events.append(text)

    def drain_events(self) -> List[str]:
        events, self._events = self._events, []
        return events

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def begin_tick(self, tick: int) -> None:
        self.tick = tick

    def end_tick(self) -> None:
        """Hook for per-tick world dynamics that are not any agent's action."""

    def is_done(self) -> bool:
        """True when the world has reached a terminal state before max_ticks."""
        return False

    def score(self) -> Dict[str, Any]:
        """Scored objectives over final state. ``{}`` when the env defines none."""
        return {}

    def state(self) -> Dict[str, Any]:
        """Full state, for the final record. Defaults to the public frame."""
        return self.frame()


__all__ = ["Environment", "speech_event"]
