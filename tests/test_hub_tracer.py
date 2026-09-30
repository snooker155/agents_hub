"""The tracer a customer installs beside their own graph.

Two things are being tested, and the second matters more than the first.

The first is that a LangGraph run reported through the tracer lands in this hub
as an ordinary run: the path through the graph, the tool trail, the tokens, the
answer. That is the feature.

The second is that the tracer cannot hurt the process it runs in. It is
installed in somebody's production service to produce a dashboard, and a
monitoring handler that raises, blocks or grows without bound is worse than no
dashboard at all. Those tests are the ones to keep if the others ever become
inconvenient.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_CLIENT_DIR = Path(__file__).resolve().parents[1] / "clients" / "agents-hub-langgraph"
if str(_CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(_CLIENT_DIR))

from agents_hub_langgraph import HubTracer  # noqa: E402
from connections import service as ingest_service  # noqa: E402
from connections import store as connection_store  # noqa: E402
from managers import run_manager  # noqa: E402

_EXAMPLE_DIR = (Path(__file__).resolve().parents[1]
                / "examples" / "imported-agents" / "langgraph-agenthub")


@pytest.fixture(autouse=True)
def isolated_connections():
    """No in-memory state carried between tests.

    The connections themselves live in the database, and the autouse
    ``fresh_db`` fixture already gives every test its own empty one.
    """
    ingest_service.reset_for_tests()
    yield
    ingest_service.reset_for_tests()


class _RoutedTransport:
    """Sends the tracer's requests into the real ingest routes, in-process.

    The transport is replaceable precisely so this is possible: the tracer's own
    batching, threading and error handling are exercised against the actual API,
    with no socket and no server to start.
    """

    def __init__(self, client, token):
        self.client = client
        self.token = token
        self.calls = []

    def post(self, path, payload):
        self.calls.append(path)
        response = self.client.post(
            path, json=payload, headers={"Authorization": f"Bearer {self.token}"})
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
        return response.json()

    def get(self, path):
        self.calls.append(path)
        response = self.client.get(path, headers={"Authorization": f"Bearer {self.token}"})
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
        return response.json()


class _BrokenTransport:
    """A hub that is down, or a token that was revoked."""

    def __init__(self):
        self.attempts = 0

    def post(self, path, payload):
        self.attempts += 1
        raise ConnectionError("hub unreachable")


@pytest.fixture
def api_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from routes import connections as connections_routes
    from routes import ingest as ingest_routes

    app = FastAPI()
    app.include_router(connections_routes.router)
    app.include_router(ingest_routes.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def close_tracers(monkeypatch):
    """Stop every tracer a test creates before the next test starts.

    The worker thread is a daemon that lingers for half a minute after its last
    event, which is right for a long-running service and wrong inside a test
    suite: the thread outlives the test, and the database it writes to has been
    swapped underneath it by then. Tests that leak threads fail somewhere else,
    intermittently, which is the worst kind of failure to chase.
    """
    created = []
    original = HubTracer.__init__

    def _tracked(self, *args, **kwargs):
        original(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(HubTracer, "__init__", _tracked)
    yield
    for tracer in created:
        try:
            tracer.close(timeout=2)
        except Exception:
            pass


@pytest.fixture
def transport(api_client):
    token = api_client.post("/api/connections", json={
        "id": "billing-graph", "name": "Billing graph", "kind": "langgraph"}).json()["token"]
    return _RoutedTransport(api_client, token)


# ── a real graph, reported end to end ───────────────────────────────────────

def _demo_graph():
    pytest.importorskip("langgraph", reason="the bundled example graph needs langgraph")
    if str(_EXAMPLE_DIR) not in sys.path:
        sys.path.insert(0, str(_EXAMPLE_DIR))
    import demo_graph

    return demo_graph.graph


def test_a_real_graph_run_lands_as_an_ordinary_run(transport):
    """The feature, end to end: LangGraph callbacks in, a hub run record out."""
    graph = _demo_graph()
    tracer = HubTracer(transport=transport, title="Billing")

    graph.invoke({"messages": [("user", "what does the team plan cost?")]},
                 config={"callbacks": [tracer]})
    assert tracer.flush(timeout=10)

    run_id = [c for c in transport.calls if c.endswith("/close")][0].split("/")[-2]
    record = run_manager.get_runs_by_ids([run_id])[run_id]
    payload = run_manager.get_run_process(run_id)

    assert record["status"] == "completed"
    assert record["agent_id"] == "billing-graph"
    assert record["output"], "the run recorded no answer"
    assert [c["tool"] for c in payload["tool_calls"]] == ["lookup_pricing"]
    # The branch the run took is the question a conditional edge raises. It is
    # kept on the run record, not in the payload, so a list of runs can show it.
    assert record["graph_path"] == ["triage", "pricing", "answer"]


def test_the_other_branch_is_recorded_as_the_other_branch(transport):
    graph = _demo_graph()
    tracer = HubTracer(transport=transport)

    graph.invoke({"messages": [("user", "just say hello")]}, config={"callbacks": [tracer]})
    assert tracer.flush(timeout=10)

    run_id = [c for c in transport.calls if c.endswith("/close")][0].split("/")[-2]
    record = run_manager.get_runs_by_ids([run_id])[run_id]
    assert record["graph_path"] == ["triage", "answer"]


def test_a_graph_reports_its_own_shape(transport):
    """Nothing here can go and ask for it, so the graph has to send it."""
    graph = _demo_graph()
    tracer = HubTracer(transport=transport)

    tracer.report_graph(graph)
    assert tracer.flush(timeout=10)

    topology = connection_store.get_connection("billing-graph")["topology"]
    assert [n["id"] for n in topology["nodes"]] == [
        "__start__", "triage", "pricing", "answer", "__end__"]
    assert any(e["conditional"] for e in topology["edges"])


# ── it must not hurt the process it runs in ─────────────────────────────────

def test_a_hub_that_is_down_does_not_stop_the_graph(transport):
    """The rule that outranks every feature in this package."""
    graph = _demo_graph()
    broken = _BrokenTransport()
    seen = []
    tracer = HubTracer(transport=broken, on_error=seen.append)

    state = graph.invoke({"messages": [("user", "hello")]}, config={"callbacks": [tracer]})
    tracer.flush(timeout=2)

    assert state["messages"], "the graph's own result was affected by the tracer"
    assert broken.attempts > 0, "it did try"
    assert seen, "failures were not reported to on_error"


def test_swallowed_failures_are_counted_where_anyone_can_read_them(transport):
    """Silence is the rule; a number is how you find out anyway."""
    tracer = HubTracer(transport=_BrokenTransport())
    assert tracer.errors == 0

    tracer.report_graph(_demo_graph())
    tracer.flush(timeout=2)

    assert tracer.errors > 0, "nothing said so, and nothing counted it either"


def test_the_first_failure_says_so_once(transport, caplog):
    """A mistyped token must not look exactly like a working setup."""
    from agents_hub_langgraph import client as client_module

    client_module.reset_warning()
    with caplog.at_level("WARNING", logger="agents_hub_langgraph"):
        first = HubTracer(transport=_BrokenTransport())
        first.report_graph(_demo_graph())
        first.flush(timeout=2)
        second = HubTracer(transport=_BrokenTransport())
        second.report_graph(_demo_graph())
        second.flush(timeout=2)

    warnings = [r for r in caplog.records if r.name == "agents_hub_langgraph"]
    # Once per process, not per tracer: the recommended usage is one tracer per
    # invocation, so per-tracer would mean a line per run while a hub is down.
    assert len(warnings) == 1
    assert "on_error" in warnings[0].getMessage()


def test_a_tracer_told_where_to_report_errors_stays_silent(transport, caplog):
    """Passing on_error is choosing the channel, which the warning must respect."""
    from agents_hub_langgraph import client as client_module

    client_module.reset_warning()
    seen = []
    with caplog.at_level("WARNING", logger="agents_hub_langgraph"):
        tracer = HubTracer(transport=_BrokenTransport(), on_error=seen.append)
        tracer.report_graph(_demo_graph())
        tracer.flush(timeout=2)

    assert seen, "the failures went nowhere at all"
    assert [r for r in caplog.records if r.name == "agents_hub_langgraph"] == []


def test_a_callback_that_is_handed_nonsense_does_not_raise(transport):
    """LangChain calls these directly; none of them may throw back into it."""
    tracer = HubTracer(transport=transport)

    tracer.on_chain_start(None, None, run_id="r", parent_run_id=None)
    tracer.on_llm_new_token(None)
    tracer.on_llm_end(object())
    tracer.on_tool_start(None, None, run_id="t")
    tracer.on_tool_end(object(), run_id="t")
    tracer.on_tool_error(ValueError("x"), run_id="t")
    tracer.on_chain_end(None, run_id="r", parent_run_id=None)
    # Reaching here without an exception is the assertion.


def test_many_tracers_do_not_leak_a_thread_or_an_exit_handler_each(transport):
    """One tracer per invocation is the recommended usage, so a server creates
    them by the thousand. A worker thread or an atexit callback per tracer would
    be a leak that only shows up in production."""
    import threading

    from agents_hub_langgraph import tracer as tracer_module

    before_threads = threading.active_count()
    tracers = [HubTracer(transport=transport) for _ in range(50)]

    assert threading.active_count() == before_threads, (
        "constructing a tracer started a thread before it had anything to send")
    assert len(tracer_module._LIVE_TRACERS) >= 50
    # One handler for all of them, not one each.
    assert tracer_module._ATEXIT_REGISTERED is True

    tracers.clear()


def test_an_idle_worker_ends_itself_and_comes_back_when_needed(transport):
    import threading
    import time

    from agents_hub_langgraph.client import HubClient

    client = HubClient(transport, flush_interval=0.02, idle_exit=0.05)
    client.emit({"type": "token", "token": "x"})
    assert client.flush(timeout=2)

    deadline = time.time() + 3
    while time.time() < deadline and client._worker is not None:
        time.sleep(0.05)
    assert client._worker is None, "the worker never ended while idle"

    client.emit({"type": "token", "token": "y"})
    assert client._worker is not None and client._worker.is_alive(), (
        "the worker did not come back for new work")
    assert isinstance(client._worker, threading.Thread)


def test_closing_a_tracer_sends_what_is_left_and_ends_its_thread(transport):
    """A process that is about to exit, and a test that must not leak a thread
    still writing after it finished, need the same thing."""
    tracer = HubTracer(transport=transport)
    tracer.on_chain_start({}, {"messages": []}, run_id="root", parent_run_id=None)
    tracer.on_chain_end({"messages": []}, run_id="root", parent_run_id=None)

    assert tracer.close(timeout=5) is True
    # This client's own worker, rather than a count of every thread in the
    # process: the suite runs other tracers, and a global count would make this
    # test fail for somebody else's reasons.
    assert tracer._client._worker is None, "the worker thread outlived close()"
    assert any(c.endswith("/close") for c in transport.calls), "queued work was dropped"


def test_the_queue_is_bounded_when_nothing_is_draining_it():
    """A monitoring buffer must not be able to eat a production process."""
    from agents_hub_langgraph.client import HubClient

    client = HubClient(_BrokenTransport(), flush_interval=5, queue_size=10)
    for i in range(200):
        client.emit({"type": "token", "token": str(i)})

    assert client._queue.qsize() <= 10
    assert client.dropped > 0


def test_frames_for_a_run_that_never_opened_are_dropped_not_hoarded():
    """The hub was down when the run started, so nothing can ever send these.

    The queue is bounded, but the worker's batch buffer is a separate list, and
    it is the one that fills when a run fails to open.
    """
    from agents_hub_langgraph.client import MAX_PENDING_WITHOUT_RUN, HubClient

    client = HubClient(_BrokenTransport(), flush_interval=0.01, idle_exit=0.05)
    client.open({"input": "go"})
    for i in range(MAX_PENDING_WITHOUT_RUN + 200):
        client.emit({"type": "token", "token": str(i)})

    assert client.flush(timeout=5)
    assert client.run_id is None, "the open failed, as this test intends"
    assert client.dropped > 0


def test_the_default_transport_speaks_real_http(transport):
    """Everything else here injects a transport, so the shipped one needs a test.

    A real socket, because what is being checked is the part no fake covers: the
    URL it builds, the bearer header it sends, the JSON it writes and the JSON
    it reads back.
    """
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from agents_hub_langgraph.client import Transport

    seen = {}

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            seen["path"] = self.path
            seen["auth"] = self.headers.get("Authorization")
            seen["body"] = json.loads(self.rfile.read(length) or b"{}")
            raw = json.dumps({"run_id": "ing-abc", "session_id": "s1"}).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = Transport(f"http://127.0.0.1:{server.server_port}/", "ahc_secret")
        answer = client.post("/api/ingest/runs", {"input": "go"})
    finally:
        server.shutdown()
        server.server_close()

    assert answer == {"run_id": "ing-abc", "session_id": "s1"}
    assert seen["path"] == "/api/ingest/runs", "a trailing slash on the base url doubled up"
    assert seen["auth"] == "Bearer ahc_secret"
    assert seen["body"] == {"input": "go"}


# ── the bookkeeping that names are not enough for ───────────────────────────

def test_a_node_entered_twice_closes_the_right_entry(transport):
    """LangChain gives a chain end no name, only the id it started with.

    Matching node ends by name would close the first entry of a looping node and
    leave every later pass open. This drives the callbacks the way LangGraph
    does, with ids and no names on the ends.
    """
    frames = []
    tracer = HubTracer(transport=transport)
    tracer._emit = frames.append

    tracer.on_chain_start({}, {"messages": []}, run_id="root", parent_run_id=None)
    tracer.on_chain_start({}, {}, run_id="a1", parent_run_id="root",
                          metadata={"langgraph_node": "critic"}, name="critic")
    tracer.on_chain_start({}, {}, run_id="a2", parent_run_id="a1",
                          metadata={"langgraph_node": "critic"}, name="inner")
    tracer.on_chain_end({}, run_id="a2", parent_run_id="a1")
    tracer.on_chain_end({}, run_id="a1", parent_run_id="root")

    kinds = [(f["type"], f.get("node")) for f in frames]
    # The inner chain is not a node: it carries the node's metadata but is the
    # node's internals, and drawing it would invent a node the graph has not.
    assert kinds == [("node_start", "critic"), ("node_end", "critic")]


def test_a_subgraphs_nodes_are_reported_with_their_depth(transport):
    frames = []
    tracer = HubTracer(transport=transport)
    tracer._emit = frames.append

    tracer.on_chain_start({}, {}, run_id="root", parent_run_id=None)
    tracer.on_chain_start({}, {}, run_id="outer", parent_run_id="root",
                          metadata={"langgraph_node": "outer"}, name="outer")
    tracer.on_chain_start({}, {}, run_id="inner", parent_run_id="outer",
                          metadata={"langgraph_node": "inner"}, name="inner")

    depths = [(f["node"], f["depth"]) for f in frames if f["type"] == "node_start"]
    assert depths == [("outer", 0), ("inner", 1)]


def test_a_failing_node_is_reported_as_failed(transport):
    frames = []
    tracer = HubTracer(transport=transport)
    tracer._emit = frames.append

    tracer.on_chain_start({}, {}, run_id="root", parent_run_id=None)
    tracer.on_chain_start({}, {}, run_id="n", parent_run_id="root",
                          metadata={"langgraph_node": "fetch"}, name="fetch")
    tracer.on_chain_error(TimeoutError("upstream timed out"), run_id="n", parent_run_id="root")

    end = [f for f in frames if f["type"] == "node_end"][0]
    assert end["ok"] is False and "timed out" in end["error"]


def test_sharing_one_tracer_across_concurrent_runs_is_refused(transport):
    """Interleaving two runs into one record silently would be worse."""
    seen = []
    tracer = HubTracer(transport=transport, on_error=seen.append)

    tracer.on_chain_start({}, {}, run_id="first", parent_run_id=None)
    tracer.on_chain_start({}, {}, run_id="second", parent_run_id=None)

    assert seen and "already following a run" in str(seen[0])


def test_token_usage_is_forwarded_in_whichever_spelling_it_arrives(transport):
    """The model call happens here, so this is the only place cost can come from."""
    frames = []
    tracer = HubTracer(transport=transport)
    tracer._emit = frames.append

    class _Message:
        content = "hi"
        usage_metadata = {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150,
                          "input_token_details": {"cache_read": 100}}

    class _Gen:
        message = _Message()
        text = "hi"

    class _Response:
        generations = [[_Gen()]]
        llm_output = None

    tracer.on_llm_end(_Response())

    usage = [f for f in frames if f["type"] == "usage"][0]
    assert usage["input_tokens"] == 120
    assert usage["cached_tokens"] == 100


# ── a graph that stops to ask ───────────────────────────────────────────────
#
# LangGraph's interrupt() really suspends: the graph's state sits in a
# checkpointer and resumes on the same thread. The hub cannot push an answer to
# an agent that reports in, so the shape is: park the run with the question,
# somebody answers in the hub, the client collects it and resumes. Only the
# client can perform the resume, which is why the tracer waits rather than acts.


def _approval_graph():
    pytest.importorskip("langgraph")
    from typing import TypedDict

    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    class State(TypedDict):
        steps: list

    def plan(state):
        return {"steps": state.get("steps", []) + ["plan"]}

    def approve(state):
        decision = interrupt({"question": "Ship the release?", "choices": ["yes", "no"]})
        return {"steps": state["steps"] + [f"approve:{decision}"]}

    def finish(state):
        return {"steps": state["steps"] + ["finish"]}

    builder = StateGraph(State)
    builder.add_node("plan", plan)
    builder.add_node("approve", approve)
    builder.add_node("finish", finish)
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "approve")
    builder.add_edge("approve", "finish")
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=MemorySaver())


def test_an_interrupted_graph_parks_its_run_with_the_question(transport, api_client):
    graph = _approval_graph()
    tracer = HubTracer(transport=transport, graph=graph)
    config = {"configurable": {"thread_id": "t1"}, "callbacks": [tracer]}

    result = graph.invoke({"steps": []}, config=config)
    assert tracer.flush(timeout=10)

    assert "__interrupt__" in result, "the graph under test did not actually pause"
    run_id = tracer.parked_run_id
    record = run_manager.get_run_by_id(run_id)
    assert record["status"] == "awaiting_input"
    assert record["pending_question"]["question"] == "Ship the release?"
    assert record["pending_question"]["choices"] == ["yes", "no"]
    # The node it stopped in never gets an end of its own from LangGraph, and a
    # paused run drawn as a running one is the opposite of useful.
    assert record["graph_path"] == ["plan", "approve"]


def test_the_tracer_waits_for_an_answer_and_the_graph_carries_on(transport, api_client):
    graph = _approval_graph()
    tracer = HubTracer(transport=transport, graph=graph)
    config = {"configurable": {"thread_id": "t2"}, "callbacks": [tracer]}

    graph.invoke({"steps": []}, config=config)
    tracer.flush(timeout=10)
    parked = tracer.parked_run_id

    # Somebody answers in the hub, the way the connection's page does.
    api_client.post(f"/api/connections/billing-graph/runs/{parked}/answer",
                    json={"value": "yes", "answered_by": "anton"})

    answer = tracer.wait_for_answer(timeout=5, poll_interval=0.01)
    assert answer == "yes"

    from langgraph.types import Command
    final = graph.invoke(Command(resume=answer), config=config)
    assert tracer.flush(timeout=10)

    assert final["steps"] == ["plan", "approve:yes", "finish"]
    # The resumed work is its own run, linked to the one that waited: nothing
    # here pretends a run stayed open across a person's lunch break.
    resumed_id = [c for c in transport.calls if c.endswith("/close")][-1].split("/")[-2]
    resumed = run_manager.get_run_by_id(resumed_id)
    assert resumed["resumed_from"] == parked
    assert resumed["session_id"] == run_manager.get_run_by_id(parked)["session_id"]


def test_waiting_gives_up_rather_than_blocking_a_graph_forever(transport):
    graph = _approval_graph()
    tracer = HubTracer(transport=transport, graph=graph)

    graph.invoke({"steps": []}, config={"configurable": {"thread_id": "t3"}, "callbacks": [tracer]})
    tracer.flush(timeout=10)

    assert tracer.wait_for_answer(timeout=0.2, poll_interval=0.05) is None


def test_an_interrupt_payload_that_is_not_a_question_still_asks_something():
    """`interrupt()` takes whatever the graph's author passed. A bare string, or
    a dict with no question field, must still reach a person as a prompt."""
    from agents_hub_langgraph.tracer import _question_of

    class _Interrupt:
        def __init__(self, value):
            self.value = value
            self.id = "abc"

    assert _question_of(_Interrupt("Approve?"))["question"] == "Approve?"
    assert _question_of(_Interrupt({"amount": 900}))["question"], "an unlabelled payload must still be shown"
    assert _question_of(_Interrupt({"question": "Ship?", "options": ["y", "n"]}))["choices"] == ["y", "n"]


def test_a_pause_is_reported_even_when_the_tracer_has_no_graph(transport):
    """Without the graph the question cannot be read, but "this run is waiting,
    in this node" is still the truth and still worth reporting."""
    graph = _approval_graph()
    tracer = HubTracer(transport=transport)

    graph.invoke({"steps": []}, config={"configurable": {"thread_id": "t4"}, "callbacks": [tracer]})
    assert tracer.flush(timeout=10)

    record = run_manager.get_run_by_id(tracer.parked_run_id)
    assert record["status"] == "awaiting_input"
    assert record["pending_question"]["node"] == "approve"
    assert record["pending_question"]["question"] == ""
