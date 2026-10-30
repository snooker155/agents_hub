"""
Every tool an agent can hold, called once from a process with no database
(stage 5 of the remote state work): an agent holding a slice of the catalog
runs in audit mode while the fake model calls each of its tools with
arguments made from the tool's schema. The audit lists every store access the
tools still make directly (tests/remote_harness.py).

Exploration only: ``REMOTE_AUDIT=<file>`` runs it, and the report lands in
that file. A tool's own failure (a bad argument, no network) is fine here;
what counts is that nothing it did opened the database.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest

from common import run_tokens
from remote_harness import audit_report, child_env, hub_server, run_child

AUDIT = os.environ.get("REMOTE_AUDIT")
pytestmark = pytest.mark.skipif(not AUDIT, reason="exploration only (REMOTE_AUDIT=<file>)")

#: Tools left out: they wait on something that never comes here, need a
#: browser or the network, end the run, or see the whole service (service
#: agents run on the hub, never in a container).
SKIP = frozenset({
    "wait_for_agent_tool", "ask_user", "map_on_nodes", "run_code", "generate_video",
    "web_search", "fetch_url", "browse", "transcribe_audio", "synthesize_speech",
    "generate_image", "ask_special_model",
    # They stop or park the sweep's own task, which ends the run.
    "stop_agent_tool", "stop_task", "block_task", "delete_workspace",
})

CHUNKS = 6


def _catalog() -> List[Any]:
    from common.workspace_scope import SERVICE_WIDE_TOOLS
    from tools.registry import get_all_tools
    out = []
    for spec in get_all_tools():
        if spec.id in SKIP or spec.id in SERVICE_WIDE_TOOLS or spec.category in (
                "service_ops", "system_ops", "browser"):
            continue
        out.append(spec)
    return sorted(out, key=lambda s: (s.category, s.id))


@pytest.fixture
def world(tmp_path, reopen_db):
    """A workspace with one of each record a tool's id argument may name."""
    (tmp_path / "hubroot").mkdir()
    reopen_db(tmp_path / "hubroot" / "agents_hub.db")
    from common.bootstrap import seed_registry_from_bootstrap
    from flow import store as flow_store
    from memory.models import SharedMemory
    from memory.store import MemoryStore
    from tasks.service import create_task
    from workspace.storage import create_workspace_folder, update_workspace_metadata
    seed_registry_from_bootstrap()
    folder = create_workspace_folder("acme")
    update_workspace_metadata("acme", {"model_default": {"provider": "openai", "model": "gpt-4o-mini"}})
    (folder / "notes.txt").write_text("hello", encoding="utf-8")
    task = create_task("sweep", "the sweep's own task", workspace="acme")
    flow_store.save_flow({
        "id": "f-sweep", "name": "Sweep flow", "workspace": "acme",
        "nodes": [{"id": "a", "type": "flowNode", "position": {"x": 0, "y": 0},
                   "data": {"label": "a", "agent_id": "swe_agent"}}], "edges": []})
    pool = MemoryStore().add(SharedMemory(name="sweep pool", workspace="acme"))
    return {"folder": folder, "task": str(task.id), "flow": "f-sweep", "pool": str(pool.id)}


def _value(name: str, kind: str, world: Dict[str, Any]) -> Any:
    n = name.lower()
    if kind == "integer" or kind == "number":
        return 1
    if kind == "boolean":
        return False
    if kind == "array":
        if "task" in n:
            return [world["task"]]
        return []
    if kind == "object":
        return {}
    if n in ("task_id", "id", "parent_id"):
        return world["task"]
    if "flow" in n and "id" in n:
        return world["flow"]
    if n in ("memory_id", "pool_id"):
        return world["pool"]
    if "agent" in n and "id" in n:
        return "swe_agent"
    if n == "workspace" or n == "name" and False:
        return "acme"
    if n in ("path", "file_path"):
        return "notes.txt"
    if "url" in n:
        return "http://127.0.0.1:9/nothing"
    if "json" in n:
        return "{}"
    if n in ("cron",):
        return "0 9 * * *"
    if "date" in n or n.endswith("_at"):
        return "2030-01-01T09:00:00+00:00"
    return "sweep"


def _args(spec: Any, world: Dict[str, Any]) -> Dict[str, Any]:
    # Every parameter, not only the ones marked required: a few tools take
    # one positionally that their schema leaves optional.
    return {p["name"]: _value(p["name"], str(p.get("type") or "string"), world)
            for p in spec.parameters}


@pytest.mark.parametrize("chunk", range(CHUNKS))
def test_sweep(world, tmp_path, chunk):
    from agents.registry import load_all_raw, replace_all_raw
    tools = _catalog()[chunk::CHUNKS]
    records = load_all_raw()
    base = next(r for r in records if r.get("id") == "swe_agent")
    agent = {**base, "id": "sweep_agent", "name": "Sweep", "tools": [t.id for t in tools],
             "tool_policy": {"*": "always_allow"}}
    replace_all_raw([*records, agent])
    from agents.prompt_assembly import write_instructions
    write_instructions("sweep_agent", "Call the tools you are given, one at a time.")
    script = [{"tool": t.id, "args": _args(t, world)} for t in tools]
    audit = tmp_path / "audit.txt"
    run_id = f"run-sweep-{chunk}"
    from managers.run_manager import open_run
    open_run(run_id, "sweep_agent", task_id=world["task"], workspace="acme", status="starting",
             link_to_session=False)
    with hub_server(script) as hub:
        token = run_tokens.mint(run_id=run_id, workspace="acme")
        env = child_env(hub["url"], token, workspace="acme", root=tmp_path / "child", audit=audit)
        Path(env["AGENTS_HUB_ENV_FILE"]).write_text("CAPABILITY_GUARD=off\n", encoding="utf-8")
        out = run_child(["runtime.agent_run", "sweep_agent", "--task-id", world["task"],
                         "--run-id", run_id, "--workspace", str(world["folder"]),
                         "--provider", "openai", "--model", "gpt-4o-mini"], env, timeout=900)
    text = out.stdout + out.stderr
    called = sorted({line.split("tool=", 1)[1].split()[0] for line in text.splitlines()
                     if line.startswith("[tool_start] tool=")})
    with open(AUDIT, "a", encoding="utf-8") as fh:
        fh.write(f"######## chunk {chunk}: {len(tools)} tools, {len(called)} called, "
                 f"exit {out.returncode}\n")
        missing = sorted({t.id for t in tools} - set(called))
        if missing:
            fh.write(f"## NOT CALLED: {json.dumps(missing)}\n")
        for head, paths in sorted(audit_report(audit).items()):
            fh.write(f"## {head}  ({len(paths)})\n")
            for line in paths[:3]:
                fh.write(f"   {line}\n")
    with open(AUDIT + f".chunk{chunk}.log", "w", encoding="utf-8") as fh:
        fh.write(text)


def _run_one(tmp_path: Path, world: Dict[str, Any], agent_id: str, workspace: str, script: List[Any],
             extra_args: List[str], name: str) -> str:
    audit = tmp_path / f"audit-{name}.txt"
    run_id = f"run-sweep-{name}"
    from managers.run_manager import open_run
    open_run(run_id, agent_id, workspace=workspace, status="starting", link_to_session=False)
    with hub_server(script) as hub:
        token = run_tokens.mint(run_id=run_id, workspace=workspace)
        env = child_env(hub["url"], token, workspace=workspace, root=tmp_path / f"child-{name}",
                        audit=audit)
        Path(env["AGENTS_HUB_ENV_FILE"]).write_text("CAPABILITY_GUARD=off\n", encoding="utf-8")
        out = run_child(["runtime.agent_run", agent_id, "do it", "--run-id", run_id,
                         "--provider", "openai", "--model", "gpt-4o-mini", *extra_args], env, timeout=600)
    text = out.stdout + out.stderr
    called = sorted({line.split("tool=", 1)[1].split()[0] for line in text.splitlines()
                     if line.startswith("[tool_start] tool=")})
    with open(AUDIT, "a", encoding="utf-8") as fh:
        fh.write(f"######## {name}: called {json.dumps(called)}, exit {out.returncode}\n")
        for head, paths in sorted(audit_report(audit).items()):
            fh.write(f"## {head}  ({len(paths)})\n")
            for line in paths[:3]:
                fh.write(f"   {line}\n")
    with open(AUDIT + f".{name}.log", "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def test_sweep_memory_tools(world, tmp_path):
    """The pool-bound memory tools, which an agent gets only with a pool."""
    pool = world["pool"]
    script = [
        {"tool": "write_memory", "args": {"note_title": "tea", "note_content": "green"}},
        {"tool": "read_memory", "args": {"note_title": "tea"}},
        {"tool": "search_memory", "args": {"query": "tea"}},
        {"tool": "write_structured_memory", "args": {"slot": "prefs", "data": {"tea": "green"}}},
        {"tool": "read_structured_memory", "args": {"slot": "prefs"}},
        {"tool": "append_journal", "args": {"summary": "drank tea"}},
        {"tool": "recall", "args": {"query": "tea"}},
        {"tool": "remember", "args": {"slot": "prefs", "data": {"value": "green tea"}}},
        {"tool": "recall", "args": {"query": "prefs"}},
    ]
    _run_one(tmp_path, world, "universal_agent", "acme", script,
             ["--workspace", str(world["folder"]), "--memory-pool", pool, "--memory-access", "write"],
             "memory")


def test_sweep_workspace_tools(world, tmp_path):
    """The main agent's workspace tools, run in the default workspace."""
    from workspace.storage import create_workspace_folder
    folder = create_workspace_folder("default")
    script = [
        {"tool": "list_workspaces", "args": {}},
        {"tool": "get_workspace", "args": {"name": "acme"}},
        {"tool": "create_workspace", "args": {"name": "fresh", "description": "new"}},
        {"tool": "update_workspace", "args": {"name": "fresh", "description": "changed"}},
        {"tool": "add_workspace_agent", "args": {"name": "fresh", "agent_id": "swe_agent"}},
        {"tool": "remove_workspace_agent", "args": {"name": "fresh", "agent_id": "swe_agent"}},
    ]
    _run_one(tmp_path, world, "main-agent", "default", script, ["--workspace", str(folder)], "workspaces")
