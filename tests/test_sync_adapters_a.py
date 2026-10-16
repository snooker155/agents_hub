"""
Sync adapters, first half: agents, skills, memory, files, chats, assistant
threads and views (hubsync/adapters/).

Two hubs are simulated on one database. Ids are global in most stores, so the
"other hub" gets its own record id (the engine's id mapping does exactly that)
and either another workspace or another owner. What every kind must hold:
a document read here and applied there reads back with the same content hash
(otherwise the engine would echo the record forever), a list scoped to the
owner, a delete, and no secret value in a document.
"""
from __future__ import annotations

import json
import uuid

import pytest

from hubsync.adapters import base
from hubsync.adapters.base import AdapterError

WS, OTHER = "sync-a", "sync-b"


def _adapter(kind):
    adapter = base.get(kind)
    assert adapter is not None, kind
    return adapter


def _same(adapter, first, second):
    return adapter.content_hash(first) == adapter.content_hash(second)


def test_all_seven_kinds_register_in_order():
    kinds = base.kinds()
    for kind in ("file", "agent", "skill", "memory", "chat", "assistant", "view"):
        assert kind in kinds
    order = [a.order for a in base.all_adapters()]
    assert order == sorted(order)
    ranked = [k for k in kinds if k in ("file", "agent", "skill", "memory", "chat", "assistant", "view")]
    assert ranked == ["file", "agent", "skill", "memory", "chat", "assistant", "view"]


# ── chat ─────────────────────────────────────────────────────────────────────

def _chat(chat_id="c1", workspace=WS, **extra):
    from common import chat_store
    return chat_store.save_chat({
        "id": chat_id, "workspace": workspace, "title": "Plan", "agent_id": "main-agent",
        "messages": [
            {"id": "m1", "role": "user", "content": "hello"},
            {"id": "m2", "role": "agent", "content": "hi", "run_id": "r1", "thinking_live": "..."},
        ], **extra})


def test_chat_round_trip_and_owner():
    from common import chat_store
    a = _adapter("chat")
    _chat()
    doc = a.read(WS, "c1")
    assert "owner" not in doc and "workspace" not in doc and "origin" not in doc
    assert "thinking_live" not in json.dumps(doc)
    assert a.apply(WS, "c2", doc, owner="u2") == "c2"
    stored = chat_store.get_chat("c2")
    assert stored["owner"] == "u2" and stored["origin"] == "sync"
    assert _same(a, doc, a.read(WS, "c2"))
    assert a.list_ids(WS, "local") == ["c1"]
    assert a.list_ids(WS, "u2") == ["c2"]


def test_chat_update_keeps_hash_stable_and_refuses_foreign_ids():
    a = _adapter("chat")
    _chat()
    doc = a.read(WS, "c1")
    a.apply(WS, "c2", doc, owner="u2")
    doc2 = {**doc, "messages": doc["messages"] + [{"id": "m3", "role": "user", "content": "more"}]}
    a.apply(WS, "c2", doc2, owner="u2")
    assert _same(a, doc2, a.read(WS, "c2"))
    with pytest.raises(AdapterError):
        a.apply(WS, "c2", doc, owner="u3")  # someone else's chat
    with pytest.raises(AdapterError):
        a.apply(OTHER, "c2", doc, owner="u2")  # id lives in another workspace


def test_chat_skips_mirror_copies_and_deletes():
    a = _adapter("chat")
    _chat("mine")
    _chat("copy", origin="mirror")
    assert a.list_ids(WS, "local") == ["mine"]
    assert a.read(WS, "copy") is None
    a.delete(WS, "mine")
    assert a.read(WS, "mine") is None
    assert a.list_ids(WS, "local") == []


# ── assistant threads ────────────────────────────────────────────────────────

def _thread(kind="assistant", workspace=WS, user="local", words=("hi", "yo")):
    from common.entity_chat_store import entity_chat_store
    store = entity_chat_store()
    meta = {"workspace": workspace} if kind == "assistant" else None
    store.append_message(kind, f"user-{user}", "user", words[0], meta=meta)
    store.append_message(kind, f"user-{user}", "assistant", words[1], meta=meta)


def test_assistant_round_trip_to_another_person():
    from common.entity_chat_store import entity_chat_store
    a = _adapter("assistant")
    _thread()
    assert a.list_ids(WS, "local") == ["assistant:user-local"]
    doc = a.read(WS, "assistant:user-local")
    assert len(doc["threads"]) == 1
    local_id = a.apply(WS, "assistant:user-local", doc, owner="u2")
    assert local_id == "assistant:user-u2"
    assert _same(a, doc, a.read(WS, local_id))
    assert entity_chat_store().get_messages("assistant", "user-u2")[0]["content"] == "hi"
    assert a.list_ids(WS, "u2") == [local_id]
    assert a.list_ids(WS, "u3") == []


def test_assistant_keeps_other_workspaces_and_archives_the_rest():
    from common.entity_chat_store import entity_chat_store
    a = _adapter("assistant")
    store = entity_chat_store()
    _thread(workspace=OTHER, user="u2", words=("elsewhere", "ok"))
    # u2 already has a live thread of OTHER; a thread of WS arrives.
    _thread(workspace=WS, words=("mine", "ok"))
    doc = a.read(WS, "assistant:user-local")
    a.apply(WS, "assistant:user-local", doc, owner="u2")
    assert store.get_messages("assistant", "user-u2")[0]["content"] == "elsewhere"
    assert _same(a, doc, a.read(WS, "assistant:user-u2"))
    assert a.read(OTHER, "assistant:user-u2")["threads"][0]["messages"][0]["content"] == "elsewhere"
    # An update replaces the workspace's thread, it does not stack a copy.
    a.apply(WS, "assistant:user-local", doc, owner="u2")
    assert len(a.read(WS, "assistant:user-u2")["threads"]) == 1
    a.delete(WS, "assistant:user-u2")
    assert a.read(WS, "assistant:user-u2") is None
    assert a.read(OTHER, "assistant:user-u2") is not None


def test_help_threads_sync_in_default_only():
    a = _adapter("assistant")
    _thread(kind="help")
    assert a.list_ids("default", "local") == ["help:user-local"]
    assert a.list_ids(WS, "local") == []
    doc = a.read("default", "help:user-local")
    a.apply("default", "help:user-local", doc, owner="u2")
    assert _same(a, doc, a.read("default", "help:user-u2"))
    with pytest.raises(AdapterError):
        a.apply("default", "other:user-local", doc, owner="u2")


# ── memory ───────────────────────────────────────────────────────────────────

def test_personal_pool_maps_to_the_owners_pool():
    from memory import personal
    from memory.store import MemoryStore
    a = _adapter("memory")
    pool = personal.ensure_pool("local", WS)
    pool.notes.append({"id": "n1", "title": "t", "content": "likes tea", "created_at": "2026-01-01"})
    pool.rag_files.append({"filename": "a.md", "workspace": WS, "status": "indexed", "chunks": 4,
                           "indexed_at": "2026-01-01", "content_hash": "abc"})
    MemoryStore().add(pool)
    doc = a.read(WS, str(pool.id))
    assert "name" not in doc and doc["rag_files"] == [{"filename": "a.md", "workspace": WS, "content_hash": "abc"}]
    local_id = a.apply(WS, str(pool.id), doc, owner="u2")
    assert local_id == personal.pool_id("u2", WS) != str(pool.id)
    assert _same(a, doc, a.read(WS, local_id))
    assert MemoryStore().get(local_id).rag_files[0]["status"] == "pending"
    assert a.list_ids(WS, "u2") == [local_id]
    assert a.list_ids(WS, "local") == [str(pool.id)]


def test_index_status_is_not_a_change():
    from memory import personal
    from memory.store import MemoryStore
    a = _adapter("memory")
    pool = personal.ensure_pool("local", WS)
    pool.rag_files.append({"filename": "a.md", "workspace": WS, "status": "pending"})
    MemoryStore().add(pool)
    before = a.content_hash(a.read(WS, str(pool.id)))
    pool.rag_files[0].update(status="indexed", chunks=9, indexed_at="2026-02-02")
    MemoryStore().add(pool)
    assert a.content_hash(a.read(WS, str(pool.id))) == before


def test_shared_pool_round_trip_and_taken_ids():
    from memory.models import SharedMemory
    from memory.store import MemoryStore
    a = _adapter("memory")
    shared = SharedMemory(name="Team facts", workspace=WS, description="d")
    shared.structured_data = {"k": {"v": 1}}
    MemoryStore().add(shared)
    doc = a.read(WS, str(shared.id))
    new_id = str(uuid.uuid4())
    assert a.apply(OTHER, new_id, doc, owner="u2") == new_id
    assert _same(a, doc, a.read(OTHER, new_id))
    assert new_id in a.list_ids(OTHER, "anyone")
    with pytest.raises(AdapterError):
        a.apply(OTHER, str(shared.id), doc, owner="u2")  # lives in WS
    a.delete(OTHER, new_id)
    assert a.read(OTHER, new_id) is None


# ── agent ────────────────────────────────────────────────────────────────────

@pytest.fixture
def agents_env(tmp_path, monkeypatch):
    from agents import prompt_assembly, registry
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    registry.replace_all_raw([])
    yield registry
    registry.replace_all_raw([])


def _agent(registry, agent_id="ag1", **kw):
    from agents import prompt_assembly
    spec = registry.AgentSpec(id=agent_id, name=kw.pop("name", "Helper"),
                              type=kw.pop("type", "langchain"),
                              entrypoint=kw.pop("entrypoint", "agents.agent_factory:build_agent_executor"),
                              owner_workspace=kw.pop("owner_workspace", WS),
                              owner_user=kw.pop("owner_user", "local"), **kw)
    registry.add_agent(spec)
    prompt_assembly.write_instructions(agent_id, "Be brief.")
    prompt_assembly.write_usage(agent_id, 'Good fits:\n- "Do a thing"\n')
    return spec


def test_agent_round_trip_with_prompts_and_secret_names_only(agents_env):
    from agents import prompt_assembly as pa
    registry = agents_env
    a = _adapter("agent")
    _agent(registry, secrets=["JIRA_TOKEN"], shared=True, tools=[])
    doc = a.read(WS, "ag1")
    assert doc["secrets"] == ["JIRA_TOKEN"]
    assert doc["prompts"]["instructions"] == "Be brief."
    for local in ("owner_user", "owner_workspace", "shared", "review_status", "id"):
        assert local not in doc
    assert a.apply(OTHER, "ag2", doc, owner="u2") == "ag2"
    stored = registry.get_agent_raw("ag2")
    assert stored.owner_workspace == OTHER and stored.owner_user == "u2" and not stored.shared
    assert pa.read_instructions("ag2") == "Be brief."
    assert _same(a, doc, a.read(OTHER, "ag2"))
    assert a.list_ids(WS, "local") == ["ag1"] and a.list_ids(OTHER, "local") == []
    assert a.list_ids(OTHER, "u2") == ["ag2"]


def test_agent_update_rewrites_prompts(agents_env):
    from agents import prompt_assembly as pa
    registry = agents_env
    a = _adapter("agent")
    _agent(registry)
    doc = a.read(WS, "ag1")
    a.apply(OTHER, "ag2", doc, owner="u2")
    changed = {**doc, "description": "new", "prompts": {**doc["prompts"], "instructions": "Be long.", "usage": ""}}
    a.apply(OTHER, "ag2", changed, owner="u2")
    assert pa.read_instructions("ag2") == "Be long." and pa.read_usage("ag2") == ""
    assert _same(a, changed, a.read(OTHER, "ag2"))


def test_agent_taken_ids_raise(agents_env):
    registry = agents_env
    a = _adapter("agent")
    _agent(registry)
    _agent(registry, "sysagent", owner_workspace=None, system=True)
    doc = a.read(WS, "ag1")
    with pytest.raises(AdapterError, match="taken"):
        a.apply(OTHER, "ag1", doc, owner="u2")  # belongs to WS
    with pytest.raises(AdapterError, match="taken"):
        a.apply(OTHER, "sysagent", doc, owner="u2")
    with pytest.raises(AdapterError):
        a.apply(WS, "ag1", doc, owner="u3")  # same workspace, someone else's
    assert a.read(WS, "sysagent") is None and "sysagent" not in a.list_ids(WS, "local")


def test_agent_refusal_carries_the_reason(agents_env):
    registry = agents_env
    a = _adapter("agent")
    _agent(registry)
    doc = a.read(WS, "ag1")
    with pytest.raises(AdapterError, match="@"):
        a.apply(OTHER, "@role", doc, owner="u2")


def test_remote_agent_descriptor_holds_no_credential(agents_env):
    registry = agents_env
    a = _adapter("agent")
    _agent(registry, type="remote", entrypoint="agents.remote_agent:RemoteAgent",
           remote={"url": "https://x.example/run", "auth_token_env": "X_TOKEN", "token": "s3cr3t-value"})
    doc = a.read(WS, "ag1")
    assert "s3cr3t-value" not in json.dumps(doc)
    assert doc["remote"]["auth_token_env"] == "X_TOKEN"


def test_agent_delete(agents_env):
    registry = agents_env
    a = _adapter("agent")
    _agent(registry)
    a.delete(WS, "ag1")
    assert registry.get_agent_raw("ag1") is None and a.read(WS, "ag1") is None


# ── skill ────────────────────────────────────────────────────────────────────

def _skill(**kw):
    from memory.procedural import Procedure, ProcedureStore
    skill = Procedure(name="Release", description="when shipping", steps=["tag", "push"],
                      workspace=WS, owner_user="local", **kw)
    ProcedureStore(WS).add(skill)
    return skill


def test_skill_round_trip_reviews_locally():
    from memory.procedural import ProcedureStore
    a = _adapter("skill")
    skill = _skill(shared=True, use_count=7)
    doc = a.read(WS, str(skill.id))
    assert not {"shared", "use_count", "safety", "version", "owner_user"} & set(doc)
    new_id = str(uuid.uuid4())
    # a stale verdict in the document is never trusted
    assert a.apply(WS, new_id, {**doc, "safety": {"severity": "none"}}, owner="u2") == new_id
    stored = ProcedureStore(WS).get(new_id)
    assert stored.owner_user == "u2" and stored.safety is not None and not stored.shared
    assert _same(a, doc, a.read(WS, new_id))
    assert a.list_ids(WS, "u2") == [new_id] and a.list_ids(WS, "local") == [str(skill.id)]


def test_skill_bookkeeping_is_not_a_change_and_edit_is():
    from memory.procedural import ProcedureStore
    a = _adapter("skill")
    skill = _skill()
    before = a.content_hash(a.read(WS, str(skill.id)))
    store = ProcedureStore(WS)
    live = store.get(skill.id)
    live.use_count += 3
    store.update(live)
    assert a.content_hash(a.read(WS, str(skill.id))) == before
    doc = a.read(WS, str(skill.id))
    a.apply(WS, str(skill.id), {**doc, "steps": ["tag"]}, owner="local")
    assert a.content_hash(a.read(WS, str(skill.id))) != before
    assert store.get(skill.id).version >= 2


def test_skill_refuses_repo_source_and_foreign_workspace():
    a = _adapter("skill")
    skill = _skill()
    doc = a.read(WS, str(skill.id))
    with pytest.raises(AdapterError):
        a.apply(WS, str(uuid.uuid4()), {**doc, "source": "repo"}, owner="u2")
    with pytest.raises(AdapterError):
        a.apply(OTHER, str(skill.id), doc, owner="u2")
    _skill(source="repo")
    assert a.list_ids(WS, "local") == [str(skill.id)]


def test_skill_delete():
    a = _adapter("skill")
    skill = _skill()
    a.delete(WS, str(skill.id))
    assert a.read(WS, str(skill.id)) is None


# ── file ─────────────────────────────────────────────────────────────────────

def test_upload_round_trip_and_hash_ignores_nothing_but_content():
    from files import service
    a = _adapter("file")
    rec = service.create_file(WS, "note.txt", b"hello sync", created_by="local", meta={"tag": "x"})
    doc = a.read(WS, rec["file_id"])
    assert doc["path"] is None and doc["meta"] == {"tag": "x"}
    new_id = a.apply(OTHER, rec["file_id"], doc, owner="u2")
    assert service.read_bytes(new_id) == b"hello sync"
    assert service.get_file(new_id)["created_by"] == "u2"
    assert _same(a, doc, a.read(OTHER, new_id))
    assert a.list_ids(WS, "local") == [rec["file_id"]] and a.list_ids(WS, "u2") == []
    assert a.list_ids(OTHER, "u2") == [new_id]


def test_folder_file_lands_at_its_path():
    from files import service
    a = _adapter("file")
    rec = service.write_folder_file(WS, "docs/plan.md", b"# plan", created_by="local")
    doc = a.read(WS, rec["file_id"])
    assert doc["path"] == "docs/plan.md" and "path" not in doc["meta"]
    new_id = a.apply(OTHER, rec["file_id"], doc, owner="u2")
    assert service.folder_path_of(new_id, OTHER) == "docs/plan.md"
    assert _same(a, doc, a.read(OTHER, new_id))
    # a second apply of the same document is a no-op, not a second record
    assert a.apply(OTHER, rec["file_id"], doc, owner="u2") == new_id
    a.delete(OTHER, new_id)
    assert a.read(OTHER, new_id) is None


def test_changed_upload_replaces_the_record():
    from files import service
    a = _adapter("file")
    rec = service.create_file(WS, "n.txt", b"one", created_by="local")
    doc = a.read(WS, rec["file_id"])
    first = a.apply(OTHER, rec["file_id"], doc, owner="u2")
    new_bytes = b"two"
    import base64
    import hashlib
    changed = {**doc, "content_b64": base64.b64encode(new_bytes).decode(), "size": 3,
               "sha256": hashlib.sha256(new_bytes).hexdigest()}
    second = a.apply(OTHER, first, changed, owner="u2")
    # The old upload stays until the engine has mapped the new one (it may
    # refuse to), then goes through replaced().
    assert service.read_bytes(second) == b"two" and service.read_bytes(first) == b"one"
    a.replaced(OTHER, first, second, changed)
    assert service.get_file(first) is None and service.read_bytes(second) == b"two"


def test_oversize_file_is_skipped_with_a_reason(monkeypatch):
    from files import service
    a = _adapter("file")
    rec = service.create_file(WS, "big.bin", b"x" * 100, created_by="local")
    monkeypatch.setattr(service, "max_file_bytes", lambda: 10)
    doc = a.read(WS, rec["file_id"])
    assert "content_b64" not in doc and doc["skipped"]
    with pytest.raises(AdapterError, match="not sent"):
        a.apply(OTHER, rec["file_id"], doc, owner="u2")


def test_damaged_file_is_refused_and_tombstones_are_not_listed():
    from files import service
    a = _adapter("file")
    rec = service.create_file(WS, "n.txt", b"abc", created_by="local")
    doc = a.read(WS, rec["file_id"])
    with pytest.raises(AdapterError, match="damaged"):
        a.apply(OTHER, "x", {**doc, "sha256": "0" * 64}, owner="u2")
    service.delete_file(rec["file_id"])
    assert a.list_ids(WS, "local") == [] and a.read(WS, rec["file_id"]) is None


# ── view ─────────────────────────────────────────────────────────────────────

def _view(**kw):
    from views import create_view
    return create_view("markdown", "Notes", {"markdown": "# hi"}, summary="s", workspace=WS, **kw)


def test_view_round_trip_with_assets():
    from views import store
    a = _adapter("view")
    env = _view()
    store.add_asset(env.view_id, __file__, "src/test.py")
    store.set_view_state(env.view_id, {"zoom": 2})
    doc = a.read(WS, env.view_id)
    assert set(doc["files"]) == {"src/test.py"} and doc["state"] == {"zoom": 2}
    assert "view_id" not in doc and "run_id" not in doc
    new_id = a.apply(OTHER, env.view_id, doc, owner="u2")
    assert new_id != env.view_id
    back = a.read(OTHER, new_id)
    assert _same(a, doc, back)
    assert back["files"] == doc["files"]
    assert a.list_ids(OTHER, "u2") == [new_id]


def test_view_state_and_update():
    from views import store
    a = _adapter("view")
    env = _view()
    before = a.content_hash(a.read(WS, env.view_id))
    store.set_view_state(env.view_id, {"x": 1})
    assert a.content_hash(a.read(WS, env.view_id)) == before
    doc = a.read(WS, env.view_id)
    new_id = a.apply(OTHER, env.view_id, doc, owner="u2")
    changed = {**doc, "title": "Renamed", "spec": {"markdown": "# changed"}, "files": {"a.txt": "aGk="},
               "assets": ["a.txt"]}
    assert a.apply(OTHER, new_id, changed, owner="u2") == new_id
    assert _same(a, changed, a.read(OTHER, new_id))
    dropped = {**changed, "files": {}, "assets": []}
    a.apply(OTHER, new_id, dropped, owner="u2")
    assert a.read(OTHER, new_id)["files"] == {}


def test_oversize_view_files_are_skipped_and_stable(monkeypatch):
    from hubsync.adapters import view as view_mod
    from views import store
    a = _adapter("view")
    env = _view()
    store.add_asset(env.view_id, __file__, "big.py")
    monkeypatch.setattr(view_mod, "MAX_FILES_BYTES", 10)
    doc = a.read(WS, env.view_id)
    assert doc["files"] == {} and doc["files_skipped"]
    new_id = a.apply(OTHER, env.view_id, doc, owner="u2")
    assert _same(a, doc, a.read(OTHER, new_id))  # the echo reads back the same
    assert not (store.view_dir(new_id) / "big.py").exists()


def test_view_rejects_escaping_paths_and_deletes():
    a = _adapter("view")
    env = _view()
    doc = a.read(WS, env.view_id)
    with pytest.raises(AdapterError):
        a.apply(OTHER, env.view_id, {**doc, "files": {"../evil": "aGk="}}, owner="u2")
    a.delete(WS, env.view_id)
    assert a.read(WS, env.view_id) is None
