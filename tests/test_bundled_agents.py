"""Coverage for the bundled Claude Code and Codex examples and the pieces of
the import/cost pipeline that were extended for them.

Four things are exercised here, deliberately kept in one file since they are
one feature:

1. The two examples' manifests parse cleanly (``agents.importer.manifest``).
2. Their ``server.py`` translation classes turn recorded CLI output into the
   hub's frame vocabulary correctly, imported directly from the example
   directories, with no real ``claude``/``codex`` process and no API key.
3. The bundled-preset import path (``GET /presets``, and importing one by
   ``preset`` id through ``inspect``/``register`` with no repository URL and
   no network) works end to end against a real (throwaway, per-test) database.
4. A remote agent that reports its own dollar cost on a ``usage`` frame gets
   it credited to the run record as ``reported_cost_usd``, and
   ``managers.runs.groups.runs_cost`` prefers that figure over catalog pricing.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from agents import registry
from agents.importer import service as import_service
from agents.importer.manifest import parse_manifest
from agents.remote_agent import RemoteAgent

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "imported-agents"
CLAUDE_DIR = EXAMPLES / "claude-code-agenthub"
CODEX_DIR = EXAMPLES / "codex-agenthub"


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    """Keep generated definition markdown out of the real agents/definitions.

    Registering an agent (``test_importing_a_preset_clones_nothing`` and its
    Codex counterpart do) writes instructions.md/capabilities.md/usage.md for
    the new agent id through ``agents.prompt_assembly``. Without this redirect
    those land in the checked-out ``agents/definitions/claude-code`` and
    ``agents/definitions/codex`` for real, exactly the kind of stray write the
    task's isolation rule exists to prevent. Mirrors the identically named
    fixture in tests/test_agent_import.py.
    """
    from agents import prompt_assembly
    from agents.agent_factory import get_factory

    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    monkeypatch.setattr(get_factory(), "definitions_dir", defs)


def _load_module(name: str, path: Path):
    """Import an example's server.py directly from its directory.

    The bundled examples are not part of the ``agents_hub`` Python package:
    they run in their own container, with their own dependency tree, so this
    loads the file by path rather than by import statement, exactly what a
    reviewer reading ``server.py`` in isolation would run.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def claude_server():
    return _load_module("claude_code_agenthub_server", CLAUDE_DIR / "server.py")


@pytest.fixture(scope="module")
def codex_server():
    return _load_module("codex_agenthub_server", CODEX_DIR / "server.py")


# ── manifests ────────────────────────────────────────────────────────────────

def test_claude_code_manifest_parses_cleanly():
    manifest = parse_manifest(CLAUDE_DIR)
    assert manifest.found
    assert manifest.problems == []
    assert manifest.id == "claude-code"
    assert manifest.run_path == "/run"
    assert manifest.stream_path == "/run/stream"
    assert manifest.dockerfile == "Dockerfile.agenthub"
    required = {e.name for e in manifest.env if e.required}
    assert required == {"ANTHROPIC_API_KEY"}
    optional = {e.name for e in manifest.env if not e.required}
    assert optional == {"CLAUDE_MODEL", "ANTHROPIC_BASE_URL", "CLAUDE_WORKDIR"}


def test_codex_manifest_parses_cleanly():
    manifest = parse_manifest(CODEX_DIR)
    assert manifest.found
    assert manifest.problems == []
    assert manifest.id == "codex"
    assert manifest.run_path == "/run"
    assert manifest.stream_path == "/run/stream"
    assert manifest.dockerfile == "Dockerfile.agenthub"
    required = {e.name for e in manifest.env if e.required}
    assert required == {"OPENAI_API_KEY"}


# ── Claude Code translation ─────────────────────────────────────────────────

def test_claude_code_translates_text_into_token_frames(claude_server):
    translator = claude_server.ClaudeCodeTranslator()
    event = {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "reading the file"}]},
    }
    frames = translator.feed(event)
    assert frames == [{"type": "token", "token": "reading the file"}]


def test_claude_code_tool_use_becomes_tool_start_and_end(claude_server):
    translator = claude_server.ClaudeCodeTranslator()
    start = translator.feed({
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}},
        ]},
    })
    assert start == [{"type": "tool_start", "name": "Bash", "input": json.dumps({"command": "ls"})}]

    end = translator.feed({
        "type": "user",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "file1\nfile2"},
        ]},
    })
    assert end == [{"type": "tool_end", "name": "Bash", "output": "file1\nfile2"}]


def test_claude_code_tool_result_content_blocks_are_flattened(claude_server):
    """A tool_result's content is sometimes a list of {type, text} blocks
    rather than a bare string, the same shape the Messages API uses
    everywhere else."""
    translator = claude_server.ClaudeCodeTranslator()
    translator.feed({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]},
    })
    end = translator.feed({
        "type": "user",
        "message": {"content": [{
            "type": "tool_result", "tool_use_id": "t1",
            "content": [{"type": "text", "text": "line one"}, {"type": "text", "text": "line two"}],
        }]},
    })
    assert end == [{"type": "tool_end", "name": "Read", "output": "line oneline two"}]


def test_claude_code_result_reports_usage_and_cost(claude_server):
    translator = claude_server.ClaudeCodeTranslator()
    frames = translator.feed({
        "type": "result", "subtype": "success", "is_error": False,
        "result": "done editing api/users.py", "num_turns": 3, "session_id": "sess-1",
        "total_cost_usd": 0.0412,
        "usage": {"input_tokens": 812, "output_tokens": 340,
                  "cache_creation_input_tokens": 100, "cache_read_input_tokens": 6100},
    })
    usage, done = frames
    assert usage == {
        "type": "usage",
        "prompt_tokens": 812 + 100,
        "completion_tokens": 340,
        "total_tokens": 812 + 100 + 340,
        "cached_tokens": 6100,
        "cost_usd": 0.0412,
    }
    assert done == {"type": "done", "ok": True, "output": "done editing api/users.py", "error": None}
    assert translator.total_cost_usd == 0.0412


def test_claude_code_error_result_is_a_failed_done_frame(claude_server):
    translator = claude_server.ClaudeCodeTranslator()
    frames = translator.feed({
        "type": "result", "subtype": "error_max_turns", "is_error": True,
        "result": "", "usage": {}, "session_id": "sess-2",
    })
    done = frames[-1]
    assert done["ok"] is False
    assert done["error"] == "error_max_turns"


def test_claude_code_skips_the_system_init_event(claude_server):
    translator = claude_server.ClaudeCodeTranslator()
    assert translator.feed({"type": "system", "subtype": "init", "session_id": "s"}) == []


def test_claude_code_full_transcript_end_to_end(claude_server):
    """A whole recorded run, fed line by line as the streaming endpoint would."""
    lines = [
        {"type": "system", "subtype": "init", "session_id": "sess-3"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Looking at the repo. "}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_9", "name": "Bash", "input": {"command": "pytest -q"}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_9", "content": "3 passed"},
        ]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "All green."}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "All green.",
         "total_cost_usd": 0.011,
         "usage": {"input_tokens": 500, "output_tokens": 90,
                   "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}},
    ]
    translator = claude_server.ClaudeCodeTranslator()
    frames = [f for line in lines for f in translator.feed(line)]
    kinds = [f["type"] for f in frames]
    assert kinds == ["token", "tool_start", "tool_end", "token", "usage", "done"]
    assert frames[-1]["ok"] is True
    assert frames[-1]["output"] == "All green."
    assert frames[-2]["cost_usd"] == 0.011


# ── Codex translation ────────────────────────────────────────────────────────

def test_codex_agent_message_becomes_a_token_frame(codex_server):
    translator = codex_server.CodexTranslator()
    frames = translator.feed({
        "type": "item.completed",
        "item": {"id": "i1", "type": "agent_message", "text": "Patched the retry helper."},
    })
    assert frames == [{"type": "token", "token": "Patched the retry helper."}]


def test_codex_reasoning_becomes_a_thinking_frame(codex_server):
    translator = codex_server.CodexTranslator()
    frames = translator.feed({
        "type": "item.completed",
        "item": {"id": "i2", "type": "reasoning", "text": "Checking the failing test first."},
    })
    assert frames == [{"type": "thinking", "message": "Checking the failing test first."}]


def test_codex_command_execution_becomes_tool_start_and_end(codex_server):
    translator = codex_server.CodexTranslator()
    started = translator.feed({
        "type": "item.started",
        "item": {"id": "cmd1", "type": "command_execution", "command": "pytest -q"},
    })
    assert started == [{"type": "tool_start", "name": "command_execution", "input": "pytest -q"}]

    completed = translator.feed({
        "type": "item.completed",
        "item": {"id": "cmd1", "type": "command_execution", "exit_code": 0,
                  "aggregated_output": "3 passed"},
    })
    assert completed == [{"type": "tool_end", "name": "command_execution", "output": "3 passed"}]


def test_codex_turn_completed_reports_usage_without_cost(codex_server):
    translator = codex_server.CodexTranslator()
    translator.feed({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "Done."}})
    frames = translator.feed({
        "type": "turn.completed",
        "usage": {"input_tokens": 400, "cached_input_tokens": 50, "output_tokens": 120},
    })
    usage, done = frames
    assert usage == {
        "type": "usage", "prompt_tokens": 400, "completion_tokens": 120,
        "total_tokens": 520, "cached_tokens": 50,
    }
    assert "cost_usd" not in usage
    assert done == {"type": "done", "ok": True, "output": "Done.", "error": None}


def test_codex_turn_failed_is_a_failed_done_frame(codex_server):
    translator = codex_server.CodexTranslator()
    frames = translator.feed({"type": "turn.failed", "message": "sandbox denied the command"})
    assert frames == [{"type": "done", "ok": False, "output": "", "error": "sandbox denied the command"}]


def test_codex_unknown_event_types_are_skipped(codex_server):
    translator = codex_server.CodexTranslator()
    assert translator.feed({"type": "thread.started", "thread_id": "t1"}) == []
    assert translator.feed({"type": "turn.started"}) == []


# ── presets: listing and importing without a repository ────────────────────

@pytest.fixture
def api_client():
    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agent_import as agent_import_routes

    app = FastAPI()
    app.include_router(agent_import_routes.router)
    return TestClient(app)


def test_presets_endpoint_lists_both_bundled_agents(api_client):
    body = api_client.get("/api/agent-import/presets").json()
    presets = {p["id"]: p for p in body["presets"]}
    assert set(presets) == {"claude-code", "codex"}
    assert presets["claude-code"]["available"] is True
    assert presets["claude-code"]["agent_id"] == "claude-code"
    assert presets["claude-code"]["docker"] is True
    assert "ANTHROPIC_API_KEY" in {e["name"] for e in presets["claude-code"]["env"]}
    assert presets["codex"]["available"] is True
    assert "OPENAI_API_KEY" in {e["name"] for e in presets["codex"]["env"]}


def test_unknown_preset_is_a_client_error(api_client):
    resp = api_client.post("/api/agent-import/inspect", json={"preset": "not-a-real-preset"})
    assert resp.status_code == 400


def test_inspect_requires_either_a_repo_url_or_a_preset(api_client):
    resp = api_client.post("/api/agent-import/inspect", json={})
    assert resp.status_code == 400


def test_importing_a_preset_clones_nothing(monkeypatch):
    """A preset import stages a *copy* of the bundled example, never a git
    clone: the example is not a git repository, so a clone would fail."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    inspected = import_service.inspect(preset="claude-code")
    assert inspected["repo_url"] == "preset:claude-code"
    assert inspected["manifest"]["id"] == "claude-code"
    assert inspected["report"]["checks"]

    registered = import_service.register(
        inspected["token"], repo_url=inspected["repo_url"], agent_id="claude-code",
    )
    assert registered["agent"]["type"] == "remote"
    spec = registry.get_agent("claude-code")
    assert spec is not None
    assert spec.remote["repo_url"] == "preset:claude-code"
    # The staged copy was promoted to the agent's own clone directory, not to
    # a `.git` history: proof that promote() worked on a plain directory copy.
    assert (Path(spec.remote["clone_path"]) / "server.py").is_file()
    assert not (Path(spec.remote["clone_path"]) / ".git").exists()


def test_importing_the_codex_preset_also_works(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    inspected = import_service.inspect(preset="codex")
    registered = import_service.register(
        inspected["token"], repo_url=inspected["repo_url"], agent_id="codex",
    )
    assert registered["agent"]["type"] == "remote"
    assert registry.get_agent("codex").remote["repo_url"] == "preset:codex"


# ── cost crediting ───────────────────────────────────────────────────────────

class _CostStubHandler(BaseHTTPRequestHandler):
    """A remote that reports its usage, including a cost_usd, on a `usage`
    frame: the shape ClaudeCodeTranslator produces."""

    stream_frames = [
        {"type": "token", "token": "hi"},
        {"type": "usage", "prompt_tokens": 900, "completion_tokens": 300,
         "total_tokens": 1200, "cached_tokens": 100, "cost_usd": 0.0412},
        {"type": "done", "ok": True, "output": "hi"},
    ]

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for frame in type(self).stream_frames:
            self.wfile.write((json.dumps(frame) + "\n").encode())
            self.wfile.flush()


@pytest.fixture
def cost_stub_service():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CostStubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


class _RecordingEmitter:
    def __init__(self):
        self.events = []
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def emit_external(self, payload):
        self.events.append(payload)


def _open_test_run(run_id: str, **extra: Any) -> None:
    from managers.runs.lifecycle import open_run
    open_run(run_id, "claude-code", status="running", **extra)


def test_reported_cost_is_credited_to_the_run_record(cost_stub_service):
    """A remote's own reported cost_usd lands on the run as reported_cost_usd,
    which is the field managers.runs.groups.runs_cost and the Costs route
    prefer over catalog pricing."""
    from managers.runs.store import get_runs_by_ids

    run_id = "run-cost-1"
    _open_test_run(run_id)

    agent = RemoteAgent(
        agent_id="claude-code", name="Claude Code",
        remote={"url": cost_stub_service, "stream_path": "/run/stream"},
    )
    listener = _RecordingEmitter()
    result = agent.run("go", callbacks=[listener], run_id=run_id)

    assert result.ok is True
    record = get_runs_by_ids([run_id])[run_id]
    assert record["reported_cost_usd"] == pytest.approx(0.0412)
    # Tokens are still credited exactly as before this change.
    assert listener.prompt_tokens == 900
    assert listener.completion_tokens == 300


def test_runs_cost_prefers_the_reported_figure_over_catalog_pricing(monkeypatch):
    from managers.runs import groups
    from managers.runs.store import update_run

    run_id = "run-cost-2"
    _open_test_run(run_id, provider="anthropic", model="claude-priced-test")
    update_run(run_id, {
        "reported_cost_usd": 5.0,
        "process": {"token_usage": {"inbound_tokens": 1000, "outbound_tokens": 1000,
                                     "total_tokens": 2000, "cached_tokens": 0}},
    })

    # A price that, applied to the tokens above, would give a very different
    # (and much cheaper) figure than the reported one: proof runs_cost is not
    # merely falling back to zero-priced-unknown-model.
    monkeypatch.setattr(
        "common.pricing.load_price_map",
        lambda: {("anthropic", "claude-priced-test"): (1.0, 1.0, 0.1)},
    )

    assert groups.runs_cost([run_id]) == pytest.approx(5.0)


def test_runs_cost_falls_back_to_catalog_pricing_without_a_reported_cost(monkeypatch):
    from managers.runs import groups

    run_id = "run-cost-3"
    _open_test_run(run_id, provider="anthropic", model="claude-priced-test")
    from managers.runs.store import update_run
    update_run(run_id, {
        "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 1_000_000,
                                     "total_tokens": 2_000_000, "cached_tokens": 0}},
    })
    monkeypatch.setattr(
        "common.pricing.load_price_map",
        lambda: {("anthropic", "claude-priced-test"): (2.0, 4.0, 0.2)},
    )
    # 1M input tokens @ $2/M + 1M output tokens @ $4/M = $6.
    assert groups.runs_cost([run_id]) == pytest.approx(6.0)


def test_cost_is_not_credited_without_a_run_id(cost_stub_service):
    """RemoteAgent.run() called with no run_id (the exact shape the existing
    test suite in test_agent_import.py exercises) must not raise just because
    there is nowhere to persist a reported cost."""
    agent = RemoteAgent(
        agent_id="claude-code", name="Claude Code",
        remote={"url": cost_stub_service, "stream_path": "/run/stream"},
    )
    listener = _RecordingEmitter()
    result = agent.run("go", callbacks=[listener])
    assert result.ok is True
