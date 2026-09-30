"""Workspace files where they are used: a memory pool, a task, an eval case
and the agent tools, plus "where used" reading every one of them back.

No model and no vector store: RAG is forced to BM25 over the chunk store, and
the task run goes through ``runtime.agent_run.main`` with the agent build and
invocation stubbed, the same seam tests/test_runtime_smoke.py uses.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from files import service  # noqa: E402
from files.usage import where_used  # noqa: E402


@pytest.fixture(autouse=True)
def _no_vectors(monkeypatch):
    monkeypatch.setenv("RAG_VECTOR_DB", "none")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "none")
    monkeypatch.delenv(service.MAX_FILE_MB_ENV, raising=False)
    monkeypatch.delenv(service.MAX_WORKSPACE_MB_ENV, raising=False)
    from rag import bm25
    bm25.clear_cache()
    yield
    bm25.clear_cache()


def _memory_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.memory as memory_routes

    app = FastAPI()
    app.include_router(memory_routes.router)
    return TestClient(app)


# ── memory ───────────────────────────────────────────────────────────────────

def test_a_workspace_file_is_added_to_a_pool_and_indexed():
    from memory.models import SharedMemory
    from memory.store import MemoryStore
    from rag.chunk_store import pool_file_index

    mem = MemoryStore().add(SharedMemory(name="kb", workspace="acme"))
    rec = service.create_file("acme", "handbook.md",
                              b"# Leave\n\nEvery employee gets 30 days of paid leave a year.\n")
    api = _memory_client()
    resp = api.post(f"/api/shared-memory/{mem.id}/files/from-workspace", json={"file_id": rec["file_id"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "indexed" and body["chunks"] >= 1
    assert body["workspace_file_id"] == rec["file_id"] and body["filename"] == "handbook.md"

    entry = MemoryStore().get(mem.id).rag_files[0]
    assert entry["workspace_file_id"] == rec["file_id"] and entry["status"] == "indexed"
    assert "handbook.md" in pool_file_index(str(mem.id))
    listing = api.get(f"/api/shared-memory/{mem.id}/files", params={"workspace": "acme"}).json()["files"]
    assert [f["workspace_file_id"] for f in listing if f["filename"] == "handbook.md"] == [rec["file_id"]]
    assert where_used(rec["file_id"])["memory_pools"][0]["pool_id"] == str(mem.id)

    # Added twice: the same copy, re-indexed, one entry.
    again = api.post(f"/api/shared-memory/{mem.id}/files/from-workspace", json={"file_id": rec["file_id"]})
    assert again.status_code == 200 and again.json()["filename"] == "handbook.md"
    assert len(MemoryStore().get(mem.id).rag_files) == 1


def test_a_pool_of_another_workspace_and_a_binary_file_are_refused():
    from memory.models import SharedMemory
    from memory.store import MemoryStore
    from common.paths import workspace_knowledge_dir

    other = MemoryStore().add(SharedMemory(name="other", workspace="beta"))
    rec = service.create_file("acme", "a.md", b"# A\ntext")
    api = _memory_client()
    resp = api.post(f"/api/shared-memory/{other.id}/files/from-workspace", json={"file_id": rec["file_id"]})
    assert resp.status_code == 400

    mine = MemoryStore().add(SharedMemory(name="mine", workspace="acme"))
    img = service.create_file("acme", "photo.png", b"\x89PNG\r\n\x1a\n\x00\x00")
    resp = api.post(f"/api/shared-memory/{mine.id}/files/from-workspace", json={"file_id": img["file_id"]})
    assert resp.status_code == 415
    # The copy made for the attempt does not stay behind.
    assert not (workspace_knowledge_dir("acme") / "photo.png").exists()
    missing = api.post(f"/api/shared-memory/{mine.id}/files/from-workspace",
                       json={"file_id": "file_0000000000000000"})
    assert missing.status_code == 404


# ── tasks ────────────────────────────────────────────────────────────────────

def test_task_files_are_copied_into_the_run_dir_and_named(tmp_path):
    from tasks import service as tasks_service
    from tasks.context import build_task_instruction

    spec = service.create_file("acme", "spec.md", b"The widget is blue.")
    task = tasks_service.create_task("Build the widget", workspace="acme", file_ids=[spec["file_id"]])
    assert tasks_service.get_task(task.id).file_ids == [spec["file_id"]]

    work = tmp_path / "work"
    text = build_task_instruction(str(task.id), "go", work_dir=str(work))
    assert (work / "task_files" / "spec.md").read_bytes() == b"The widget is blue."
    assert "FILES ATTACHED TO THIS TASK" in text
    assert f"task_files/spec.md  (spec.md ({spec['file_id']}" in text
    # A second run reuses the copy rather than adding spec-2.md.
    build_task_instruction(str(task.id), "go", work_dir=str(work))
    assert sorted(p.name for p in (work / "task_files").iterdir()) == ["spec.md"]

    # Without a working directory the files are named by id.
    named = build_task_instruction(str(task.id), "go")
    assert "read_workspace_file" in named and spec["file_id"] in named

    assert where_used(spec["file_id"])["tasks"][0]["task_id"] == str(task.id)


def test_a_subtask_inherits_the_files_unless_given_its_own():
    from tasks import service as tasks_service

    spec = service.create_file("acme", "spec.md", b"spec")
    parent = tasks_service.create_task("Parent", workspace="acme", file_ids=[spec["file_id"]])
    assert tasks_service.add_subtask(parent.id, "part").file_ids == [spec["file_id"]]
    assert tasks_service.add_subtask(parent.id, "own", file_ids=[]).file_ids == []


def test_an_agent_made_subtask_carries_the_parent_files():
    """The add_subtask agent tool and delegate_task_tool both create the child
    through tasks.service.add_subtask, so the files come along with the money
    cap (tests/test_delegate_task_tool.py covers the rest of delegation)."""
    from tasks import service as tasks_service
    from tools.task_management import add_subtask as add_subtask_tool

    spec = service.create_file("acme", "spec.md", b"spec")
    parent = tasks_service.create_task("Parent", workspace="acme", file_ids=[spec["file_id"]])
    out = json.loads(add_subtask_tool.invoke({"parent_id": str(parent.id), "title": "piece"}))
    assert out["ok"] and out["task"]["file_ids"] == [spec["file_id"]]


def test_the_task_routes_validate_file_ids():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.tasks as task_routes

    app = FastAPI()
    app.include_router(task_routes.router)
    api = TestClient(app)
    mine = service.create_file("acme", "mine.md", b"mine")
    theirs = service.create_file("beta", "theirs.md", b"theirs")

    created = api.post("/api/tasks", json={"title": "With files", "workspace_name": "acme",
                                           "file_ids": [mine["file_id"], mine["file_id"]]})
    assert created.status_code == 200, created.text
    task = created.json()
    assert task["file_ids"] == [mine["file_id"]]

    refused = api.post("/api/tasks", json={"title": "Wrong", "workspace_name": "acme",
                                           "file_ids": [theirs["file_id"]]})
    assert refused.status_code == 400
    patched = api.patch(f"/api/tasks/{task['id']}", json={"file_ids": []})
    assert patched.status_code == 200 and patched.json()["file_ids"] == []
    assert api.patch(f"/api/tasks/{task['id']}", json={"file_ids": [theirs["file_id"]]}).status_code == 400


def test_a_task_run_materializes_its_files_and_keeps_its_citations(monkeypatch, tmp_path):
    """runtime/agent_run.py end to end with the agent stubbed: the task's
    files land in the run's working directory and are named in the
    instruction, and a citation recorded during the run is stored on the run
    record as ``citations``."""
    import runtime.agent_run as ar
    import agents.registry as registry
    import agents.agent_lifecycle as al
    from common.citation_sink import record_citation
    from managers import run_manager
    from tasks import service as tasks_service
    from workspace import WORKSPACES_ROOT

    spec = service.create_file("runws", "brief.md", b"Ship on Friday.")
    task = tasks_service.create_task("Plan the launch", workspace="runws", file_ids=[spec["file_id"]])

    monkeypatch.setattr(registry, "get_agent", lambda agent_id: SimpleNamespace(id=agent_id))
    fake_agent = SimpleNamespace(provider="prov", model="mdl", system_prompt="sys")
    monkeypatch.setattr(al, "create_agent", lambda agent_id, workspace=None, **kw: fake_agent)
    seen = {}

    def _invoke(agent, instruction, **kw):
        seen["instruction"] = instruction
        record_citation(pool_id="p1", file_id="p1::brief.md", filename="brief.md", chunk_idx=0,
                        text="Ship on Friday.", workspace_file_id=spec["file_id"])
        stats = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2,
                                tool_calls=0, cached_prompt_tokens=0)
        return SimpleNamespace(result=SimpleNamespace(ok=True, agent_output="Friday [1]", error=None),
                               duration_ms=5, process={"tools": []}, stats=stats)

    monkeypatch.setattr(al, "invoke_agent", _invoke)
    monkeypatch.setenv("AGENT_LOG_FILE", str(tmp_path / "agent.log"))
    monkeypatch.setenv("AGENT_RUN_ID", "placeholder")
    monkeypatch.setenv("AGENT_WORKSPACE", "runws")
    # In-process entrypoint: the direct transport on either database (on
    # Postgres the default is the HTTP relay, which needs a backend).
    monkeypatch.setenv("AGENT_RUN_STATE_TRANSPORT", "db")
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    monkeypatch.setattr(sys, "argv", ["agent_run.py", "planner", "plan it", "--workspace", "runws",
                                      "--run-id", "run-files", "--task-id", str(task.id)])
    import common.agent_context as agent_context
    token = agent_context.current_task_id.set(None)
    try:
        with pytest.raises(SystemExit) as exc:
            ar.main()
    finally:
        agent_context.current_task_id.reset(token)
    assert exc.value.code == 0

    copy = Path(WORKSPACES_ROOT) / "runws" / "task_files" / "brief.md"
    assert copy.read_bytes() == b"Ship on Friday."
    assert "task_files/brief.md" in seen["instruction"]
    record = run_manager.get_run_by_id("run-files") or {}
    assert [c["n"] for c in record.get("citations") or []] == [1]
    assert record["citations"][0]["workspace_file_id"] == spec["file_id"]


# ── evals ────────────────────────────────────────────────────────────────────

def test_an_eval_case_runs_with_its_files():
    from evals import store as eval_store
    from evals.models import Case, EvalSet
    from evals.runner import compose_input, prepare_work_dir

    contract = service.create_file("acme", "contract.txt", b"Term: 12 months.")
    case = Case(input="How long is the term?", file_ids=[contract["file_id"]])
    assert Case.from_dict(case.to_dict()).file_ids == [contract["file_id"]]
    assert Case.from_dict({"input": "old case"}).file_ids == []

    work = prepare_work_dir(case, "evr_files", "acme")
    assert work and (Path(work) / "contract.txt").read_bytes() == b"Term: 12 months."
    prompt = compose_input(case, work)
    assert f"- contract.txt: contract.txt ({contract['file_id']}" in prompt
    assert prompt.endswith("How long is the term?")

    plain = Case(input="hi")
    assert prepare_work_dir(plain, "evr_files", "acme") is None
    assert compose_input(plain) == "hi"

    eval_store.save_eval_set(EvalSet(name="contracts", workspace="acme", cases=[case]))
    used = where_used(contract["file_id"])["eval_cases"]
    assert used and used[0]["case_id"] == case.case_id


# ── the agent tools ──────────────────────────────────────────────────────────

def test_the_agent_tools_list_read_and_save_in_the_run_workspace():
    from common import entity_sink
    from common.entity_links import entity_payloads
    from common.workspace_context import _workspace_ctx
    from tools.workspace_files import list_workspace_files, read_workspace_file, save_workspace_file

    theirs = service.create_file("beta", "theirs.txt", b"not yours")
    ws_token = _workspace_ctx.set("acme")
    sink = entity_sink.EntitySink()
    sink_token = entity_sink.set_sink(sink)
    try:
        saved = json.loads(save_workspace_file.invoke({"name": "summary.md",
                                                       "content": "# Summary\n" + "x" * 100}))
        assert saved["ok"] and saved["saved"]
        record = service.get_file(saved["file_id"])
        assert record["source"] == "agent" and record["workspace"] == "acme"
        links = entity_payloads(sink.records())
        assert links == [{"kind": "workspace_file", "id": saved["file_id"], "action": "created",
                          "title": "summary.md", "url": f"/files?file={saved['file_id']}",
                          "icon": "🗂️", "noun": "Workspace file"}]

        listed = json.loads(list_workspace_files.invoke({"query": "summ"}))
        assert [f["file_id"] for f in listed["files"]] == [saved["file_id"]]

        first = json.loads(read_workspace_file.invoke({"file_id": saved["file_id"], "max_chars": 20}))
        assert first["text"] == "# Summary\n" + "x" * 10 and first["next_offset"] == 20
        rest = json.loads(read_workspace_file.invoke({"file_id": saved["file_id"], "offset": 20,
                                                      "max_chars": 1000}))
        assert "next_offset" not in rest and rest["total_chars"] == 110

        refused = json.loads(read_workspace_file.invoke({"file_id": theirs["file_id"]}))
        assert refused["ok"] is False and refused["code"] == "not_found"
    finally:
        entity_sink.reset_sink(sink_token)
        _workspace_ctx.reset(ws_token)


def test_the_tools_are_in_the_catalog_with_their_capabilities():
    from tools.registry import get_tool_by_id

    assert get_tool_by_id("read_workspace_file").reads_private
    assert get_tool_by_id("list_workspace_files").reads_private
    save = get_tool_by_id("save_workspace_file")
    assert save is not None and not save.reads_private and not save.can_exfiltrate
