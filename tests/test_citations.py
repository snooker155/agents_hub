"""RAG citations: numbered passages from search_memory / recall, the [n] they
are cited by, and the places they travel to (the chat ``done`` event, the run
record the run page reads, a flow node's run).

No model and no vector store: RAG is forced to BM25 over the chunk store, and
the agent in the pipeline tests is a stand-in whose ``arun`` calls the real
memory tool, the way a model's tool call would.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common import citation_sink  # noqa: E402
from common.citation_sink import CitationSink, record_citation  # noqa: E402


@pytest.fixture(autouse=True)
def _no_vectors(monkeypatch):
    monkeypatch.setenv("RAG_VECTOR_DB", "none")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "none")
    from rag import bm25
    bm25.clear_cache()
    yield
    bm25.clear_cache()


@pytest.fixture
def sink():
    s = CitationSink()
    token = citation_sink.set_sink(s)
    try:
        yield s
    finally:
        citation_sink.reset_sink(token)


def _pool_with_handbook(workspace_file_id: str = "file_00000000000000aa"):
    """A pool with one indexed document (two passages) and one note."""
    from memory.models import SharedMemory
    from memory.store import MemoryStore
    from rag.chunk_store import replace_file_chunks
    from rag.chunking import Chunk

    mem = SharedMemory(name="kb", workspace="acme",
                       notes=[{"id": "n1", "title": "Leave policy note",
                               "content": "Unused leave days carry over to March."}],
                       rag_files=[{"filename": "handbook.md", "workspace": "acme", "status": "indexed",
                                   "chunks": 2, "workspace_file_id": workspace_file_id}])
    MemoryStore().add(mem)
    pool = str(mem.id)
    texts = ["Every employee gets 30 days of paid leave a year.",
             "Laptops are replaced every three years."]
    chunks = [Chunk(text=t, heading_path=["Handbook", "Leave" if i == 0 else "Equipment"],
                    char_start=0, char_end=len(t)) for i, t in enumerate(texts)]
    replace_file_chunks(pool, "handbook.md", f"{pool}::handbook.md", chunks, content_hash="h1")
    return mem


# ── the sink ─────────────────────────────────────────────────────────────────

def test_numbers_are_stable_per_passage_and_ordered(sink):
    a = record_citation(pool_id="p", file_id="p::a.md", filename="a.md", chunk_idx=0, text="alpha")
    b = record_citation(pool_id="p", file_id="p::a.md", filename="a.md", chunk_idx=1, text="beta")
    again = record_citation(pool_id="p", file_id="p::a.md", filename="a.md", chunk_idx=0, text="alpha!")
    other_pool = record_citation(pool_id="q", file_id="q::a.md", filename="a.md", chunk_idx=0)
    assert (a, b, again, other_pool) == (1, 2, 1, 3)
    payloads = sink.payloads()
    assert [p["n"] for p in payloads] == [1, 2, 3]
    assert payloads[0]["snippet"] == "alpha" and payloads[0]["layer"] == "rag"
    assert set(payloads[0]) == {"n", "pool_id", "file_id", "filename", "chunk_idx", "heading_path",
                                "snippet", "score", "workspace_file_id", "layer"}


def test_a_long_passage_is_cut_to_a_snippet(sink):
    record_citation(pool_id="p", file_id="f", text="word " * 200)
    snippet = sink.payloads()[0]["snippet"]
    assert len(snippet) <= citation_sink.SNIPPET_CHARS and snippet.endswith("…")


def test_no_sink_no_number():
    assert citation_sink.current() is None
    assert record_citation(pool_id="p", file_id="f") is None


# ── search_memory and recall ─────────────────────────────────────────────────

def test_search_memory_numbers_passages_and_notes_and_says_how_to_cite(sink):
    from memory.tool import CITE_INSTRUCTION, _search_memory_impl

    mem = _pool_with_handbook()
    out = json.loads(_search_memory_impl("paid leave days", str(mem.id), top_k=5))
    assert out["ok"] and out["citations"] == CITE_INSTRUCTION
    rag = [r for r in out["results"] if r["layer"] == "rag"]
    assert rag and rag[0]["filename"] == "handbook.md" and rag[0]["heading_path"] == ["Handbook", "Leave"]
    cited = {r["cite"] for r in out["results"] if "cite" in r}
    assert cited == set(range(1, len(cited) + 1))
    by_n = {c["n"]: c for c in sink.payloads()}
    leave = next(c for c in by_n.values() if c["layer"] == "rag" and "30 days" in c["snippet"])
    assert leave["workspace_file_id"] == "file_00000000000000aa"
    assert leave["pool_id"] == str(mem.id) and leave["filename"] == "handbook.md"
    note = next(c for c in by_n.values() if c["layer"] == "note")
    assert note["filename"] == "Leave policy note" and note["workspace_file_id"] == ""
    # Blocks are core memory already in the prompt, never numbered.
    assert all(r.get("layer") not in ("block", "slot", "episode") or "cite" not in r
               for r in out["results"])

    # The same passage found again keeps its number; a new one gets the next.
    first = leave["n"]
    again = json.loads(_search_memory_impl("paid leave", str(mem.id), top_k=5))
    assert next(r["cite"] for r in again["results"]
                if r["layer"] == "rag" and "30 days" in r["text"]) == first
    json.loads(_search_memory_impl("laptops replaced", str(mem.id), top_k=5))
    assert max(c["n"] for c in sink.payloads()) == len(sink.payloads())


def test_outside_a_run_search_memory_answers_without_numbers():
    from memory.tool import _search_memory_impl

    mem = _pool_with_handbook()
    out = json.loads(_search_memory_impl("paid leave", str(mem.id)))
    assert out["ok"] and any(r["layer"] == "rag" for r in out["results"])
    assert "citations" not in out and not any("cite" in r for r in out["results"])


def test_recall_cites_passages_too(sink):
    from memory.tool import create_memory_tools

    mem = _pool_with_handbook()
    tools = {t.name: t for t in create_memory_tools(str(mem.id), [])}
    out = json.loads(tools["recall"].invoke({"query": "paid leave"}))
    assert out["found"] and "citations" in out
    assert any(r.get("layer") == "rag" and r.get("cite") for r in out["results"])
    rag_trace = next(t for t in out["trace"] if t["layer"] == "rag")
    assert rag_trace["hits"] >= 1 and rag_trace["vector"] is False


# ── where they travel ────────────────────────────────────────────────────────

def test_the_drive_loop_keeps_citations_on_the_result_and_the_run_record(monkeypatch):
    import chat.streaming as streaming_mod
    from chat.streaming import StreamDriveResult, drive_streaming_run
    from managers import run_manager

    monkeypatch.setattr(streaming_mod, "get_run", lambda run_id: {"status": "running"})
    run_manager.open_run("run-cite", "agent-x")
    s = CitationSink()
    s.record(pool_id="p", file_id="p::a.md", filename="a.md", chunk_idx=0, text="alpha")

    async def _drive():
        async def _agent():
            return SimpleNamespace(ok=True, agent_output="alpha is true [1]", error=None, response=None)

        queue: asyncio.Queue = asyncio.Queue()
        callback = SimpleNamespace(
            prompt_tokens=1, completion_tokens=1, total_tokens=2, cached_prompt_tokens=0,
            tool_calls=0, tool_history=[], thinking_history=[], llm_invocations=[],
            llm_invoke_responses=[], artifact_history=[], cancelled=False,
            _last_prompt_struct={}, context_usage=lambda: {},
        )
        result = StreamDriveResult()
        async for _ in drive_streaming_run(task=asyncio.ensure_future(_agent()), queue=queue,
                                           callback=callback, run_id="run-cite", full_prompt="q",
                                           started_ts=time.perf_counter(), result=result,
                                           citation_sink=s):
            pass
        return result

    result = asyncio.run(_drive())
    assert [c["n"] for c in result.citations] == [1]
    assert result.process_payload["citations"] == result.citations
    assert run_manager.get_run_by_id("run-cite")["citations"] == result.citations


def test_the_chat_done_event_carries_the_citations(monkeypatch):
    """The single-agent pipeline end to end: the agent's memory search records
    passages on the turn's sink, and the turn's ``done`` lists them."""
    import agents.registry as registry
    import chat.pipelines as pipelines
    from chat.models import ChatRequest
    from managers import run_manager
    from memory.tool import _search_memory_impl

    mem = _pool_with_handbook()
    monkeypatch.setattr(registry, "get_agent",
                        lambda agent_id: SimpleNamespace(tools=[], model_overrides=lambda: {}))
    monkeypatch.setattr("providers.context_windows.get_model_context_window",
                        lambda provider, model: 200_000)

    class _Agent:
        provider, model, system_prompt = "openai", "gpt-4o-mini", "sys"

        async def arun(self, prompt, history=None, callbacks=None, **kw):
            found = json.loads(_search_memory_impl("paid leave", str(mem.id)))
            n = next(r["cite"] for r in found["results"] if r["layer"] == "rag")
            return SimpleNamespace(ok=True, agent_output=f"You get 30 days [{n}].", error=None,
                                   response=None, status="done")

    monkeypatch.setattr(pipelines, "create_agent",
                        lambda agent_id, workspace=None, streaming=False, **kw: _Agent())
    request = ChatRequest(agent_id="a", message="How much leave?", conversation_id="conv-cite")

    async def _drive():
        return [event async for event in pipelines._run_chat_pipeline(request)]

    events = asyncio.run(_drive())
    done = [e for e in events if e["type"] == "done"][-1]
    assert done["ok"] and "[1]" in done["response"]
    rag = [c for c in done["citations"] if c["layer"] == "rag"]
    assert rag and rag[0]["filename"] == "handbook.md"
    assert rag[0]["workspace_file_id"] == "file_00000000000000aa"
    assert run_manager.get_run_by_id(done["run_id"])["citations"] == done["citations"]


def test_a_flow_node_keeps_its_own_citations(monkeypatch):
    """Each flow node runs with its own sink (chat/flow_driver.py): the node
    that searched keeps its numbered passages on its run record and in the
    driver's node meta, which is what the node's ``node_done`` is built from."""
    import chat.flow_driver as flow_driver
    import chat.pipelines as pipelines
    import flow.dispatch as fd
    import flow.validate as fv
    from chat.models import ChatRequest
    from managers import run_manager
    from memory.tool import _search_memory_impl

    mem = _pool_with_handbook()
    flow_def = {
        "id": "flow-cite", "name": "Cite flow",
        "nodes": [{"id": "n1", "agent_id": "researcher", "label": "Researcher", "output": ["result"]}],
        "edges": [],
    }
    monkeypatch.setattr(pipelines, "load_flow_definition", lambda flow_id: flow_def)
    monkeypatch.setattr(fv, "validate_flow", lambda flow: None)
    monkeypatch.setattr(fv, "resolve_entities", lambda nodes: {})

    class _Entity:
        runs_in_process = True

        def __init__(self, node):
            self.spec = SimpleNamespace(id=node["id"], name=node.get("label"), category="agent")

    monkeypatch.setattr(fd.FlowEntity, "for_node", classmethod(lambda cls, node: _Entity(node)))

    class _Agent:
        provider, model, system_prompt = "openai", "gpt-4o-mini", "sys"

        async def arun(self, prompt, history=None, callbacks=None, **kw):
            _search_memory_impl("paid leave", str(mem.id))
            return SimpleNamespace(ok=True, agent_output="30 days [1]", error=None,
                                   response=None, status="done")

    monkeypatch.setattr(flow_driver, "create_agent", lambda agent_id, workspace=None, **kw: _Agent())
    seen = {}
    real_build = flow_driver.build_chat_driver

    def _capture(**kw):
        driver, state = real_build(**kw)
        seen["state"] = state
        return driver, state

    monkeypatch.setattr(flow_driver, "build_chat_driver", _capture)
    request = ChatRequest(flow_id="flow-cite", message="How much leave?", conversation_id="conv-flow")

    async def _drive():
        return [event async for event in pipelines._run_chat_flow_pipeline(request)]

    events = asyncio.run(_drive())
    node_done = next(e for e in events if e["type"] == "node_done")
    assert node_done["ok"]
    meta = seen["state"].node_meta["n1"]
    assert meta["citations"] and meta["citations"][0]["n"] == 1
    assert run_manager.get_run_by_id(node_done["run_id"])["citations"] == meta["citations"]
