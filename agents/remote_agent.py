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
        {"type": "done",       "ok": true, "output": "...", "error": null}

Those frames are translated onto the vocabulary the chat UI already renders and
pushed through the same emitter a delegated local agent uses
(``ChatStreamCallback.emit_external`` / :mod:`common.stream_sink`), so a remote
agent's tokens appear in the chat bubble exactly like a built-in agent's. The
``usage`` frame is credited to the run's token counters — the remote is the only
party that can know them, since the model call happens in its process.

Streaming is used only when both halves are present: the remote declared the
endpoint *and* something on this side is listening. Otherwise the single-shot
POST runs, which is one round trip and is all the contract requires.

Two limits remain, and they follow from the process boundary:

* Without a ``usage`` frame there is no token or cost accounting. LangChain
  callbacks observe nothing here, so a remote that reports no usage leaves the
  cost columns at zero.
* Tools listed on the registry record are informational for a remote agent: the
  hub does not supply them, the remote's own tool layer does.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from agents.agent_base import AgentBase, AgentResult, ToolResult


# Defaults for descriptor keys a manifest may omit.
DEFAULT_RUN_PATH = "/run"
DEFAULT_HEALTH_PATH = "/health"
# Suggested path for the optional streaming endpoint. Only a manifest that
# declares ``runtime.stream_path`` enables streaming — there is no default,
# because probing a path an agent never implemented would cost a failed request
# on every run.
SUGGESTED_STREAM_PATH = "/run/stream"
DEFAULT_TIMEOUT = 600
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


class _StreamState:
    """Accumulator for one streamed run: the text so far, steps, and usage.

    Holds the emitter and the usage sinks so ``_handle_stream_event`` stays a
    pure translation step, and so a broken listener can never abort a run that
    is otherwise progressing fine.
    """

    def __init__(self, emitter: Optional[Any], usage_sinks: List[Any]):
        self._emitter = emitter
        self._usage_sinks = usage_sinks
        self.text_parts: List[str] = []
        self.steps: List[ToolResult] = []
        self.done: Optional[Dict[str, Any]] = None
        self.pending_tool: Optional[str] = None
        self.step = 0

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

    def credit_usage(self, usage: Dict[str, Any]) -> None:
        """Add remote-reported token usage to the run's counters.

        The remote is the only party that can know these numbers — the model
        call happened in its process — so when it reports them they are credited
        exactly like a local call's, and the run's cost stops reading as zero.
        """
        try:
            prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        except (TypeError, ValueError):
            return
        try:
            total = int(usage.get("total_tokens") or (prompt + completion))
        except (TypeError, ValueError):
            total = prompt + completion
        if not (prompt or completion or total):
            return
        # A remote that reports cache reads gets them credited too, so its runs
        # are priced on the same split as local ones.
        from agents.callbacks.run_statistics import cached_input_tokens
        cached = min(cached_input_tokens(usage), prompt)
        for sink in self._usage_sinks:
            try:
                sink.prompt_tokens += prompt
                sink.completion_tokens += completion
                sink.total_tokens += total
                sink.cached_prompt_tokens = getattr(sink, "cached_prompt_tokens", 0) + cached
            except Exception:
                continue
        self.emit({
            "type": "usage",
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "cached_tokens": cached,
            "remote": True,
        })


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
    def run_url(self) -> str:
        path = self.remote.get("run_path") or DEFAULT_RUN_PATH
        return f"{self.base_url_remote}{path if path.startswith('/') else '/' + path}"

    @property
    def health_url(self) -> str:
        path = self.remote.get("health_path") or DEFAULT_HEALTH_PATH
        return f"{self.base_url_remote}{path if path.startswith('/') else '/' + path}"

    @property
    def stream_url(self) -> str:
        path = self.remote.get("stream_path") or ""
        if not path:
            return ""
        return f"{self.base_url_remote}{path if path.startswith('/') else '/' + path}"

    @property
    def supports_streaming(self) -> bool:
        """Whether the remote declared a streaming endpoint.

        Opt-in by design: ``/run`` is the one endpoint the contract requires, so
        an agent that never streams stays trivially importable. Declaring
        ``runtime.stream_path`` is what turns streaming on.
        """
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
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(self.health_url, headers=resolve_auth_header(self.remote))
            if resp.status_code < 400:
                return {"ok": True, "detail": f"HTTP {resp.status_code}"}
            return {"ok": False, "detail": f"HTTP {resp.status_code}"}
        except Exception as exc:  # noqa: BLE001 - any transport error is "not reachable"
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

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

    def _handle_stream_event(self, event: Dict[str, Any], state: "_StreamState") -> None:
        """Translate one event from the remote onto this hub's event vocabulary.

        The remote speaks a small, stable vocabulary (token / thinking /
        tool_start / tool_end / tool_error / usage / done). Mapping happens here
        rather than in the remote so an imported agent never has to know how
        this hub's UI names things.
        """
        kind = str(event.get("type") or "").strip()

        if kind == "token":
            token = event.get("token")
            if isinstance(token, str) and token:
                state.text_parts.append(token)
                state.emit({"type": "token", "token": token})
            return

        if kind in ("thinking", "progress", "log"):
            message = str(event.get("message") or event.get("text") or "").strip()
            if message:
                state.emit({"type": "thinking", "message": message})
            return

        if kind == "tool_start":
            state.step += 1
            name = str(event.get("name") or event.get("tool") or "tool")
            state.pending_tool = name
            state.emit({
                "type": "tool_start",
                "step": state.step,
                "tool": name,
                "input": str(event.get("input") if event.get("input") is not None else ""),
            })
            return

        if kind == "tool_end":
            name = str(event.get("name") or event.get("tool") or state.pending_tool or "")
            output = str(event.get("output") if event.get("output") is not None else "")
            args = event.get("args") if isinstance(event.get("args"), dict) else {}
            state.steps.append(ToolResult(name=name, args=args, output=output))
            state.pending_tool = None
            state.emit({"type": "tool_end", "output": output})
            return

        if kind == "tool_error":
            name = str(event.get("name") or event.get("tool") or state.pending_tool or "")
            state.pending_tool = None
            state.emit({"type": "tool_error", "tool": name, "error": str(event.get("error") or "")})
            return

        if kind == "usage":
            state.credit_usage(event)
            return

        if kind in ("done", "final", "result"):
            state.done = event
            if isinstance(event.get("usage"), dict):
                state.credit_usage(event["usage"])
            return

        if kind == "error":
            state.done = {"ok": False, "error": str(event.get("error") or "remote agent reported an error")}
            return

    def _stream_result(self, state: "_StreamState") -> AgentResult:
        """Turn a completed stream into an AgentResult.

        A remote that ends its stream without a ``done`` frame is not treated as
        a failure: the tokens it already sent are the answer. Only a stream that
        produced neither is an error, and it says so with the endpoint named.
        """
        if state.done is not None:
            payload = dict(state.done)
            payload.setdefault("steps", [s.model_dump() for s in state.steps])
            if not _extract_output(payload) and state.text_parts:
                payload["output"] = "".join(state.text_parts)
            result = self._map_response(200, payload, "")
            # Steps collected live are richer than whatever the done frame
            # repeats, so keep them when the frame carried none.
            if not result.steps and state.steps:
                result.steps = state.steps
            return result

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

    def _request(self, instruction: str, kwargs: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, str]]:
        """Build the JSON body and headers for one run request."""
        body: Dict[str, Any] = {
            "prompt": instruction,
            "run_id": kwargs.get("run_id"),
            "workspace": kwargs.get("workspace", self.workspace),
        }
        headers = {"Content-Type": "application/json", **resolve_auth_header(self.remote)}
        return body, headers

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
        body, headers = self._request(instruction, kwargs)

        if streaming:
            state = _StreamState(emitter, sinks)
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    with client.stream("POST", self.stream_url, json=body, headers=headers) as resp:
                        if resp.status_code >= 400:
                            resp.read()
                            return self._map_response(resp.status_code, None, resp.text)
                        for line in resp.iter_lines():
                            event = _parse_stream_line(line)
                            if event is not None:
                                self._handle_stream_event(event, state)
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
        body, headers = self._request(instruction, kwargs)

        if streaming:
            state = _StreamState(emitter, sinks)
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    async with client.stream("POST", self.stream_url, json=body, headers=headers) as resp:
                        if resp.status_code >= 400:
                            await resp.aread()
                            return self._map_response(resp.status_code, None, resp.text)
                        async for line in resp.aiter_lines():
                            event = _parse_stream_line(line)
                            if event is not None:
                                self._handle_stream_event(event, state)
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
    "resolve_auth_header",
    "DEFAULT_RUN_PATH",
    "DEFAULT_HEALTH_PATH",
    "DEFAULT_TIMEOUT",
    "SUGGESTED_STREAM_PATH",
]
