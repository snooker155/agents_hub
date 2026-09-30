"""The hub as an A2A client: the wire format, and RemoteAgent speaking it.

The pure half (:mod:`a2a.client`) is tested against recorded JSON, because the
cases that matter are the ones a live agent is least likely to produce on
demand: a task that completed with its answer only in the status message, one
that stopped to ask, a JSON-RPC error. The transport half is then tested against
a stub A2A server, which is what proves the two halves are wired together.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from a2a import client as a2a_client
from agents.remote_agent import RemoteAgent


def _task(state, *, task_id="task-1", artifacts=(), message=""):
    """A Task object as an A2A agent would return it."""
    status = {"state": state, "timestamp": "2026-01-01T00:00:00Z"}
    if message:
        status["message"] = {"role": "agent", "parts": [{"kind": "text", "text": message}],
                             "messageId": "m", "kind": "message"}
    return {
        "id": task_id,
        "contextId": "ctx-1",
        "kind": "task",
        "status": status,
        "artifacts": [{"artifactId": "a", "name": "result",
                       "parts": [{"kind": "text", "text": text}]} for text in artifacts],
    }


def _reply(result, request_id="1"):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


# ── the request ─────────────────────────────────────────────────────────────

def test_a_prompt_becomes_one_user_message():
    request = a2a_client.build_send_request("fix the tests", run_id="r-1", workspace="ws")
    assert request["jsonrpc"] == "2.0"
    assert request["method"] == "message/send"
    message = request["params"]["message"]
    assert message["role"] == "user"
    assert message["parts"] == [{"kind": "text", "text": "fix the tests"}]
    assert message["messageId"]
    # The run and workspace travel in metadata, where the spec leaves room for
    # the application: a remote that logs alongside this hub can join them up.
    assert request["params"]["metadata"] == {"run_id": "r-1", "workspace": "ws"}
    assert request["params"]["configuration"]["blocking"] is True


def test_a_streamed_request_asks_for_the_stream_and_does_not_block():
    request = a2a_client.build_send_request("go", stream=True)
    assert request["method"] == "message/stream"
    assert "configuration" not in request["params"]


def test_an_answer_is_sent_on_the_task_that_asked():
    """Resume is not a second endpoint in A2A: it is the same method carrying
    the task id, which is what lets the agent continue instead of restarting."""
    request = a2a_client.build_send_request("staging", task_id="task-9")
    assert request["params"]["message"]["taskId"] == "task-9"


# ── the response ────────────────────────────────────────────────────────────

def test_a_completed_task_yields_its_artifacts_as_the_output():
    outcome = a2a_client.outcome_from_response(
        _reply(_task("completed", artifacts=["patched api/users.py"], message="done"))
    )
    assert outcome == {"ok": True, "status": "done", "output": "patched api/users.py",
                       "error": "", "key": "task-1"}


def test_a_completed_task_without_artifacts_falls_back_to_the_status_message():
    """Plenty of agents answer a short question with a message and never produce
    an artifact; treating those as empty would throw the whole reply away."""
    outcome = a2a_client.outcome_from_response(
        _reply(_task("completed", message="42"))
    )
    assert outcome["ok"] is True and outcome["output"] == "42"


def test_a_bare_message_reply_is_a_finished_run():
    outcome = a2a_client.outcome_from_response(_reply({
        "kind": "message", "role": "agent", "messageId": "m1",
        "parts": [{"kind": "text", "text": "hello"}],
    }))
    assert outcome["ok"] is True and outcome["output"] == "hello"


def test_input_required_becomes_awaiting_input_carrying_the_task_id():
    outcome = a2a_client.outcome_from_response(
        _reply(_task("input-required", task_id="task-7", message="Which environment?"))
    )
    assert outcome["status"] == "awaiting_input"
    assert outcome["question"] == "Which environment?"
    # Without the task id the answer has nowhere to go.
    assert outcome["key"] == "task-7"


@pytest.mark.parametrize("state", ["failed", "rejected", "canceled"])
def test_a_task_that_ended_badly_is_a_failed_run(state):
    outcome = a2a_client.outcome_from_response(_reply(_task(state, message="no model key")))
    assert outcome["ok"] is False
    assert state in outcome["error"] and "no model key" in outcome["error"]


def test_a_jsonrpc_error_is_reported_with_its_code():
    outcome = a2a_client.outcome_from_response({
        "jsonrpc": "2.0", "id": "1",
        "error": {"code": -32001, "message": "Task not found"},
    })
    assert outcome["ok"] is False
    assert "-32001" in outcome["error"] and "Task not found" in outcome["error"]


def test_a_task_left_working_says_so_rather_than_returning_nothing():
    """A run of this hub is synchronous. An agent that accepted the work and
    returned leaves nothing to report, and the run must say why."""
    outcome = a2a_client.outcome_from_response(_reply(_task("working")))
    assert outcome["ok"] is False
    assert "working" in outcome["error"] and "tasks/get" in outcome["error"]


def test_a_reply_that_is_not_jsonrpc_is_an_error_not_a_crash():
    assert a2a_client.outcome_from_response("<html>502</html>")["ok"] is False
    assert a2a_client.outcome_from_response({"jsonrpc": "2.0", "id": 1})["ok"] is False


# ── the stream ──────────────────────────────────────────────────────────────

def _status_frame(state, text="", final=False, task_id="task-1"):
    status = {"state": state, "timestamp": "2026-01-01T00:00:00Z"}
    if text:
        status["message"] = {"role": "agent", "kind": "message", "messageId": "m",
                             "parts": [{"kind": "text", "text": text}]}
    return _reply({"taskId": task_id, "contextId": "ctx", "kind": "status-update",
                   "status": status, "final": final})


def test_working_updates_become_tokens():
    frames = a2a_client.frames_from_stream_event(_status_frame("working", "reading "))
    assert frames == [{"type": "token", "token": "reading "}]


def test_a_completed_update_closes_the_run():
    frames = a2a_client.frames_from_stream_event(_status_frame("completed", "all done", final=True))
    assert frames == [{"type": "done", "ok": True, "output": "all done", "error": None}]


def test_an_artifact_is_held_back_rather_than_printed_twice():
    """The artifact repeats what the status updates already streamed, so it is
    kept as the output instead of emitted as more tokens."""
    frames = a2a_client.frames_from_stream_event(_reply({
        "taskId": "task-1", "contextId": "ctx", "kind": "artifact-update",
        "artifact": {"artifactId": "a", "parts": [{"kind": "text", "text": "the answer"}]},
    }))
    assert frames == [{"type": "a2a_artifact", "text": "the answer"}]


def test_a_streamed_pause_becomes_an_interrupt():
    frames = a2a_client.frames_from_stream_event(
        _status_frame("input-required", "Which branch?", final=True, task_id="task-3")
    )
    assert frames == [{"type": "interrupt", "question": "Which branch?",
                       "choices": [], "key": "task-3"}]


def test_an_unreadable_stream_frame_yields_nothing():
    assert a2a_client.frames_from_stream_event(_reply({"kind": "task"})) == []
    assert a2a_client.frames_from_stream_event({"not": "jsonrpc"})[0]["ok"] is False


# ── RemoteAgent over the wire ───────────────────────────────────────────────

class _A2AHandler(BaseHTTPRequestHandler):
    """A stub A2A agent: one JSON-RPC endpoint, answers set per test."""

    reply = None                 # the `result` to return from message/send
    stream_events = []           # the `result` of each SSE frame
    received = []                # request bodies, so a test can check what we sent

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.endswith("agent-card.json"):
            self._send(200, {
                "protocolVersion": "0.3.0",
                "name": "Stub A2A Agent",
                "description": "A stub that speaks A2A.",
                "url": f"http://127.0.0.1:{self.server.server_port}",
                "version": "2",
                "capabilities": {"streaming": True},
                "skills": [{"id": "answer", "name": "Answer", "tags": ["test"]}],
            })
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).received.append(body)
        if body.get("method") == "message/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event in type(self).stream_events:
                frame = {"jsonrpc": "2.0", "id": body.get("id"), "result": event}
                self.wfile.write(("data: " + json.dumps(frame) + "\n\n").encode())
                self.wfile.flush()
            return
        self._send(200, {"jsonrpc": "2.0", "id": body.get("id"), "result": type(self).reply})

    def _send(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def stub_a2a():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _A2AHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _A2AHandler.reply = _task("completed", artifacts=["patched api/users.py"])
    _A2AHandler.stream_events = []
    _A2AHandler.received = []
    try:
        yield f"http://127.0.0.1:{server.server_port}", _A2AHandler
    finally:
        server.shutdown()
        server.server_close()


class _RecordingEmitter:
    """Stands in for ChatStreamCallback, as the imported-agent tests do."""

    def __init__(self):
        self.events = []
        self.prompt_tokens = self.completion_tokens = self.total_tokens = 0

    def emit_external(self, payload):
        self.events.append(payload)

    def tokens(self):
        return "".join(e["token"] for e in self.events if e.get("type") == "token")


def _agent(url, **extra):
    return RemoteAgent(agent_id="stub", name="Stub",
                       remote={"kind": "a2a", "url": url, "run_path": "", **extra})


def test_a_run_posts_jsonrpc_to_the_single_endpoint(stub_a2a):
    url, handler = stub_a2a
    result = _agent(url).run("fix it", run_id="r-1")

    assert result.ok is True and result.agent_output == "patched api/users.py"
    sent = handler.received[-1]
    assert sent["method"] == "message/send"
    assert sent["params"]["message"]["parts"][0]["text"] == "fix it"
    assert sent["params"]["metadata"]["run_id"] == "r-1"


def test_a_paused_a2a_agent_parks_the_run_with_its_task_id(stub_a2a):
    url, handler = stub_a2a
    handler.reply = _task("input-required", task_id="task-7", message="Which environment?")

    result = _agent(url).run("deploy")

    assert result.status == "awaiting_input"
    assert result.pending_question["question"] == "Which environment?"
    assert result.pending_question["key"] == "task-7"


def test_resume_answers_on_the_same_task(stub_a2a):
    url, handler = stub_a2a
    handler.reply = _task("completed", artifacts=["deployed to staging"])

    result = _agent(url).resume("run-1", "staging", key="task-7")

    assert result.ok is True and result.agent_output == "deployed to staging"
    sent = handler.received[-1]
    assert sent["params"]["message"]["taskId"] == "task-7"
    assert sent["params"]["message"]["parts"][0]["text"] == "staging"


def test_resume_without_a_recorded_task_id_explains_itself(stub_a2a):
    url, _ = stub_a2a
    result = _agent(url).resume("run-1", "staging", key="")
    assert result.ok is False and "remote task id" in result.error


def test_an_a2a_run_streams_when_the_card_declared_it(stub_a2a):
    url, handler = stub_a2a
    handler.stream_events = [
        {"taskId": "t", "contextId": "c", "kind": "status-update",
         "status": {"state": "working", "message": {"role": "agent", "kind": "message",
                                                    "messageId": "m",
                                                    "parts": [{"kind": "text", "text": "reading "}]}},
         "final": False},
        {"taskId": "t", "contextId": "c", "kind": "status-update",
         "status": {"state": "working", "message": {"role": "agent", "kind": "message",
                                                    "messageId": "m2",
                                                    "parts": [{"kind": "text", "text": "api/users.py"}]}},
         "final": False},
        {"taskId": "t", "contextId": "c", "kind": "artifact-update",
         "artifact": {"artifactId": "a", "parts": [{"kind": "text", "text": "patched api/users.py"}]}},
        {"taskId": "t", "contextId": "c", "kind": "status-update",
         "status": {"state": "completed"}, "final": True},
    ]
    listener = _RecordingEmitter()

    result = _agent(url, streaming=True).run("go", callbacks=[listener])

    assert listener.tokens() == "reading api/users.py"
    # The artifact is the answer even though the closing update carried no text.
    assert result.ok is True and result.agent_output == "patched api/users.py"
    assert handler.received[-1]["method"] == "message/stream"


def test_an_agent_whose_card_declares_no_streaming_sends_one_message(stub_a2a):
    url, handler = stub_a2a
    listener = _RecordingEmitter()

    result = _agent(url).run("go", callbacks=[listener])

    assert result.ok is True
    assert handler.received[-1]["method"] == "message/send"
    assert listener.events == []


def test_health_for_an_a2a_agent_is_its_card(stub_a2a):
    url, _ = stub_a2a
    agent = _agent(url, card_url=f"{url}/.well-known/agent-card.json")
    assert agent.check_health()["ok"] is True

    assert _agent(url).check_health()["ok"] is False, "no card URL, nothing to probe"


def test_the_http_runtime_is_untouched_by_the_a2a_branch():
    """The two runtimes share one class, so the boundary is worth asserting."""
    http_agent = RemoteAgent(agent_id="x", name="X",
                             remote={"url": "http://h", "run_path": "/run",
                                     "stream_path": "/run/stream"})
    assert http_agent.is_a2a is False
    assert http_agent.run_url == "http://h/run"
    assert http_agent.stream_url == "http://h/run/stream"
    assert http_agent.supports_resume is False
