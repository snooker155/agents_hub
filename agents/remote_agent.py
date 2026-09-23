"""RemoteAgent — an agent that lives outside this service and is reached over HTTP.

Built-in agents are assembled here: a system prompt from
``agents/definitions/<id>/`` plus tools from ``tools/registry.py``, wired into a
LangChain executor by :mod:`agents.agent_factory`. That only works for agents
whose *behaviour* we own.

An imported agent is the opposite case: a finished, already-tested agent living
in its own repository, with its own dependencies, its own model access and its
own tool layer. Importing its Python into this process would mean inheriting its
dependency tree, so instead the hub speaks to it over HTTP and keeps the process
boundary. ``RemoteAgent`` is the adapter that makes such a service look like any
other :class:`~agents.agent_base.AgentBase` to the rest of the system, so runs,
tasks, flows and chat reach it through the unchanged
``create_agent(...).run(...)`` path.

The wire contract is deliberately tiny — one required endpoint::

    POST <url><run_path>
        {"prompt": str, "run_id": str | null, "workspace": str | null}
    ->  {"ok": bool, "output": str, "error": str | null, "steps": [...]}
        or, for an agent that stopped to ask:
        {"ok": true, "interrupt": {"question": str, "choices": [...], "key": str}}

    GET  <url><health_path>          # optional, used by the readiness check

It is the same shape :mod:`runtime.http_server` already serves for containerised
built-in agents, so an agent packaged for this hub and a built-in agent exposed
over HTTP are indistinguishable to the caller.

A remote may additionally declare ``runtime.stream_path``, and then a run is
streamed rather than awaited::

    POST <url><stream_path>          # same body; replies NDJSON or SSE
        {"type": "token",      "token": "..."}
        {"type": "thinking",   "message": "..."}
        {"type": "tool_start", "name": "...", "input": "..."}
        {"type": "tool_end",   "name": "...", "output": "..."}
        {"type": "usage",      "prompt_tokens": N, "completion_tokens": N}
        {"type": "node_start", "node": "...", "depth": 0}
        {"type": "node_end",   "node": "...", "ok": true, "next": "..."}
        {"type": "done",       "ok": true, "output": "...", "error": null}

``node_start``/``node_end`` are for a remote that is internally a graph. They
are optional, and an agent that is one straight line simply never sends them.
They are translated to ``graph_node_start``/``graph_node_end`` rather than
forwarded under their own names, because ``node_start`` already means "a node of
a *hub flow* started" on the chat stream, and a remote agent's internal nodes
are not flow nodes: conflating them would have a remote agent's steps open flow
bubbles in the chat of a conversation that is not running a flow.

Those frames are translated onto the vocabulary the chat UI already renders and
pushed through the same emitter a delegated local agent uses
(``ChatStreamCallback.emit_external`` / :mod:`common.stream_sink`), so a remote
agent's tokens appear in the chat bubble exactly like a built-in agent's. The
``usage`` frame is credited to the run's token counters — the remote is the only
party that can know them, since the model call happens in its process.

A remote that can *pause* declares ``runtime.resume_path`` as well. It then ends
a run with an ``interrupt`` frame instead of ``done``, and the hub records the
run as ``awaiting_input`` with the question on it — the same state, and the same
surfaces, an agent of this hub's own reaches through ``ask_user``. When the
person answers, :meth:`RemoteAgent.resume` posts the answer to ``resume_path``
and streams whatever the agent does next::

    POST <url><resume_path>
        {"run_id": str, "value": Any, "key": str}
    ->  the same frames a run replies with

Re-running the agent with the answer in its prompt, which is how this hub
resumes its own agents, is wrong for a remote one: an agent that suspended with
its state on a checkpointer has somewhere to come back to, and starting it again
from the top is a different execution with the same words in it.

Streaming is used only when both halves are present: the remote declared the
endpoint *and* something on this side is listening. Otherwise the single-shot
POST runs, which is one round trip and is all the contract requires.

A remote may instead speak **A2A** (the Agent2Agent protocol). Its descriptor
then carries ``kind: "a2a"`` and its ``url`` is a JSON-RPC endpoint rather than a
base with paths under it::

    POST <url>    {"jsonrpc": "2.0", "method": "message/send",
                   "params": {"message": {"role": "user", "parts": [...]},
                              "configuration": {"blocking": true},
                              "metadata": {"run_id": ..., "workspace": ...}}}
    ->  {"jsonrpc": "2.0", "result": {Task}}

Only the wire format differs: the Task's artifacts become the run's output, its
``input-required`` state becomes ``awaiting_input`` (and the answer is sent as a
second message on the same ``taskId``), and ``message/stream``'s events become
the same ``token``/``done`` frames everything downstream already renders. The
translation is in :mod:`a2a.client`, kept pure so it is tested against recorded
JSON; this class only carries it over HTTP.

Two limits remain, and they follow from the process boundary:

* Without a ``usage`` frame there is no token or cost accounting. LangChain
  callbacks observe nothing here, so a remote that reports no usage leaves the
  cost columns at zero. A remote that also knows its own dollar cost (for
  example a CLI it wraps, such as Claude Code, prints one) may add an optional
  ``cost_usd`` field to its ``usage`` frame, or nest ``usage`` with a
  ``cost_usd`` field under its ``done`` frame. That figure is recorded on the
  run as ``reported_cost_usd`` and is preferred over catalog pricing wherever
  a run's cost is read, since the remote's own bill is more accurate than a
  hub-side estimate priced from token counts alone.
* Tools listed on the registry record are informational for a remote agent: the
  hub does not supply them, the remote's own tool layer does.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from agents.agent_base import AgentBase, AgentResult, ToolResult
from common.agent_frames import FrameTranslator


# Defaults for descriptor keys a manifest may omit.
DEFAULT_RUN_PATH = "/run"
DEFAULT_HEALTH_PATH = "/health"
# Suggested path for the optional streaming endpoint. Only a manifest that
# declares ``runtime.stream_path`` enables streaming — there is no default,
# because probing a path an agent never implemented would cost a failed request
# on every run.
SUGGESTED_STREAM_PATH = "/run/stream"
# Suggested path for the optional topology endpoint, on the same opt-in terms.
SUGGESTED_GRAPH_PATH = "/graph"
# Suggested path for the optional resume endpoint, on the same opt-in terms.
SUGGESTED_RESUME_PATH = "/resume"
DEFAULT_TIMEOUT = 600

# Ceilings on a fetched topology. The picture is drawn in a browser and comes
# from a service this hub does not control, so a graph too large to render, or a
# hostile one, is truncated rather than shipped to the frontend whole.
MAX_TOPOLOGY_NODES = 300
MAX_TOPOLOGY_EDGES = 900
MAX_TOPOLOGY_LABEL = 120
DEFAULT_AUTH_HEADER = "Authorization"


class RemoteAgentError(Exception):
    """Raised when the remote agent cannot be reached or answers unusably."""


def normalize_base_url(url: str) -> str:
    """Strip a trailing slash so ``url + path`` never doubles the separator."""
    return (url or "").strip().rstrip("/")


def resolve_auth_header(remote: Dict[str, Any]) -> Dict[str, str]:
    """Build the auth header for a remote descriptor, or ``{}`` when unused.

    The token itself is never stored in ``agents.json`` — the descriptor names
    an environment variable (``auth_token_env``) and the value is read from the
    process environment at call time. A named-but-unset variable yields no
    header; the readiness check is what tells the operator it is missing, so a
    run fails at the remote's own 401 rather than on a confusing KeyError.
    """
    env_name = (remote.get("auth_token_env") or "").strip()
    if not env_name:
        return {}
    token = (os.environ.get(env_name) or "").strip()
    if not token:
        return {}
    header = (remote.get("auth_header") or DEFAULT_AUTH_HEADER).strip() or DEFAULT_AUTH_HEADER
    scheme = remote.get("auth_scheme")
    # ``auth_scheme`` defaults to Bearer for Authorization and to nothing for a
    # custom header (e.g. X-API-Key: <token>), which is how such headers are
    # conventionally sent.
    if scheme is None:
        scheme = "Bearer" if header.lower() == "authorization" else ""
    scheme = str(scheme).strip()
    return {header: f"{scheme} {token}".strip() if scheme else token}


def _extract_output(payload: Any) -> str:
    """Pull the answer text out of a remote response, tolerating key variants.

    Agents packaged by different people name this field differently; accepting
    the common spellings costs nothing and removes a whole class of "imported
    agent returns empty output" support questions.
    """
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return str(payload)
    for key in ("output", "agent_output", "result", "response", "text", "answer"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
        # A nested {"result": {"output": ...}} is common enough to unwrap once.
        if isinstance(value, dict):
            inner = _extract_output(value)
            if inner:
                return inner
    return ""


def _extract_steps(payload: Any) -> List[ToolResult]:
    """Map an optional ``steps`` array onto ToolResult so the run trail renders.

    Every field is optional; a remote that reports nothing simply yields no
    steps rather than failing the run.
    """
    steps: List[ToolResult] = []
    if not isinstance(payload, dict):
        return steps
    raw = payload.get("steps")
    if not isinstance(raw, list):
        return steps
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        args = entry.get("args")
        if not isinstance(args, dict):
            args = {"input": args} if args is not None else {}
        steps.append(ToolResult(
            name=str(entry.get("name") or entry.get("tool") or ""),
            args=args,
            output=str(entry.get("output") if entry.get("output") is not None else ""),
        ))
    return steps


def _clip_label(value: Any) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= MAX_TOPOLOGY_LABEL else text[:MAX_TOPOLOGY_LABEL] + "…"


def normalize_topology(payload: Any) -> Dict[str, Any]:
    """Coerce a remote's ``/graph`` answer into the shape the canvas draws.

    The remote is asked to normalise its own framework's format (the bundled
    LangGraph adapter does), so this is not a second translation layer: it is
    the boundary check. Whatever a foreign service returns lands in a browser,
    so the shape is enforced, the labels are clipped and the size is capped
    here rather than trusted.

    Edges to nodes that were never declared are dropped instead of drawn, since
    a renderer given a dangling edge either crashes or invents a node.
    """
    if not isinstance(payload, dict):
        return {"ok": False, "error": "topology endpoint did not return a JSON object",
                "nodes": [], "edges": []}
    if payload.get("ok") is False:
        return {"ok": False, "error": str(payload.get("error") or "the agent reported no topology"),
                "nodes": [], "edges": []}

    nodes: List[Dict[str, Any]] = []
    seen = set()
    for raw in (payload.get("nodes") or [])[:MAX_TOPOLOGY_NODES]:
        if not isinstance(raw, dict):
            continue
        node_id = str(raw.get("id") or "").strip()
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        kind = str(raw.get("kind") or "node").strip() or "node"
        nodes.append({
            "id": node_id,
            "label": _clip_label(raw.get("label") or node_id),
            "kind": kind if kind in ("node", "terminal", "subgraph") else "node",
        })

    edges: List[Dict[str, Any]] = []
    for raw in (payload.get("edges") or [])[:MAX_TOPOLOGY_EDGES]:
        if not isinstance(raw, dict):
            continue
        source, target = str(raw.get("source") or ""), str(raw.get("target") or "")
        if source not in seen or target not in seen:
            continue
        edges.append({
            "source": source,
            "target": target,
            "label": _clip_label(raw["label"]) if raw.get("label") else None,
            "conditional": bool(raw.get("conditional")),
        })

    return {
        "ok": bool(nodes),
        "framework": _clip_label(payload.get("framework") or "") or "unknown",
        "nodes": nodes,
        "edges": edges,
        "error": None if nodes else "the topology endpoint declared no nodes",
    }


def _parse_stream_line(line: str) -> Optional[Dict[str, Any]]:
    """Decode one line of the remote's stream, tolerating both wire formats.

    NDJSON sends a bare JSON object per line; SSE prefixes it with ``data: ``
    and separates frames with blank lines. Accepting both means a repository can
    use whichever its web framework makes easy, and neither needs a flag.
    Undecodable lines are skipped rather than failing the run — a keep-alive
    comment or a stray blank line must not kill a working stream.
    """
    import json

    raw = (line or "").strip()
    if not raw or raw.startswith(":"):
        return None
    if raw.startswith("data:"):
        raw = raw[len("data:"):].strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        event = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return event if isinstance(event, dict) else None


def _extract_reported_cost(frame: Dict[str, Any]) -> Optional[float]:
    """Read an optional ``cost_usd`` off a raw ``usage`` or ``done`` frame.

    ``FrameTranslator.feed`` strips unrecognised fields on its way to producing
    a hub event, so ``cost_usd`` is read from the *wire* frame here, before
    translation, rather than off the event the translator produced. A ``usage``
    frame carries it directly; a ``done`` frame that nests its usage (the
    common shape for an agent that only knows its total at the very end)
    carries it one level down, exactly where the token counts already are.
    """
    kind = str(frame.get("type") or "").strip()
    if kind == "usage":
        raw = frame.get("cost_usd")
    elif kind in ("done", "final", "result"):
        nested = frame.get("usage")
        raw = nested.get("cost_usd") if isinstance(nested, dict) else None
    else:
        raw = None
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


class _StreamState:
    """One streamed run: the shared translation, plus where its events go.

    The translation itself lives in :mod:`common.agent_frames`, shared with the
    push path (``/api/ingest``) so a run renders the same whether the hub called
    the agent or the agent called the hub. What is local to this direction is
    the emitter and the token counters: a pulled run is being watched by a chat
    session right now, and its usage is credited to the callbacks that session
    reads its cost off.
    """

    def __init__(self, emitter: Optional[Any], usage_sinks: List[Any], run_id: Optional[str] = None):
        self._emitter = emitter
        self._usage_sinks = usage_sinks
        self.translator = FrameTranslator()
        # An A2A stream ends with the answer as an artifact, separately from
        # the tokens that already showed it. It is kept here rather than
        # emitted so the chat does not print the reply twice, and used as the
        # output when the closing frame carries none.
        self.artifact_text = ""
        # The run this stream belongs to, so a reported cost can be written
        # straight to its record. None when the caller never had a run id to
        # give (for example these tests exercise RemoteAgent directly); a
        # reported cost is then still credited to the usage sinks' tokens but
        # has nowhere durable to land.
        self.run_id = str(run_id) if run_id else None
        # The remote's own running total of what this run has cost, in USD,
        # summed across every usage frame that reported one. None means no
        # frame has reported a cost yet, which is different from a reported
        # zero.
        self.reported_cost_usd: Optional[float] = None

    @property
    def text_parts(self) -> List[str]:
        return self.translator.text_parts

    @property
    def done(self) -> Optional[Dict[str, Any]]:
        return self.translator.done

    @property
    def steps(self) -> List[ToolResult]:
        return [
            ToolResult(name=s["name"], args=s["args"], output=s["output"])
            for s in self.translator.steps
        ]

    def emit(self, payload: Dict[str, Any]) -> None:
        """Forward one event, swallowing listener errors.

        Streaming is decoration of a run that is happening regardless; a
        failure to display it must not fail the work.
        """
        if self._emitter is None:
            return
        try:
            self._emitter(payload)
        except Exception:
            pass

    def handle(self, frame: Dict[str, Any]) -> None:
        """Translate one frame from the remote and forward what it produced."""
        for event in self.translator.feed(frame):
            if event.get("type") == "usage":
                self._credit(event, _extract_reported_cost(frame))
            self.emit(event)

    def _credit(self, usage: Dict[str, Any], cost_usd: Optional[float] = None) -> None:
        """Add remote-reported token usage, and optionally its dollar cost, to
        the run.

        The remote is the only party that can know these numbers, since the
        model call happened in its process. When it reports them they are
        credited exactly like a local call's, and the run's cost stops reading
        as zero.

        ``cost_usd`` is a further refinement some remotes can offer: a CLI such
        as Claude Code prices its own call and reports the dollar figure
        directly, which is more accurate than pricing token counts against this
        hub's catalog. No model lookup is needed, and it is exact for whatever
        pricing the CLI's own provider actually charged. When present it is
        summed onto this stream's running total and written straight to the run
        record, so ``managers.runs.groups.runs_cost`` and the Costs page can
        prefer it over the catalog estimate.
        """
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))
        cached = int(usage.get("cached_tokens") or 0)
        for sink in self._usage_sinks:
            try:
                sink.prompt_tokens += prompt
                sink.completion_tokens += completion
                sink.total_tokens += total
                sink.cached_prompt_tokens = getattr(sink, "cached_prompt_tokens", 0) + cached
            except Exception:
                continue
        if cost_usd is not None:
            self.reported_cost_usd = (self.reported_cost_usd or 0.0) + cost_usd
            if self.run_id:
                try:
                    from managers.runs.store import update_run
                    update_run(self.run_id, {"reported_cost_usd": self.reported_cost_usd})
                except Exception:
                    # A run record that cannot be found or updated yet (the
                    # caller opened no run at all, or this is a test exercising
                    # RemoteAgent directly) must not fail a run that is
                    # otherwise working. The tokens are still credited above.
                    pass


class RemoteAgent(AgentBase):
    """An ``AgentBase`` whose ``run`` is an HTTP call to an external service."""

    def __init__(
        self,
        agent_id: str,
        name: str,
        remote: Dict[str, Any],
        *,
        description: str = "",
        workspace: Optional[str] = None,
        verbose: bool = False,
    ):
        # No prompt, no tools, no model: all three live in the remote service.
        # AgentBase still wants them, so pass empties — build_executor is
        # overridden below so nothing ever tries to construct an LLM from them.
        super().__init__(
            agent_id=agent_id,
            name=name,
            system_prompt="",
            tools=[],
            verbose=verbose,
        )
        self.remote = dict(remote or {})
        self.description = description
        self.workspace = workspace

    # ── descriptor accessors ────────────────────────────────────────────────

    @property
    def base_url_remote(self) -> str:
        return normalize_base_url(self.remote.get("url") or "")

    @property
    def is_a2a(self) -> bool:
        """Whether this remote speaks A2A rather than the hub's own contract."""
        return str(self.remote.get("kind") or "").strip().lower() == "a2a"

    @property
    def run_url(self) -> str:
        if self.is_a2a:
            # One endpoint, every method: A2A puts the verb in the JSON-RPC
            # body, so there is no path to append.
            return self.base_url_remote
        path = self.remote.get("run_path") or DEFAULT_RUN_PATH
        return f"{self.base_url_remote}{path if path.startswith('/') else '/' + path}"

    @property
    def health_url(self) -> str:
        path = self.remote.get("health_path") or DEFAULT_HEALTH_PATH
        return f"{self.base_url_remote}{path if path.startswith('/') else '/' + path}"

    @property
    def stream_url(self) -> str:
        if self.is_a2a:
            return self.base_url_remote
        path = self.remote.get("stream_path") or ""
        if not path:
            return ""
        return f"{self.base_url_remote}{path if path.startswith('/') else '/' + path}"

    @property
    def graph_url(self) -> str:
        path = self.remote.get("graph_path") or ""
        if not path:
            return ""
        return f"{self.base_url_remote}{path if path.startswith('/') else '/' + path}"

    @property
    def resume_url(self) -> str:
        path = self.remote.get("resume_path") or ""
        if not path:
            return ""
        return f"{self.base_url_remote}{path if path.startswith('/') else '/' + path}"

    @property
    def supports_resume(self) -> bool:
        """Whether a paused run of this agent can be continued where it stopped.

        Always true for A2A: continuing is not an extra endpoint there but the
        ordinary way to speak to a task that asked something, so every agent
        that speaks the protocol can be answered.
        """
        if self.is_a2a:
            return bool(self.base_url_remote)
        return bool(self.base_url_remote and self.remote.get("resume_path"))

    @property
    def supports_topology(self) -> bool:
        """Whether the remote can describe its own shape."""
        return bool(self.base_url_remote and self.remote.get("graph_path"))

    @property
    def supports_streaming(self) -> bool:
        """Whether the remote declared a streaming endpoint.

        Opt-in by design: ``/run`` is the one endpoint the contract requires, so
        an agent that never streams stays trivially importable. Declaring
        ``runtime.stream_path`` is what turns streaming on. For an A2A agent the
        equivalent statement is ``capabilities.streaming`` on its card, recorded
        on the descriptor at import time.
        """
        if self.is_a2a:
            return bool(self.base_url_remote and self.remote.get("streaming"))
        return bool(self.base_url_remote and self.remote.get("stream_path"))

    @property
    def timeout(self) -> int:
        try:
            return int(self.remote.get("timeout") or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT

    def build_executor(self) -> Any:
        """A remote agent has no local executor — the remote owns the loop.

        Overridden (rather than inherited) so that any caller reaching for
        ``.executor`` — most notably ``agent_factory.build_agent_executor`` —
        fails with an explanation instead of trying to build a LangChain agent
        out of an empty prompt and no tools.
        """
        raise RemoteAgentError(
            f"Agent '{self.agent_id}' is a remote agent: it has no local executor. "
            f"Call .run(prompt) instead, which POSTs to {self.run_url or '<no url configured>'}."
        )

    # ── health ──────────────────────────────────────────────────────────────

    def check_health(self, timeout: int = 10) -> Dict[str, Any]:
        """Probe the remote's health endpoint.

        Returns ``{"ok": bool, "detail": str}``. Never raises — the readiness
        report treats an unreachable service as a finding, not a crash.
        """
        import httpx

        if not self.base_url_remote:
            return {"ok": False, "detail": "no url configured"}
        if self.is_a2a:
            # A2A has no health method. The card is the equivalent: a service
            # that serves one is up, and it is the only URL the protocol
            # guarantees answers a GET.
            card_url = str(self.remote.get("card_url") or "")
            if not card_url:
                return {"ok": False, "detail": "no agent card URL recorded for this A2A agent"}
            from agents.importer import a2a_import

            probe = a2a_import.probe(card_url)
            return {"ok": probe["ok"], "detail": probe["detail"]}
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(self.health_url, headers=resolve_auth_header(self.remote))
            if resp.status_code < 400:
                return {"ok": True, "detail": f"HTTP {resp.status_code}"}
            return {"ok": False, "detail": f"HTTP {resp.status_code}"}
        except Exception as exc:  # noqa: BLE001 - any transport error is "not reachable"
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    def fetch_topology(self, timeout: int = 10) -> Dict[str, Any]:
        """Ask the remote for its own shape, normalised and bounded.

        Never raises, for the same reason ``check_health`` does not: an agent
        whose service is down must still import, and a missing picture is a
        blank panel with a reason on it, not a failed page.
        """
        import httpx

        if not self.supports_topology:
            return {"ok": False, "error": "this agent declares no graph_path",
                    "nodes": [], "edges": []}
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(self.graph_url, headers=resolve_auth_header(self.remote))
        except Exception as exc:  # noqa: BLE001 - unreachable is a reason, not a crash
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "nodes": [], "edges": []}

        if resp.status_code >= 400:
            return {"ok": False, "error": f"HTTP {resp.status_code} from {self.graph_url}",
                    "nodes": [], "edges": []}
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": f"{self.graph_url} did not return JSON",
                    "nodes": [], "edges": []}
        return normalize_topology(payload)

    # ── streaming plumbing ──────────────────────────────────────────────────

    @staticmethod
    def _resolve_emitter(callbacks: Any) -> Optional[Any]:
        """Find somewhere to forward live events to, or None.

        Two sources, in order of specificity:

        1. A callback exposing ``emit_external`` — that is ``ChatStreamCallback``,
           whose whole purpose is accepting externally produced events and
           marshalling them onto the chat's SSE queue. It is documented as
           thread-safe, so the synchronous path may use it too.
        2. ``stream_sink.get_emitter()`` — the context-var channel a delegating
           parent installs, which is how a remote agent invoked through
           ``run_agent_tool`` reaches the parent's stream.

        None means nobody is listening, and the caller falls back to a single
        request/response — streaming into a void buys nothing.
        """
        for cb in (callbacks if isinstance(callbacks, (list, tuple)) else [callbacks] if callbacks else []):
            emit = getattr(cb, "emit_external", None)
            if callable(emit):
                return emit
        try:
            from common import stream_sink
            return stream_sink.get_emitter()
        except Exception:
            return None

    @staticmethod
    def _usage_sinks(callbacks: Any) -> List[Any]:
        """Callbacks that keep token counters, so remote-reported usage lands.

        Both ``ChatStreamCallback`` and ``StatsCollectorCallback`` accumulate
        into ``prompt_tokens``/``completion_tokens``/``total_tokens``; the chat
        pipeline and the run recorders read them straight off the callback. A
        remote that reports its own usage can therefore be credited exactly like
        a local model call — which is why usage is worth asking for in the
        streaming contract.
        """
        sinks = []
        for cb in (callbacks if isinstance(callbacks, (list, tuple)) else [callbacks] if callbacks else []):
            if all(hasattr(cb, attr) for attr in ("prompt_tokens", "completion_tokens", "total_tokens")):
                sinks.append(cb)
        return sinks

    def _stream_result(self, state: "_StreamState") -> AgentResult:
        """Turn a completed stream into an AgentResult.

        A remote that ends its stream without a ``done`` frame is not treated as
        a failure: the tokens it already sent are the answer. Only a stream that
        produced neither is an error, and it says so with the endpoint named.
        """
        if state.done is not None:
            payload = dict(state.done)
            payload.setdefault("steps", [s.model_dump() for s in state.steps])
            if not _extract_output(payload) and state.artifact_text:
                payload["output"] = state.artifact_text
            if not _extract_output(payload) and state.text_parts:
                payload["output"] = "".join(state.text_parts)
            result = self._map_hub_payload(payload)
            # Steps collected live are richer than whatever the done frame
            # repeats, so keep them when the frame carried none.
            if not result.steps and state.steps:
                result.steps = state.steps
            return result

        pending = state.translator.interrupt
        if pending is not None:
            # The agent stopped to ask, which is neither an answer nor a
            # failure. ``awaiting_input`` is the hub's own word for it, so the
            # task runner parks this run exactly as it parks a local agent that
            # called ask_user, and one set of surfaces serves both.
            return AgentResult(
                ok=True,
                status="awaiting_input",
                agent_output=pending.get("question") or "".join(state.text_parts).strip(),
                pending_question=dict(pending),
                steps=state.steps,
            )

        text = "".join(state.text_parts).strip()
        if text:
            return AgentResult(ok=True, status="done", agent_output=text, steps=state.steps)
        return AgentResult(
            ok=False,
            status="error",
            error=(
                f"Remote agent closed the stream at {self.stream_url} without sending "
                f"any output or a 'done' event."
            ),
        )

    # ── run ─────────────────────────────────────────────────────────────────

    def _request(
        self,
        instruction: str,
        kwargs: Dict[str, Any],
        *,
        stream: bool = False,
    ) -> tuple[Dict[str, Any], Dict[str, str]]:
        """Build the JSON body and headers for one run request.

        ``stream`` only matters for A2A, where the method name is part of the body
        rather than part of the URL.
        """
        headers = {"Content-Type": "application/json", **resolve_auth_header(self.remote)}
        if self.is_a2a:
            from a2a import client as a2a_client

            body = a2a_client.build_send_request(
                instruction,
                stream=stream,
                run_id=kwargs.get("run_id"),
                workspace=kwargs.get("workspace", self.workspace),
            )
            return body, headers
        body: Dict[str, Any] = {
            "prompt": instruction,
            "run_id": kwargs.get("run_id"),
            "workspace": kwargs.get("workspace", self.workspace),
        }
        return body, headers

    def _hub_frames(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The hub stream frames one line off the wire produced.

        A native remote already speaks the hub's frame vocabulary, so its event
        passes through untouched; an A2A event is translated first. Doing it
        here keeps one streaming loop for both.
        """
        if not self.is_a2a:
            return [event]
        from a2a import client as a2a_client

        return a2a_client.frames_from_stream_event(event)

    def _feed(self, state: "_StreamState", line: str) -> None:
        """Parse one line of the stream and hand its frames to the translator."""
        event = _parse_stream_line(line)
        if event is None:
            return
        for frame in self._hub_frames(event):
            if frame.get("type") == "a2a_artifact":
                state.artifact_text = str(frame.get("text") or "") or state.artifact_text
                continue
            state.handle(frame)

    def _map_a2a(self, payload: Any) -> AgentResult:
        """Turn a JSON-RPC reply from an A2A agent into an AgentResult."""
        from a2a import client as a2a_client

        outcome = a2a_client.outcome_from_response(payload)
        if outcome["status"] == "awaiting_input":
            return AgentResult(
                ok=True,
                status="awaiting_input",
                agent_output=outcome.get("question") or outcome.get("output") or "",
                pending_question={
                    "question": outcome.get("question") or "",
                    "choices": [],
                    # The remote task id: answering means sending another
                    # message on this exact task, so without it the run could
                    # only be started again from the top.
                    "key": outcome.get("key") or "",
                    "node": "",
                },
            )
        if outcome["ok"]:
            return AgentResult(ok=True, status="done", agent_output=outcome["output"])
        return AgentResult(ok=False, status="error", error=outcome["error"],
                           agent_output=outcome.get("output") or "")

    def _no_endpoint(self) -> AgentResult:
        return AgentResult(
            ok=False,
            status="error",
            error=(
                f"Remote agent '{self.agent_id}' has no endpoint configured. "
                f"Set its URL on the agent page before running it."
            ),
        )

    def _unreachable(self, exc: Exception, url: str) -> AgentResult:
        return AgentResult(
            ok=False,
            status="error",
            error=f"Could not reach remote agent at {url}: {type(exc).__name__}: {exc}",
        )

    def _map_response(self, status_code: int, payload: Any, text: str) -> AgentResult:
        """Turn the remote's reply into an AgentResult.

        Shared by the sync, async and streaming paths so all three report
        identically — the distinction between them is transport only, never
        semantics.
        """
        if status_code >= 400:
            # Include a bounded slice of the body: remote errors are usually
            # explained there, and truncation keeps a stack trace from flooding
            # the run record.
            return AgentResult(
                ok=False,
                status="error",
                error=f"Remote agent returned HTTP {status_code}: {(text or '').strip()[:2000]}",
            )

        # An A2A reply is a JSON-RPC envelope around a Task, not the hub's own
        # {ok, output} shape, so it is read by its own mapper. Errors above this
        # line are transport-level and read the same either way.
        if self.is_a2a:
            return self._map_a2a(payload)

        return self._map_hub_payload(payload)

    def _map_hub_payload(self, payload: Any) -> AgentResult:
        """Map a payload already in the hub's ``{ok, output, ...}`` shape.

        Split out of :meth:`_map_response` because a *streamed* run reaches this
        point having already been translated into hub frames, whichever protocol
        produced them: its closing frame is hub-shaped even for an A2A agent, so
        it must not be run through the A2A envelope reader a second time.
        """
        # A pause reported without a stream. The streaming path learns this from
        # an ``interrupt`` frame, but streaming needs a listener on this side,
        # and a run nobody is watching must still be able to stop and ask: a
        # graph left suspended while its run reads "completed" is the worst of
        # both, because nothing will ever answer it.
        if isinstance(payload, dict):
            pending = payload.get("interrupt") or payload.get("pending_question")
            if isinstance(pending, dict) and pending:
                question = str(pending.get("question") or "").strip()
                return AgentResult(
                    ok=True,
                    status="awaiting_input",
                    agent_output=question or _extract_output(payload),
                    pending_question={
                        "question": question,
                        "choices": [str(c) for c in (pending.get("choices") or [])],
                        "key": str(pending.get("key") or ""),
                        "node": str(pending.get("node") or ""),
                    },
                    steps=_extract_steps(payload),
                )

        # A remote may report failure in-band with HTTP 200; honour that.
        if isinstance(payload, dict) and payload.get("ok") is False:
            return AgentResult(
                ok=False,
                status="error",
                error=str(payload.get("error") or "remote agent reported failure"),
                steps=_extract_steps(payload),
            )

        output = _extract_output(payload)
        if not output:
            return AgentResult(
                ok=False,
                status="error",
                error=(
                    "Remote agent replied without any output field. Expected JSON with "
                    "an 'output' key; got: " + str(payload)[:500]
                ),
            )

        return AgentResult(
            ok=True,
            status="done",
            agent_output=output,
            steps=_extract_steps(payload),
        )

    @staticmethod
    def _payload_of(resp: Any) -> Any:
        try:
            return resp.json()
        except Exception:
            return resp.text

    def _should_stream(self, callbacks: Any) -> tuple[bool, Optional[Any], List[Any]]:
        """Decide whether this run streams, and to whom.

        Streaming needs both halves: a remote that declares a stream endpoint,
        and a listener on this side. Missing either, the single-shot POST is the
        better call — it is one round trip and the remote must support it anyway.
        """
        emitter = self._resolve_emitter(callbacks)
        sinks = self._usage_sinks(callbacks)
        return (self.supports_streaming and emitter is not None), emitter, sinks

    def run(self, instruction: str, **kwargs) -> AgentResult:
        """Run the prompt on the remote agent, streaming when both sides can.

        Transport failures become a failed ``AgentResult`` rather than an
        exception, matching how ``StandardAgent.run`` reports errors so callers
        need no special case.
        """
        import httpx

        if not self.base_url_remote:
            return self._no_endpoint()

        callbacks = kwargs.get("callbacks")
        streaming, emitter, sinks = self._should_stream(callbacks)
        body, headers = self._request(instruction, kwargs, stream=streaming)

        if streaming:
            state = _StreamState(emitter, sinks, run_id=kwargs.get("run_id"))
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    with client.stream("POST", self.stream_url, json=body, headers=headers) as resp:
                        if resp.status_code >= 400:
                            resp.read()
                            return self._map_response(resp.status_code, None, resp.text)
                        for line in resp.iter_lines():
                            self._feed(state, line)
            except Exception as exc:  # noqa: BLE001
                # Partial output is still worth keeping: a stream that dies
                # halfway has usually already shown the user real work.
                if state.text_parts:
                    return AgentResult(
                        ok=False, status="error", steps=state.steps,
                        agent_output="".join(state.text_parts),
                        error=f"Remote agent stream failed: {type(exc).__name__}: {exc}",
                    )
                return self._unreachable(exc, self.stream_url)
            return self._stream_result(state)

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(self.run_url, json=body, headers=headers)
        except Exception as exc:  # noqa: BLE001
            return self._unreachable(exc, self.run_url)

        return self._map_response(resp.status_code, self._payload_of(resp), resp.text)

    def resume(self, run_id: str, value: Any, *, key: str = "", **kwargs) -> AgentResult:
        """Continue a run this agent paused, with the answer it was waiting for.

        Streams the continuation when both halves are there, exactly like a
        run: what comes back is the agent working, and the same frames describe
        it. A remote that pauses again simply sends another ``interrupt``, and
        the result says ``awaiting_input`` a second time.
        """
        import httpx

        if self.is_a2a:
            return self._resume_a2a(run_id, value, key=key, **kwargs)

        if not self.supports_resume:
            return AgentResult(
                ok=False, status="error",
                error=(
                    f"Agent '{self.agent_id}' declares no resume_path, so a paused run "
                    f"cannot be continued. Add runtime.resume_path to its manifest."
                ),
            )

        callbacks = kwargs.get("callbacks")
        emitter = self._resolve_emitter(callbacks)
        sinks = self._usage_sinks(callbacks)
        headers = {"Content-Type": "application/json", **resolve_auth_header(self.remote)}
        body = {"run_id": run_id, "value": value, "key": key,
                "workspace": kwargs.get("workspace", self.workspace)}

        state = _StreamState(emitter, sinks, run_id=run_id)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream("POST", self.resume_url, json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        resp.read()
                        return self._map_response(resp.status_code, None, resp.text)
                    for line in resp.iter_lines():
                        self._feed(state, line)
        except Exception as exc:  # noqa: BLE001
            if state.text_parts:
                return AgentResult(
                    ok=False, status="error", steps=state.steps,
                    agent_output="".join(state.text_parts),
                    error=f"Remote agent resume failed: {type(exc).__name__}: {exc}",
                )
            return self._unreachable(exc, self.resume_url)
        return self._stream_result(state)

    def _resume_a2a(self, run_id: str, value: Any, *, key: str = "", **kwargs) -> AgentResult:
        """Answer an A2A agent that paused, on the task it paused in.

        There is no separate endpoint: a second ``message/send`` carrying the
        paused task's id *is* the continuation, and the agent picks up whatever
        state it was holding. Without that id the hub would have to start the
        agent over with the answer in its prompt, which is a different run that
        merely reads the same.
        """
        import httpx

        if not self.base_url_remote:
            return self._no_endpoint()
        task_id = str(key or "").strip()
        if not task_id:
            return AgentResult(
                ok=False, status="error",
                error=(
                    f"Agent '{self.agent_id}' paused without recording the remote task id, "
                    f"so the answer has nowhere to go. Run it again instead."
                ),
            )

        from a2a import client as a2a_client

        callbacks = kwargs.get("callbacks")
        emitter = self._resolve_emitter(callbacks)
        sinks = self._usage_sinks(callbacks)
        streaming = self.supports_streaming and emitter is not None
        headers = {"Content-Type": "application/json", **resolve_auth_header(self.remote)}
        body = a2a_client.build_send_request(
            "" if value is None else str(value),
            stream=streaming,
            task_id=task_id,
            run_id=run_id,
            workspace=kwargs.get("workspace", self.workspace),
        )

        if not streaming:
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(self.run_url, json=body, headers=headers)
            except Exception as exc:  # noqa: BLE001
                return self._unreachable(exc, self.run_url)
            return self._map_response(resp.status_code, self._payload_of(resp), resp.text)

        state = _StreamState(emitter, sinks, run_id=run_id)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream("POST", self.stream_url, json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        resp.read()
                        return self._map_response(resp.status_code, None, resp.text)
                    for line in resp.iter_lines():
                        self._feed(state, line)
        except Exception as exc:  # noqa: BLE001
            if state.text_parts:
                return AgentResult(
                    ok=False, status="error", steps=state.steps,
                    agent_output="".join(state.text_parts),
                    error=f"Remote agent resume failed: {type(exc).__name__}: {exc}",
                )
            return self._unreachable(exc, self.stream_url)
        return self._stream_result(state)

    async def arun(self, instruction: str, **kwargs) -> AgentResult:
        """Async counterpart of :meth:`run`.

        Chat and the project routes drive agents with ``await agent.arun(...)``.
        Without this, a remote agent would raise AttributeError in exactly the
        surfaces an operator is most likely to try it from — and running the
        blocking client on the event loop instead would stall every other
        request for the length of the remote's run, which for a code agent is
        minutes.
        """
        import httpx

        if not self.base_url_remote:
            return self._no_endpoint()

        callbacks = kwargs.get("callbacks")
        streaming, emitter, sinks = self._should_stream(callbacks)
        body, headers = self._request(instruction, kwargs, stream=streaming)

        if streaming:
            state = _StreamState(emitter, sinks, run_id=kwargs.get("run_id"))
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    async with client.stream("POST", self.stream_url, json=body, headers=headers) as resp:
                        if resp.status_code >= 400:
                            await resp.aread()
                            return self._map_response(resp.status_code, None, resp.text)
                        async for line in resp.aiter_lines():
                            self._feed(state, line)
            except Exception as exc:  # noqa: BLE001
                if state.text_parts:
                    return AgentResult(
                        ok=False, status="error", steps=state.steps,
                        agent_output="".join(state.text_parts),
                        error=f"Remote agent stream failed: {type(exc).__name__}: {exc}",
                    )
                return self._unreachable(exc, self.stream_url)
            return self._stream_result(state)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.run_url, json=body, headers=headers)
        except Exception as exc:  # noqa: BLE001
            return self._unreachable(exc, self.run_url)

        return self._map_response(resp.status_code, self._payload_of(resp), resp.text)


__all__ = [
    "RemoteAgent",
    "RemoteAgentError",
    "normalize_base_url",
    "normalize_topology",
    "resolve_auth_header",
    "DEFAULT_RUN_PATH",
    "DEFAULT_HEALTH_PATH",
    "DEFAULT_TIMEOUT",
    "SUGGESTED_STREAM_PATH",
    "SUGGESTED_GRAPH_PATH",
    "SUGGESTED_RESUME_PATH",
]
