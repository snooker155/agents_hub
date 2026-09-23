"""End-to-end coverage for importing an agent from an external repository.

The bundled Aider example (``examples/imported-agents/aider-agenthub``) is the
fixture: it is copied into a temp directory and ``git init``-ed so the importer
clones it exactly as it would clone a remote, with no network involved.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agents import registry
from agents.importer import checks, clone
from agents.importer import service as import_service
from agents.importer.manifest import parse_manifest
from agents.remote_agent import RemoteAgent, normalize_topology, resolve_auth_header

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "imported-agents"
EXAMPLE_DIR = EXAMPLES / "aider-agenthub"
# The second bundled example: an agent that is internally a graph and says so.
GRAPH_EXAMPLE_DIR = EXAMPLES / "langgraph-agenthub"
# The same adapter for LangGraph.js. Its behaviour is covered by its own test
# suite, in Node; what belongs here is that the hub reads its manifest the same
# way, since that is this side's half of the contract.
JS_GRAPH_EXAMPLE_DIR = EXAMPLES / "langgraph-agenthub-js"


# ── fixtures ────────────────────────────────────────────────────────────────

def _as_repo(source: Path, dest: Path) -> Path:
    """Copy a bundled example and git init it, so the importer can clone it."""
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run(["git", "init", "--quiet"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.local", "-c", "user.name=t",
         "commit", "--quiet", "-m", "example"],
        cwd=dest, check=True,
    )
    return dest


@pytest.fixture
def example_repo(tmp_path):
    """The bundled example, turned into a standalone git repo we can clone."""
    return _as_repo(EXAMPLE_DIR, tmp_path / "aider-agenthub-src")


@pytest.fixture
def graph_example_repo(tmp_path):
    """The bundled LangGraph example, which declares a graph endpoint."""
    return _as_repo(GRAPH_EXAMPLE_DIR, tmp_path / "langgraph-agenthub-src")


@pytest.fixture
def js_graph_example_repo(tmp_path):
    """The bundled LangGraph.js example, which declares the same contract."""
    return _as_repo(JS_GRAPH_EXAMPLE_DIR, tmp_path / "langgraph-agenthub-js-src")


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    """Keep generated definition markdown out of the real agents/definitions."""
    from agents import prompt_assembly
    from agents.agent_factory import get_factory

    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    monkeypatch.setattr(get_factory(), "definitions_dir", defs)
    return defs


@pytest.fixture(autouse=True)
def clean_registry():
    """Give each test an empty registry in the throwaway state root.

    ``bootstrap`` always writes agents.json in a real deployment, so an absent
    file is a test-only situation; creating it here keeps the registry's own
    "config must exist" contract intact instead of loosening it for tests.
    """
    registry.replace_all_raw([])
    yield
    registry.replace_all_raw([])


@pytest.fixture
def no_env(monkeypatch):
    """Ensure the example's required key is unset, whatever the dev machine has."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


# ── a stand-in for the imported agent's own service ─────────────────────────

class _StubHandler(BaseHTTPRequestHandler):
    """Implements the import contract: GET /health and POST /run."""

    # Behaviour switches set by the test before the request.
    reply = {"ok": True, "output": "patched api/users.py"}
    status = 200

    def log_message(self, *args):  # keep pytest output clean
        pass

    # Answer for GET /graph, set per test. The default is a two-node graph with
    # one conditional edge, which is the smallest shape worth drawing.
    topology = {
        "ok": True,
        "framework": "langgraph",
        "nodes": [{"id": "triage", "label": "triage"}, {"id": "answer", "label": "answer"}],
        "edges": [{"source": "triage", "target": "answer", "conditional": True}],
    }

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        elif self.path == "/graph":
            self._send(200, type(self).topology)
        else:
            self._send(404, {"error": "not found"})

    # NDJSON frames returned by /run/stream, set per test.
    stream_frames = [
        {"type": "token", "token": "reading "},
        {"type": "token", "token": "api/users.py"},
        {"type": "done", "ok": True, "output": "patched api/users.py"},
    ]

    # Frames returned by /resume, set per test.
    resume_frames = [
        {"type": "token", "token": "continuing"},
        {"type": "done", "ok": True, "output": "finished after the answer"},
    ]
    # Bodies the stub was asked to resume with, so a test can check what the hub
    # actually sent rather than only what came back.
    resumed = []

    def do_POST(self):
        if self.path == "/run/stream":
            self._send_stream()
            return
        if self.path == "/resume":
            length = int(self.headers.get("Content-Length") or 0)
            type(self).resumed.append(json.loads(self.rfile.read(length) or b"{}"))
            self._send_stream(frames=type(self).resume_frames, read_body=False)
            return
        if self.path != "/run":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        payload = dict(type(self).reply)
        payload["echo_prompt"] = body.get("prompt")
        self._send(type(self).status, payload)

    def _send_stream(self, frames=None, read_body=True):
        if read_body:
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for frame in (frames if frames is not None else type(self).stream_frames):
            self.wfile.write((json.dumps(frame) + "\n").encode())
            self.wfile.flush()

    def _send(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def stub_agent_service():
    """Run the stub agent on a free port; yields its base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _StubHandler
    finally:
        server.shutdown()
        server.server_close()
        _StubHandler.reply = {"ok": True, "output": "patched api/users.py"}
        _StubHandler.status = 200
        _StubHandler.stream_frames = [
            {"type": "token", "token": "reading "},
            {"type": "token", "token": "api/users.py"},
            {"type": "done", "ok": True, "output": "patched api/users.py"},
        ]
        _StubHandler.resume_frames = [
            {"type": "token", "token": "continuing"},
            {"type": "done", "ok": True, "output": "finished after the answer"},
        ]
        _StubHandler.resumed = []
        _StubHandler.topology = {
            "ok": True,
            "framework": "langgraph",
            "nodes": [{"id": "triage", "label": "triage"}, {"id": "answer", "label": "answer"}],
            "edges": [{"source": "triage", "target": "answer", "conditional": True}],
        }


# ── manifest ────────────────────────────────────────────────────────────────

def test_example_manifest_parses_cleanly(example_repo):
    manifest = parse_manifest(example_repo)
    assert manifest.found
    assert manifest.problems == []
    assert manifest.id == "aider"
    assert manifest.run_path == "/run"
    assert manifest.health_path == "/health"
    assert manifest.dockerfile == "Dockerfile.agenthub"
    assert [e.name for e in manifest.env if e.required] == ["OPENAI_API_KEY"]


def test_missing_manifest_is_reported_not_raised(tmp_path):
    (tmp_path / "README.md").write_text("just a repo")
    manifest = parse_manifest(tmp_path)
    assert manifest.found is False
    check = checks.check_manifest(manifest)
    assert check.ok is False
    assert "agent-hub.json" in check.detail
    assert check.fix  # the operator is told what to add


def test_malformed_manifest_collects_problems(tmp_path):
    (tmp_path / "agent-hub.json").write_text(json.dumps({
        "schema": "agents-hub/agent-manifest@1",
        "id": "Bad Id",
        "runtime": {"kind": "grpc"},
    }))
    manifest = parse_manifest(tmp_path)
    assert manifest.found
    joined = " ".join(manifest.problems)
    assert "'id' must be lowercase" in joined
    assert "runtime.kind 'grpc' is not supported" in joined


# ── inspection ──────────────────────────────────────────────────────────────

def test_inspect_reports_missing_endpoint_and_env(example_repo, no_env):
    result = import_service.inspect(str(example_repo))
    report = result["report"]

    assert result["suggested"]["id"] == "aider"
    assert report["runnable"] is False
    blocking = {c["id"] for c in report["blocking"]}
    assert blocking == {"endpoint", "env"}
    assert "Cannot run yet" in report["summary"]
    # Every failing check carries a remedy — that is what makes the report useful.
    assert all(c["fix"] for c in report["blocking"])
    clone.discard(result["token"])


def test_inspect_passes_required_checks_when_configured(example_repo, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    result = import_service.inspect(str(example_repo), url="http://localhost:8410")
    report = result["report"]

    assert report["runnable"] is True
    # Health is a recommendation: nothing is listening on 8410 in the test, and
    # an unstarted service must not block the import.
    assert {c["id"] for c in report["warnings"]} == {"health"}
    clone.discard(result["token"])


def test_inspect_finds_the_bundled_dockerfile(example_repo, no_env):
    result = import_service.inspect(str(example_repo))
    packaging = next(c for c in result["report"]["checks"] if c["id"] == "packaging")
    assert packaging["ok"] is True
    assert "Dockerfile.agenthub" in packaging["detail"]
    clone.discard(result["token"])


def test_inspect_rejects_a_source_that_is_not_a_repo(tmp_path):
    with pytest.raises(clone.ImportSourceError):
        import_service.inspect("ftp://example.com/repo.git")
    with pytest.raises(clone.ImportSourceError):
        import_service.inspect(str(tmp_path / "does-not-exist"))


# ── registration ────────────────────────────────────────────────────────────

def test_unrunnable_agent_is_still_registered_with_its_reasons(example_repo, no_env):
    inspected = import_service.inspect(str(example_repo))
    result = import_service.register(
        inspected["token"], repo_url=str(example_repo), agent_id="aider",
    )

    assert result["runnable"] is False
    spec = registry.get_agent("aider")
    assert spec is not None, "an agent that cannot run yet must still appear in the list"
    assert spec.is_remote()
    assert spec.type == "remote"
    # The reasons travel with the record, so the agent list can show them.
    stored = spec.remote["readiness"]
    assert stored["runnable"] is False
    assert {c["id"] for c in stored["blocking"]} == {"endpoint", "env"}


def test_register_records_provenance_and_writes_documentation(example_repo, monkeypatch, isolated_definitions):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    inspected = import_service.inspect(str(example_repo), url="http://localhost:8410")
    import_service.register(
        inspected["token"], repo_url=str(example_repo), agent_id="aider",
        url="http://localhost:8410",
    )

    spec = registry.get_agent("aider")
    assert spec.remote["repo_url"] == str(example_repo)
    assert spec.remote["commit"], "the imported commit is recorded"
    assert Path(spec.remote["clone_path"]).is_dir(), "the clone is kept for re-checks"
    assert spec.remote["url"] == "http://localhost:8410"
    assert spec.tools == [], "the hub grants a remote agent no tools"

    instructions = (isolated_definitions / "aider" / "instructions.md").read_text()
    assert "imported agent" in instructions
    assert "http://localhost:8410/run" in instructions


def test_reimport_of_a_different_repo_is_refused(example_repo, tmp_path, no_env):
    inspected = import_service.inspect(str(example_repo))
    import_service.register(inspected["token"], repo_url=str(example_repo), agent_id="aider")

    other = tmp_path / "other"
    shutil.copytree(EXAMPLE_DIR, other)
    subprocess.run(["git", "init", "--quiet"], cwd=other, check=True)
    subprocess.run(["git", "add", "-A"], cwd=other, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.local", "-c", "user.name=t", "commit", "--quiet", "-m", "x"],
        cwd=other, check=True,
    )
    second = import_service.inspect(str(other))
    with pytest.raises(import_service.ImportError_) as exc:
        import_service.register(second["token"], repo_url=str(other), agent_id="aider")
    assert "different repository" in str(exc.value)
    clone.discard(second["token"])


def test_collision_with_a_builtin_agent_is_refused(example_repo, no_env):
    registry.add_agent(registry.AgentSpec(
        id="already-here", name="Built in", type="langchain",
        entrypoint="agents.agent_factory:build_agent_executor",
    ))
    inspected = import_service.inspect(str(example_repo))
    with pytest.raises(import_service.ImportError_) as exc:
        import_service.register(
            inspected["token"], repo_url=str(example_repo), agent_id="already-here",
        )
    assert "not an imported agent" in str(exc.value)
    clone.discard(inspected["token"])


# ── re-check ────────────────────────────────────────────────────────────────

def test_recheck_flips_to_runnable_once_the_service_is_up(
    example_repo, monkeypatch, stub_agent_service, no_env,
):
    url, _ = stub_agent_service
    inspected = import_service.inspect(str(example_repo))
    import_service.register(inspected["token"], repo_url=str(example_repo), agent_id="aider")
    assert registry.get_agent("aider").remote["readiness"]["runnable"] is False

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    result = import_service.recheck("aider", url=url)

    assert result["runnable"] is True
    assert result["report"]["warnings"] == [], "a live service clears the health warning"
    assert registry.get_agent("aider").remote["url"] == url


def test_recheck_refreshes_the_generated_documentation(
    example_repo, stub_agent_service, isolated_definitions, no_env,
):
    """The definition quotes the endpoint, so it must follow a moved URL."""
    url, _ = stub_agent_service
    inspected = import_service.inspect(str(example_repo))
    import_service.register(inspected["token"], repo_url=str(example_repo), agent_id="aider")
    assert "<not configured>" in (isolated_definitions / "aider" / "instructions.md").read_text()

    import_service.recheck("aider", url=url)

    refreshed = (isolated_definitions / "aider" / "instructions.md").read_text()
    assert f"{url}/run" in refreshed
    assert "<not configured>" not in refreshed


def test_recheck_refuses_a_builtin_agent():
    registry.add_agent(registry.AgentSpec(
        id="plain", name="Plain", type="langchain",
        entrypoint="agents.agent_factory:build_agent_executor",
    ))
    with pytest.raises(import_service.ImportError_):
        import_service.recheck("plain")


# ── the RemoteAgent adapter ─────────────────────────────────────────────────

def test_remote_agent_run_maps_a_successful_reply(stub_agent_service):
    url, _ = stub_agent_service
    agent = RemoteAgent(agent_id="aider", name="Aider", remote={"url": url})

    result = agent.run("add retry handling", run_id="r1")

    assert result.ok is True
    assert result.status == "done"
    assert result.agent_output == "patched api/users.py"


def test_remote_agent_arun_matches_run(stub_agent_service):
    """Chat and the project routes drive agents through arun, not run."""
    import asyncio

    url, _ = stub_agent_service
    agent = RemoteAgent(agent_id="aider", name="Aider", remote={"url": url})

    result = asyncio.run(agent.arun("add retry handling", run_id="r1"))

    assert result.ok is True
    assert result.agent_output == "patched api/users.py"


def test_remote_agent_arun_reports_failures_like_run(stub_agent_service):
    import asyncio

    url, handler = stub_agent_service
    handler.status = 503
    handler.reply = {"detail": "starting up"}
    agent = RemoteAgent(agent_id="aider", name="Aider", remote={"url": url})

    result = asyncio.run(agent.arun("go"))

    assert result.ok is False
    assert "HTTP 503" in result.error


def test_remote_agent_reports_in_band_failure(stub_agent_service):
    url, handler = stub_agent_service
    handler.reply = {"ok": False, "error": "aider exited with code 1"}
    agent = RemoteAgent(agent_id="aider", name="Aider", remote={"url": url})

    result = agent.run("break everything")

    assert result.ok is False
    assert "exited with code 1" in result.error


def test_remote_agent_reports_http_failure(stub_agent_service):
    url, handler = stub_agent_service
    handler.status = 500
    handler.reply = {"detail": "boom"}
    agent = RemoteAgent(agent_id="aider", name="Aider", remote={"url": url})

    result = agent.run("anything")

    assert result.ok is False
    assert "HTTP 500" in result.error


def test_remote_agent_maps_steps(stub_agent_service):
    url, handler = stub_agent_service
    handler.reply = {
        "ok": True, "output": "done",
        "steps": [{"name": "aider", "args": {"cwd": "/work"}, "output": "edited 2 files"}],
    }
    agent = RemoteAgent(agent_id="aider", name="Aider", remote={"url": url})

    result = agent.run("go")

    assert [s.name for s in result.steps] == ["aider"]
    assert result.steps[0].output == "edited 2 files"


def test_unreachable_remote_is_a_failed_run_not_an_exception():
    agent = RemoteAgent(
        agent_id="aider", name="Aider",
        # Port 1 is reserved and never listening.
        remote={"url": "http://127.0.0.1:1", "timeout": 2},
    )
    result = agent.run("hello")
    assert result.ok is False
    assert "Could not reach remote agent" in result.error


def test_missing_endpoint_is_explained_rather_than_crashing():
    result = RemoteAgent(agent_id="x", name="X", remote={}).run("hello")
    assert result.ok is False
    assert "no endpoint configured" in result.error


def test_remote_agent_has_no_local_executor():
    from agents.remote_agent import RemoteAgentError

    agent = RemoteAgent(agent_id="x", name="X", remote={"url": "http://localhost:1"})
    with pytest.raises(RemoteAgentError):
        _ = agent.executor


def test_auth_header_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("MY_AGENT_TOKEN", "s3cret")
    assert resolve_auth_header({"auth_token_env": "MY_AGENT_TOKEN"}) == {
        "Authorization": "Bearer s3cret"
    }
    assert resolve_auth_header(
        {"auth_token_env": "MY_AGENT_TOKEN", "auth_header": "X-API-Key"}
    ) == {"X-API-Key": "s3cret"}
    # An unset variable yields no header rather than an empty credential.
    monkeypatch.delenv("MY_AGENT_TOKEN")
    assert resolve_auth_header({"auth_token_env": "MY_AGENT_TOKEN"}) == {}


# ── the factory branch ──────────────────────────────────────────────────────

def test_create_agent_builds_a_remote_agent(example_repo, stub_agent_service, no_env):
    from agents.agent_factory import create_agent

    url, _ = stub_agent_service
    inspected = import_service.inspect(str(example_repo), url=url)
    import_service.register(
        inspected["token"], repo_url=str(example_repo), agent_id="aider", url=url,
    )

    agent = create_agent("aider")
    assert isinstance(agent, RemoteAgent)
    # The unchanged create_agent(...).run(...) path reaches the remote service.
    assert agent.run("hello").agent_output == "patched api/users.py"


def test_deleting_an_imported_agent_removes_its_clone(example_repo, no_env):
    inspected = import_service.inspect(str(example_repo))
    import_service.register(inspected["token"], repo_url=str(example_repo), agent_id="aider")
    clone_path = Path(registry.get_agent("aider").remote["clone_path"])
    assert clone_path.is_dir()

    assert import_service.cleanup("aider") is True
    assert not clone_path.exists()


# ── the HTTP API ────────────────────────────────────────────────────────────

@pytest.fixture
def api_client():
    """A TestClient carrying only the routers this feature touches.

    Mounting the two routers rather than the whole app keeps the fixture free of
    the dashboard's startup work while still exercising the real request models,
    status codes and the two-phase token flow.
    """
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from routes import agent_import as agent_import_routes
    from routes import agents as agent_routes

    app = FastAPI()
    app.include_router(agent_import_routes.router)
    app.include_router(agent_routes.router)
    return TestClient(app)


def test_requirements_endpoint_describes_the_contract(api_client):
    body = api_client.get("/api/agent-import/requirements").json()
    assert body["manifest_filenames"][0] == "agent-hub.json"
    assert {r["id"] for r in body["requirements"]} >= {"manifest", "http", "env", "packaging"}
    # The example must satisfy the parser it is an example for.
    assert body["example_manifest"]["runtime"]["run_path"] == "/run"


def test_api_inspect_then_register_then_listed(api_client, example_repo, stub_agent_service, monkeypatch):
    url, _ = stub_agent_service
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    inspected = api_client.post("/api/agent-import/inspect", json={
        "repo_url": str(example_repo), "url": url,
    }).json()
    assert inspected["report"]["runnable"] is True

    registered = api_client.post("/api/agent-import/register", json={
        "token": inspected["token"],
        "repo_url": str(example_repo),
        "agent_id": inspected["suggested"]["id"],
        "url": url,
    })
    assert registered.status_code == 200
    assert registered.json()["runnable"] is True

    listed = api_client.get("/api/agents").json()
    imported = next(a for a in listed if a["id"] == "aider")
    assert imported["type"] == "remote"
    assert imported["remote"]["readiness"]["runnable"] is True


def test_api_registers_an_unready_agent_with_its_reasons(api_client, example_repo, no_env):
    inspected = api_client.post("/api/agent-import/inspect", json={
        "repo_url": str(example_repo),
    }).json()
    assert inspected["report"]["runnable"] is False

    registered = api_client.post("/api/agent-import/register", json={
        "token": inspected["token"],
        "repo_url": str(example_repo),
        "agent_id": "aider",
    })
    assert registered.status_code == 200, "an unready agent is still registered"
    assert registered.json()["runnable"] is False

    listed = api_client.get("/api/agents").json()
    imported = next(a for a in listed if a["id"] == "aider")
    blocking = {c["id"] for c in imported["remote"]["readiness"]["blocking"]}
    assert blocking == {"endpoint", "env"}


def test_api_recheck_updates_the_stored_verdict(api_client, example_repo, stub_agent_service, monkeypatch, no_env):
    url, _ = stub_agent_service
    inspected = api_client.post("/api/agent-import/inspect", json={
        "repo_url": str(example_repo),
    }).json()
    api_client.post("/api/agent-import/register", json={
        "token": inspected["token"], "repo_url": str(example_repo), "agent_id": "aider",
    })

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    rechecked = api_client.post("/api/agent-import/aider/recheck", json={"url": url})
    assert rechecked.status_code == 200
    assert rechecked.json()["runnable"] is True

    details = api_client.get("/api/agent-import/aider").json()
    assert details["readiness"]["runnable"] is True
    assert details["remote"]["url"] == url


def test_api_rejects_a_bad_source(api_client):
    resp = api_client.post("/api/agent-import/inspect", json={"repo_url": "ftp://nope/x.git"})
    assert resp.status_code == 400
    assert "Unsupported URL scheme" in resp.json()["detail"]


def test_api_discard_drops_the_staged_clone(api_client, example_repo, no_env):
    inspected = api_client.post("/api/agent-import/inspect", json={
        "repo_url": str(example_repo),
    }).json()
    staged = clone.STAGING_DIR / inspected["token"]
    assert staged.is_dir()

    assert api_client.post("/api/agent-import/discard", json={"token": inspected["token"]}).status_code == 200
    assert not staged.exists()


def test_api_deleting_an_imported_agent_removes_its_clone(api_client, example_repo, no_env):
    inspected = api_client.post("/api/agent-import/inspect", json={
        "repo_url": str(example_repo),
    }).json()
    api_client.post("/api/agent-import/register", json={
        "token": inspected["token"], "repo_url": str(example_repo), "agent_id": "aider",
    })
    clone_path = Path(registry.get_agent("aider").remote["clone_path"])
    assert clone_path.is_dir()

    assert api_client.delete("/api/agents/aider").status_code == 200
    assert registry.get_agent("aider") is None
    assert not clone_path.exists()


# ── streaming ───────────────────────────────────────────────────────────────

class _RecordingEmitter:
    """Stands in for ChatStreamCallback: collects events and counts tokens.

    Mirrors the two attributes RemoteAgent looks for — ``emit_external`` and the
    token counters the chat pipeline reads off the callback — so these tests
    exercise the same wiring the real chat run uses.
    """

    def __init__(self):
        self.events = []
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def emit_external(self, payload):
        self.events.append(payload)

    def tokens(self):
        return "".join(e["token"] for e in self.events if e.get("type") == "token")


def _streaming_agent(url):
    return RemoteAgent(
        agent_id="aider", name="Aider",
        remote={"url": url, "stream_path": "/run/stream"},
    )


def test_streaming_manifest_field_reaches_the_descriptor(example_repo, no_env):
    """The bundled example declares streaming, so an import must carry it."""
    inspected = import_service.inspect(str(example_repo))
    assert inspected["manifest"]["stream_path"] == "/run/stream"

    import_service.register(inspected["token"], repo_url=str(example_repo), agent_id="aider")
    assert registry.get_agent("aider").remote["stream_path"] == "/run/stream"


def test_streaming_check_is_a_recommendation_not_a_blocker(tmp_path):
    from agents.importer.manifest import parse_manifest as _parse

    (tmp_path / "agent-hub.json").write_text(json.dumps({
        "schema": "agents-hub/agent-manifest@1",
        "id": "quiet", "name": "Quiet",
        "runtime": {"kind": "http", "run_path": "/run"},
    }))
    check = checks.check_streaming(_parse(tmp_path))
    assert check.ok is False
    assert check.required is False, "a non-streaming agent is still perfectly runnable"
    assert "stream_path" in check.fix


def test_run_streams_tokens_to_the_listener(stub_agent_service):
    url, _ = stub_agent_service
    listener = _RecordingEmitter()

    result = _streaming_agent(url).run("go", callbacks=[listener])

    assert listener.tokens() == "reading api/users.py"
    # The done frame is authoritative for the final answer.
    assert result.ok is True
    assert result.agent_output == "patched api/users.py"


def test_arun_streams_tokens_to_the_listener(stub_agent_service):
    import asyncio

    url, _ = stub_agent_service
    listener = _RecordingEmitter()

    result = asyncio.run(_streaming_agent(url).arun("go", callbacks=[listener]))

    assert listener.tokens() == "reading api/users.py"
    assert result.agent_output == "patched api/users.py"


def test_streaming_is_skipped_when_nobody_is_listening(stub_agent_service):
    """No listener means the single POST is used — streaming into a void is waste."""
    url, handler = stub_agent_service
    handler.stream_frames = [{"type": "done", "ok": True, "output": "from the stream"}]
    agent = _streaming_agent(url)

    result = agent.run("go")

    assert agent.supports_streaming is True
    assert result.agent_output == "patched api/users.py", "fell back to POST /run"


def test_non_streaming_remote_ignores_the_listener(stub_agent_service):
    url, _ = stub_agent_service
    listener = _RecordingEmitter()
    agent = RemoteAgent(agent_id="aider", name="Aider", remote={"url": url})

    result = agent.run("go", callbacks=[listener])

    assert agent.supports_streaming is False
    assert listener.events == []
    assert result.agent_output == "patched api/users.py"


def test_stream_forwards_tool_and_thinking_frames(stub_agent_service):
    url, handler = stub_agent_service
    handler.stream_frames = [
        {"type": "thinking", "message": "scanning the repo"},
        {"type": "tool_start", "name": "aider", "input": "aider --message go"},
        {"type": "token", "token": "editing"},
        {"type": "tool_end", "name": "aider", "output": "edited 2 files"},
        {"type": "done", "ok": True, "output": "done"},
    ]
    listener = _RecordingEmitter()

    result = _streaming_agent(url).run("go", callbacks=[listener])

    kinds = [e["type"] for e in listener.events]
    assert kinds == ["thinking", "tool_start", "token", "tool_end"]
    tool_start = listener.events[1]
    assert tool_start["tool"] == "aider" and tool_start["step"] == 1
    # Tool frames seen live become the run's step trail.
    assert [s.name for s in result.steps] == ["aider"]
    assert result.steps[0].output == "edited 2 files"


def test_remote_reported_usage_is_credited_to_the_run(stub_agent_service):
    """The remote is the only party that can know its own token usage."""
    url, handler = stub_agent_service
    handler.stream_frames = [
        {"type": "token", "token": "hi"},
        {"type": "usage", "prompt_tokens": 120, "completion_tokens": 30},
        {"type": "done", "ok": True, "output": "hi"},
    ]
    listener = _RecordingEmitter()

    _streaming_agent(url).run("go", callbacks=[listener])

    assert (listener.prompt_tokens, listener.completion_tokens) == (120, 30)
    assert listener.total_tokens == 150
    usage_event = next(e for e in listener.events if e["type"] == "usage")
    assert usage_event["remote"] is True


def test_usage_on_the_done_frame_is_also_credited(stub_agent_service):
    url, handler = stub_agent_service
    handler.stream_frames = [
        {"type": "done", "ok": True, "output": "hi",
         "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}},
    ]
    listener = _RecordingEmitter()

    _streaming_agent(url).run("go", callbacks=[listener])

    assert listener.total_tokens == 15


def test_stream_without_a_done_frame_keeps_the_tokens(stub_agent_service):
    """A remote that just stops has still delivered an answer."""
    url, handler = stub_agent_service
    handler.stream_frames = [
        {"type": "token", "token": "partial "},
        {"type": "token", "token": "answer"},
    ]
    listener = _RecordingEmitter()

    result = _streaming_agent(url).run("go", callbacks=[listener])

    assert result.ok is True
    assert result.agent_output == "partial answer"


def test_stream_with_neither_output_nor_done_is_an_error(stub_agent_service):
    url, handler = stub_agent_service
    handler.stream_frames = []
    listener = _RecordingEmitter()

    result = _streaming_agent(url).run("go", callbacks=[listener])

    assert result.ok is False
    assert "without sending any output" in result.error


def test_stream_reports_in_band_failure(stub_agent_service):
    url, handler = stub_agent_service
    handler.stream_frames = [
        {"type": "token", "token": "trying"},
        {"type": "done", "ok": False, "error": "aider exited with code 1"},
    ]
    listener = _RecordingEmitter()

    result = _streaming_agent(url).run("go", callbacks=[listener])

    assert result.ok is False
    assert "exited with code 1" in result.error


def test_stream_falls_back_to_http_error_reporting(stub_agent_service):
    url, handler = stub_agent_service
    handler.status = 500
    listener = _RecordingEmitter()

    result = _streaming_agent(url).run("go", callbacks=[listener])

    assert result.ok is False
    assert "HTTP 500" in result.error


def test_sse_framing_is_accepted_as_well_as_ndjson():
    """A repository may answer with SSE; both wire formats decode the same."""
    from agents.remote_agent import _parse_stream_line

    assert _parse_stream_line('data: {"type": "token", "token": "x"}') == {"type": "token", "token": "x"}
    assert _parse_stream_line('{"type": "token", "token": "x"}') == {"type": "token", "token": "x"}
    # Keep-alives, blank lines and the SSE terminator are skipped, not fatal.
    assert _parse_stream_line(": keep-alive") is None
    assert _parse_stream_line("") is None
    assert _parse_stream_line("data: [DONE]") is None
    assert _parse_stream_line("not json at all") is None


def test_a_broken_listener_does_not_break_the_run(stub_agent_service):
    """Streaming is decoration; a failing display must not fail the work."""
    url, _ = stub_agent_service

    class Exploding(_RecordingEmitter):
        def emit_external(self, payload):
            raise RuntimeError("browser went away")

    result = _streaming_agent(url).run("go", callbacks=[Exploding()])

    assert result.ok is True
    assert result.agent_output == "patched api/users.py"


def test_delegation_emitter_is_used_when_no_callback_carries_one(stub_agent_service):
    """A remote agent reached through run_agent_tool streams to the parent chat."""
    from common import stream_sink

    url, _ = stub_agent_service
    seen = []
    token = stream_sink.set_emitter(seen.append)
    try:
        result = _streaming_agent(url).run("go")
    finally:
        stream_sink.reset_emitter(token)

    assert "".join(e["token"] for e in seen if e.get("type") == "token") == "reading api/users.py"
    assert result.agent_output == "patched api/users.py"


# ── agents that are graphs ──────────────────────────────────────────────────
#
# An imported agent may be a graph internally (LangGraph, and anything else that
# knows its own shape). Two things follow: it narrates node boundaries while it
# runs, and it can hand over its topology so the hub draws it instead of showing
# one opaque box.


def _graph_agent(url):
    return RemoteAgent(
        agent_id="graph", name="Graph",
        remote={"url": url, "stream_path": "/run/stream", "graph_path": "/graph"},
    )


def test_node_frames_are_not_forwarded_as_flow_node_events(stub_agent_service):
    """A remote's own nodes must not masquerade as hub flow nodes.

    ``node_start`` on the chat stream already means "a node of a flow this hub is
    running started", and the chat opens a bubble per flow node. Forwarding a
    remote agent's internal nodes under that name would split one agent's answer
    into flow bubbles in a conversation that is running no flow at all.
    """
    url, handler = stub_agent_service
    handler.stream_frames = [
        {"type": "node_start", "node": "triage"},
        {"type": "token", "token": "thinking"},
        {"type": "node_end", "node": "triage", "ok": True, "next": "answer"},
        {"type": "done", "ok": True, "output": "answered"},
    ]
    listener = _RecordingEmitter()

    _graph_agent(url).run("go", callbacks=[listener])

    kinds = [e["type"] for e in listener.events]
    assert kinds == ["graph_node_start", "token", "graph_node_end"]
    assert "node_start" not in kinds


def test_node_end_reports_the_branch_that_was_taken(stub_agent_service):
    """On a conditional edge, which way the run went is the whole question."""
    url, handler = stub_agent_service
    handler.stream_frames = [
        {"type": "node_start", "node": "triage", "depth": 0},
        {"type": "node_end", "node": "triage", "ok": True, "next": "pricing"},
        {"type": "done", "ok": True, "output": "ok"},
    ]
    listener = _RecordingEmitter()

    _graph_agent(url).run("go", callbacks=[listener])

    end = listener.events[-1]
    assert end["node"] == "triage"
    assert end["next"] == "pricing"
    assert end["ok"] is True


def test_a_node_entered_twice_resolves_once_per_pass(stub_agent_service):
    """An agent loop revisits nodes; each visit has to close on its own."""
    url, handler = stub_agent_service
    handler.stream_frames = [
        {"type": "node_start", "node": "critic"},
        {"type": "node_end", "node": "critic", "ok": True},
        {"type": "node_start", "node": "critic"},
        {"type": "node_end", "node": "critic", "ok": False, "error": "gave up"},
        {"type": "done", "ok": True, "output": "ok"},
    ]
    listener = _RecordingEmitter()

    _graph_agent(url).run("go", callbacks=[listener])

    ends = [e for e in listener.events if e["type"] == "graph_node_end"]
    assert [e["ok"] for e in ends] == [True, False]
    assert ends[1]["error"] == "gave up"


def test_a_failed_node_does_not_fail_the_run_by_itself(stub_agent_service):
    """The ``done`` frame stays the single authority on a run's outcome."""
    url, handler = stub_agent_service
    handler.stream_frames = [
        {"type": "node_start", "node": "fetch"},
        {"type": "node_end", "node": "fetch", "ok": False, "error": "timeout"},
        {"type": "token", "token": "recovered"},
        {"type": "done", "ok": True, "output": "answered from cache"},
    ]
    listener = _RecordingEmitter()

    result = _graph_agent(url).run("go", callbacks=[listener])

    assert result.ok is True
    assert result.agent_output == "answered from cache"


def test_node_frames_without_a_name_are_ignored(stub_agent_service):
    """A nameless node is nothing to draw, and must not break a good run."""
    url, handler = stub_agent_service
    handler.stream_frames = [
        {"type": "node_start"},
        {"type": "done", "ok": True, "output": "fine"},
    ]
    listener = _RecordingEmitter()

    result = _graph_agent(url).run("go", callbacks=[listener])

    assert listener.events == []
    assert result.ok is True


# ── topology ────────────────────────────────────────────────────────────────

def test_topology_is_fetched_and_normalised(stub_agent_service):
    url, _ = stub_agent_service

    topology = _graph_agent(url).fetch_topology()

    assert topology["ok"] is True
    assert topology["framework"] == "langgraph"
    assert [n["id"] for n in topology["nodes"]] == ["triage", "answer"]
    assert topology["edges"][0]["conditional"] is True


def test_topology_is_opt_in(stub_agent_service):
    """No ``graph_path`` means no probe: most agents are not graphs."""
    url, _ = stub_agent_service
    agent = RemoteAgent(agent_id="plain", name="Plain", remote={"url": url})

    assert agent.supports_topology is False
    assert agent.fetch_topology()["ok"] is False


def test_an_unreachable_topology_is_a_reason_not_a_crash():
    agent = RemoteAgent(
        agent_id="graph", name="Graph",
        remote={"url": "http://127.0.0.1:9", "graph_path": "/graph"},
    )

    topology = agent.fetch_topology(timeout=1)

    assert topology["ok"] is False
    assert topology["nodes"] == []
    assert topology["error"]


def test_dangling_edges_are_dropped_rather_than_drawn():
    """A renderer handed an edge to a node that does not exist invents one."""
    topology = normalize_topology({
        "framework": "langgraph",
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [{"source": "a", "target": "b"}, {"source": "a", "target": "ghost"}],
    })

    assert [(e["source"], e["target"]) for e in topology["edges"]] == [("a", "b")]


def test_a_topology_is_bounded_before_it_reaches_a_browser():
    from agents.remote_agent import MAX_TOPOLOGY_LABEL, MAX_TOPOLOGY_NODES

    topology = normalize_topology({
        "nodes": [{"id": f"n{i}", "label": "x" * 500} for i in range(MAX_TOPOLOGY_NODES + 50)],
        "edges": [],
    })

    assert len(topology["nodes"]) == MAX_TOPOLOGY_NODES
    assert len(topology["nodes"][0]["label"]) <= MAX_TOPOLOGY_LABEL + 1


def test_a_topology_with_no_nodes_is_not_ok():
    """Better a stated reason than an empty canvas that looks like a bug."""
    topology = normalize_topology({"nodes": [], "edges": []})

    assert topology["ok"] is False
    assert topology["error"]


# ── the manifest side ───────────────────────────────────────────────────────

def test_the_bundled_langgraph_example_declares_its_graph(graph_example_repo):
    manifest = parse_manifest(graph_example_repo)

    assert manifest.problems == []
    assert manifest.graph_path == "/graph"
    assert manifest.stream_path == "/run/stream"


def test_the_bundled_js_example_declares_the_same_contract(js_graph_example_repo):
    """The JavaScript adapter is a different runtime, not a different contract."""
    manifest = parse_manifest(js_graph_example_repo)

    assert manifest.problems == []
    assert manifest.graph_path == "/graph"
    assert manifest.stream_path == "/run/stream"
    assert manifest.resume_path == "/resume"
    # A distinct id, so both examples can be imported into one hub and compared
    # side by side, which is the whole point of having two.
    assert manifest.id == "langgraph-agent-js"
    assert manifest.id != parse_manifest(GRAPH_EXAMPLE_DIR).id
    # The graph is named the same way on both sides, and it is required on both:
    # an adapter serving some other graph than the one that was meant is the
    # failure this check exists to prevent.
    required = {e.name for e in manifest.env if e.required}
    assert required == {"AGENTHUB_GRAPH"}
    assert manifest.dockerfile == "Dockerfile.agenthub"


def test_graph_path_reaches_the_descriptor_and_the_topology_is_stored(
    graph_example_repo, stub_agent_service, monkeypatch,
):
    url, _ = stub_agent_service
    monkeypatch.setenv("AGENTHUB_GRAPH", "demo_graph:graph")

    inspected = import_service.inspect(str(graph_example_repo), url=url)
    import_service.register(
        inspected["token"], repo_url=str(graph_example_repo),
        agent_id="langgraph-agent", url=url,
    )

    remote = registry.get_agent("langgraph-agent").remote
    assert remote["graph_path"] == "/graph"
    # Registration is also when the picture is first fetched, so the agent page
    # has something to show without a second click.
    assert [n["id"] for n in remote["topology"]["nodes"]] == ["triage", "answer"]


def test_the_graph_check_is_absent_for_agents_that_are_not_graphs(tmp_path):
    """An agent with no graph should not carry a permanent "no graph" warning."""
    (tmp_path / "agent-hub.json").write_text(json.dumps({
        "schema": "agents-hub/agent-manifest@1",
        "id": "plain", "name": "Plain",
        "runtime": {"kind": "http", "run_path": "/run"},
    }))

    assert checks.check_graph(parse_manifest(tmp_path)) is None


def test_the_graph_check_is_a_note_when_declared(graph_example_repo):
    check = checks.check_graph(parse_manifest(graph_example_repo))

    assert check is not None
    assert check.ok is True
    assert check.required is False, "a graph endpoint is a bonus, never a blocker"


# ── agents that pause ───────────────────────────────────────────────────────
#
# An imported agent may stop to ask a person. The hub already has a word for
# that state — ``awaiting_input``, which its own agents reach through ask_user —
# so a remote one reports into the same state and the same surfaces. What is new
# is the way back: an agent that suspended onto a checkpointer has somewhere to
# return to, and running it again with the answer in its prompt is a different
# execution that merely reads the same way.


def _resumable_agent(url):
    return RemoteAgent(
        agent_id="approver", name="Approver",
        remote={"url": url, "stream_path": "/run/stream", "resume_path": "/resume"},
    )


def test_a_stream_that_ends_in_an_interrupt_is_a_paused_run(stub_agent_service):
    url, handler = stub_agent_service
    handler.stream_frames = [
        {"type": "node_start", "node": "approve"},
        {"type": "node_end", "node": "approve", "status": "interrupted"},
        {"type": "interrupt", "question": "Approve this plan?",
         "choices": ["approve", "reject"], "key": "i-7", "node": "approve"},
    ]
    listener = _RecordingEmitter()

    result = _resumable_agent(url).run("do it", callbacks=[listener])

    # The hub's own word for it, so the task runner parks this exactly as it
    # parks a local agent that called ask_user.
    assert result.status == "awaiting_input"
    assert result.ok is True
    assert result.pending_question["question"] == "Approve this plan?"
    assert result.pending_question["choices"] == ["approve", "reject"]
    assert result.agent_output == "Approve this plan?", "plain surfaces show the question"
    assert "awaiting_input" in [e["type"] for e in listener.events]


def test_resume_sends_the_answer_to_the_paused_run(stub_agent_service):
    url, handler = stub_agent_service
    agent = _resumable_agent(url)

    result = agent.resume("run-42", "approve", key="i-7")

    assert handler.resumed[-1] == {
        "run_id": "run-42", "value": "approve", "key": "i-7", "workspace": None,
    }
    assert result.ok is True
    assert result.agent_output == "finished after the answer"


def test_an_agent_can_pause_again_on_the_way(stub_agent_service):
    """An approval flow with two approvals is an ordinary thing."""
    url, handler = stub_agent_service
    handler.resume_frames = [
        {"type": "interrupt", "question": "And deploy to production?", "choices": ["yes"]},
    ]

    result = _resumable_agent(url).resume("run-42", "approve")

    assert result.status == "awaiting_input"
    assert result.pending_question["question"] == "And deploy to production?"


def test_resuming_an_agent_that_cannot_be_continued_says_so(stub_agent_service):
    url, _ = stub_agent_service
    agent = RemoteAgent(agent_id="plain", name="Plain",
                        remote={"url": url, "stream_path": "/run/stream"})

    result = agent.resume("run-42", "yes")

    assert result.ok is False
    assert agent.supports_resume is False
    assert "resume_path" in result.error


def test_a_resume_that_cannot_reach_the_agent_is_a_failed_run_not_a_crash():
    agent = RemoteAgent(agent_id="approver", name="Approver",
                        remote={"url": "http://127.0.0.1:9", "resume_path": "/resume"})

    result = agent.resume("run-42", "yes")

    assert result.ok is False
    assert "Could not reach" in result.error


def test_the_bundled_example_declares_that_it_can_be_resumed(graph_example_repo):
    manifest = parse_manifest(graph_example_repo)

    assert manifest.resume_path == "/resume"
    check = checks.check_resume(manifest)
    assert check is not None and check.ok is True and check.required is False


def test_resume_path_reaches_the_descriptor(graph_example_repo, stub_agent_service):
    url, _ = stub_agent_service
    inspected = import_service.inspect(str(graph_example_repo), url=url)
    import_service.register(inspected["token"], repo_url=str(graph_example_repo),
                            agent_id="langgraph-agent", url=url)

    assert registry.get_agent("langgraph-agent").remote["resume_path"] == "/resume"


def test_an_agent_that_never_pauses_carries_no_resume_check(tmp_path):
    (tmp_path / "agent-hub.json").write_text(json.dumps({
        "schema": "agents-hub/agent-manifest@1",
        "id": "plain", "name": "Plain",
        "runtime": {"kind": "http", "run_path": "/run"},
    }))

    assert checks.check_resume(parse_manifest(tmp_path)) is None


def test_answering_a_task_continues_a_remote_agent_instead_of_restarting_it(
    example_repo, stub_agent_service, monkeypatch, no_env,
):
    """The difference the resume endpoint exists for.

    This hub resumes its own agents by running them again with the answer in the
    prompt, which is correct for an agent that keeps nothing between runs. An
    imported agent that suspended onto a checkpointer is the other case: it has
    somewhere to come back to, and a fresh run would be a different execution
    that merely reads the same way.
    """
    from uuid import uuid4

    from tasks import service as tasks_service

    url, _ = stub_agent_service
    inspected = import_service.inspect(str(example_repo), url=url)
    import_service.register(inspected["token"], repo_url=str(example_repo),
                            agent_id="aider", url=url)
    spec = registry.get_agent("aider")
    import dataclasses
    registry.add_agent(dataclasses.replace(
        spec, remote={**spec.remote, "resume_path": "/resume"}))

    task = tasks_service.create_task(title="Ship it", description="ship the release")
    paused_run = str(uuid4())
    tasks_service.update_task(
        task.id,
        status=tasks_service.TaskStatus.awaiting_input,
        assigned_agent_type="aider",
        pending_question={
            "question": "Approve this plan?", "choices": ["approve", "reject"],
            "agent_id": "aider", "run_id": paused_run, "key": "i-7",
        },
    )

    launched = {}

    def _capture(task_id, agent_id, params=None, run_id=None):
        launched["params"] = params or {}
        return ("new-run", "session-1")

    import agents.agent_launcher as launcher
    monkeypatch.setattr(launcher, "start_run", _capture)
    import sys
    from pathlib import Path as _Path

    backend = str(_Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import tasks as task_routes

    app = FastAPI()
    app.include_router(task_routes.router)
    client = TestClient(app)

    resp = client.post(f"/api/tasks/{task.id}/answer", json={"answer": "approve"})

    assert resp.status_code == 200
    resume = launched["params"].get("resume")
    assert resume == {"run_id": paused_run, "value": "approve", "key": "i-7"}, (
        "the answer must continue the run that paused, not start a new one")
    # And the re-run prompt, which would restart the graph, is not what carries
    # the answer any more.
    assert "You previously paused" not in (launched["params"].get("description") or "")


def test_a_pause_is_reported_even_when_nobody_is_streaming(stub_agent_service):
    """Streaming needs a listener on this side; a pause must not.

    A run nobody is watching still has to be able to stop and ask. Reported as
    finished instead, the graph would sit suspended with nothing that will ever
    answer it, and the run record would say it completed.
    """
    url, handler = stub_agent_service
    handler.reply = {
        "ok": True,
        "interrupt": {"question": "Approve this plan?", "choices": ["approve", "reject"],
                      "key": "i-7", "node": "approve"},
        "output": "Approve this plan?",
    }
    agent = _resumable_agent(url)

    result = agent.run("do it")  # no callbacks: the single-shot POST path

    assert result.status == "awaiting_input"
    assert result.pending_question["choices"] == ["approve", "reject"]
    assert result.pending_question["key"] == "i-7"


# ── importing an A2A agent from its card ────────────────────────────────────

class _CardHandler(BaseHTTPRequestHandler):
    """A stand-in for an agent that speaks A2A: it serves a card and JSON-RPC."""

    card = {
        "protocolVersion": "0.3.0",
        "name": "Researcher",
        "description": "Answers research questions.",
        "version": "3",
        "capabilities": {"streaming": True},
        "skills": [{"id": "research", "name": "Research", "tags": ["web"]},
                   {"id": "summarise", "name": "Summarise", "tags": []}],
    }
    status = 200

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.rstrip("/").endswith((".well-known/agent-card.json", ".well-known/agent.json")):
            card = dict(type(self).card)
            # The card's own url is what the hub posts to, so it has to name
            # this very server rather than a placeholder.
            card.setdefault("url", f"http://127.0.0.1:{self.server.server_port}")
            self._send(type(self).status, card)
            return
        self._send(404, {"error": "not found"})

    def _send(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def stub_card_service():
    """Serves an A2A agent card; yields its card URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (f"http://127.0.0.1:{server.server_port}/.well-known/agent-card.json",
               f"http://127.0.0.1:{server.server_port}", _CardHandler)
    finally:
        server.shutdown()
        server.server_close()
        _CardHandler.status = 200


def test_a_card_url_is_told_apart_from_a_repository():
    from agents.importer import a2a_import

    assert a2a_import.is_card_url("https://x.dev/.well-known/agent-card.json") is True
    assert a2a_import.is_card_url("https://x.dev/.well-known/agent.json") is True
    assert a2a_import.is_card_url("https://github.com/owner/repo") is False
    assert a2a_import.is_card_url("/tmp/local/repo") is False


def test_a_manifest_may_declare_the_a2a_runtime(tmp_path):
    """A repository is still allowed to declare A2A: the agent is running
    elsewhere and the manifest simply names its endpoint."""
    (tmp_path / "agent-hub.json").write_text(json.dumps({
        "schema": "agents-hub/agent-manifest@1",
        "id": "remote-a2a", "name": "Remote A2A",
        "runtime": {"kind": "a2a", "url": "https://agent.example.com/rpc",
                    "card_url": "https://agent.example.com/.well-known/agent-card.json"},
    }))
    manifest = parse_manifest(tmp_path)
    assert manifest.problems == []
    assert manifest.runtime_kind == "a2a"
    assert manifest.card_url.endswith("agent-card.json")


def test_inspecting_a_card_url_clones_nothing(stub_card_service):
    card_url, endpoint, _ = stub_card_service

    inspected = import_service.inspect(card_url)

    assert inspected["token"] == "", "there is nothing staged to promote or discard"
    assert inspected["manifest"]["runtime_kind"] == "a2a"
    assert inspected["manifest"]["url"] == endpoint
    assert inspected["suggested"]["id"] == "researcher"
    assert inspected["card"]["skills"] == ["research", "summarise"]
    assert inspected["report"]["runnable"] is True
    ids = {c["id"] for c in inspected["report"]["checks"]}
    # No repository and nothing to package: the card answers both questions.
    assert "card" in ids and "repo" not in ids and "packaging" not in ids


def test_registering_from_a_card_stores_the_a2a_descriptor(stub_card_service):
    card_url, endpoint, _ = stub_card_service

    import_service.register("", repo_url=card_url, agent_id="researcher")

    spec = registry.get_agent("researcher")
    assert spec.is_remote()
    assert spec.remote["kind"] == "a2a"
    assert spec.remote["url"] == endpoint
    assert spec.remote["card_url"] == card_url
    # Copied off the card so a run need not fetch it again to find out.
    assert spec.remote["streaming"] is True
    assert spec.remote["manifest"]["tools"] == ["research", "summarise"]


def test_an_a2a_import_builds_a_remote_agent_that_speaks_a2a(stub_card_service):
    from agents.agent_factory import create_agent

    card_url, endpoint, _ = stub_card_service
    import_service.register("", repo_url=card_url, agent_id="researcher")

    agent = create_agent("researcher")
    assert isinstance(agent, RemoteAgent)
    assert agent.is_a2a is True
    # One endpoint, every method: there is no path to append.
    assert agent.run_url == endpoint
    assert agent.supports_resume is True


def test_a_card_that_cannot_be_read_is_an_import_error(stub_card_service):
    card_url, _, handler = stub_card_service
    handler.status = 503

    with pytest.raises(import_service.ImportError_) as err:
        import_service.inspect(card_url)
    assert "503" in str(err.value)


def test_rechecking_an_a2a_agent_re_reads_its_card(stub_card_service):
    card_url, _, handler = stub_card_service
    import_service.register("", repo_url=card_url, agent_id="researcher")

    handler.card = {**_CardHandler.card, "capabilities": {"streaming": False}}
    try:
        result = import_service.recheck("researcher")
    finally:
        handler.card = {**_CardHandler.card, "capabilities": {"streaming": True}}

    assert result["runnable"] is True
    assert registry.get_agent("researcher").remote["streaming"] is False


def test_the_import_api_accepts_a_card_url(api_client, stub_card_service):
    card_url, endpoint, _ = stub_card_service

    inspected = api_client.post("/api/agent-import/inspect", json={"repo_url": card_url}).json()
    assert inspected["report"]["runnable"] is True

    registered = api_client.post("/api/agent-import/register", json={
        "token": inspected["token"],
        "repo_url": card_url,
        "agent_id": inspected["suggested"]["id"],
    }).json()
    assert registered["runnable"] is True
    assert registered["agent"]["remote"]["kind"] == "a2a"
