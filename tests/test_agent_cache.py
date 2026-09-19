"""Content-addressed agent build cache tests."""
import pytest

from agents import agent_cache


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    agent_cache.invalidate()
    from common.config import settings
    monkeypatch.setattr(settings, "agent_cache_enabled", True)
    monkeypatch.setattr(settings, "agent_cache_ttl", 0)  # no time bound unless a test sets one
    yield
    agent_cache.invalidate()


class _Counter:
    def __init__(self):
        self.n = 0

    def build(self):
        self.n += 1
        return object()


def _go(agent_id, counter, *, workspace=None, overrides=None):
    return agent_cache.get_or_build(
        agent_id, workspace, overrides or {},
        definitions_dir="/defs",
        builder=counter.build,
    )


def test_second_call_returns_cached_object(monkeypatch):
    monkeypatch.setattr(agent_cache, "compute_fingerprint", lambda *a, **k: "FP")
    c = _Counter()
    a1 = _go("swe_agent", c)
    a2 = _go("swe_agent", c)
    assert a1 is a2          # same object reused
    assert c.n == 1          # builder ran once


def test_fingerprint_change_rebuilds(monkeypatch):
    fp = {"v": "A"}
    monkeypatch.setattr(agent_cache, "compute_fingerprint", lambda *a, **k: fp["v"])
    c = _Counter()
    a1 = _go("swe_agent", c)
    fp["v"] = "B"            # an input changed
    a2 = _go("swe_agent", c)
    assert a1 is not a2
    assert c.n == 2


def test_override_params_are_keyed_separately(monkeypatch):
    monkeypatch.setattr(agent_cache, "compute_fingerprint", lambda *a, **k: "FP")
    c = _Counter()
    streaming = _go("swe_agent", c, overrides={"streaming": True})
    blocking = _go("swe_agent", c, overrides={"streaming": False})
    assert streaming is not blocking     # different builds (chat vs task)
    assert c.n == 2
    assert _go("swe_agent", c, overrides={"streaming": True}) is streaming


def test_workspace_is_keyed_separately(monkeypatch):
    monkeypatch.setattr(agent_cache, "compute_fingerprint", lambda *a, **k: "FP")
    c = _Counter()
    ws_a = _go("swe_agent", c, workspace="/ws/a")
    ws_b = _go("swe_agent", c, workspace="/ws/b")
    assert ws_a is not ws_b
    assert c.n == 2


def test_invalidate_by_agent_only(monkeypatch):
    monkeypatch.setattr(agent_cache, "compute_fingerprint", lambda *a, **k: "FP")
    c1, c2 = _Counter(), _Counter()
    a = _go("agent_a", c1)
    b = _go("agent_b", c2)
    removed = agent_cache.invalidate("agent_a")
    assert removed == 1
    assert _go("agent_a", c1) is not a      # rebuilt
    assert _go("agent_b", c2) is b          # untouched


def test_ttl_expiry_rebuilds(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "agent_cache_ttl", 100)
    monkeypatch.setattr(agent_cache, "compute_fingerprint", lambda *a, **k: "FP")
    clock = {"t": 1000.0}
    monkeypatch.setattr(agent_cache, "_now", lambda: clock["t"])
    c = _Counter()
    a1 = _go("swe_agent", c)
    clock["t"] += 50          # within TTL
    assert _go("swe_agent", c) is a1
    clock["t"] += 100         # now past TTL
    a3 = _go("swe_agent", c)
    assert a3 is not a1
    assert c.n == 2


def test_disabled_bypasses_cache(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "agent_cache_enabled", False)
    monkeypatch.setattr(agent_cache, "compute_fingerprint", lambda *a, **k: "FP")
    c = _Counter()
    a1 = _go("swe_agent", c)
    a2 = _go("swe_agent", c)
    assert a1 is not a2
    assert c.n == 2


def test_lru_evicts_oldest(monkeypatch):
    monkeypatch.setattr(agent_cache, "_MAX_ENTRIES", 3)
    monkeypatch.setattr(agent_cache, "compute_fingerprint", lambda *a, **k: "FP")
    c = _Counter()
    objs = {aid: _go(aid, c) for aid in ("a", "b", "c")}
    _go("d", c)                       # evicts 'a' (oldest)
    assert _go("a", c) is not objs["a"]   # 'a' was evicted → rebuilt
    assert _go("c", c) is objs["c"]       # 'c' still cached


def test_create_agent_routes_through_cache(monkeypatch):
    # The factory must delegate to the cache: a repeated create_agent with the
    # same inputs builds once. _build_agent is stubbed so no real LangChain
    # executor is constructed.
    from agents.agent_factory import AgentFactory
    f = AgentFactory()
    calls = []

    def _fake_build(agent_id, workspace=None, **override):
        calls.append((agent_id, workspace))
        return object()

    monkeypatch.setattr(f, "_build_agent", _fake_build)
    a1 = f.create_agent("ghost", workspace="ws1")
    a2 = f.create_agent("ghost", workspace="ws1")
    assert a1 is a2
    assert calls == [("ghost", "ws1")]
    # A different workspace is a distinct build.
    f.create_agent("ghost", workspace="ws2")
    assert len(calls) == 2


def test_fingerprint_reacts_to_definition_file_change(tmp_path):
    # Real fingerprint over on-disk definition files: touching instructions.md
    # (mtime + size change) must change the fingerprint.
    defs = tmp_path / "definitions"
    (defs / "demo").mkdir(parents=True)
    instr = defs / "demo" / "instructions.md"
    instr.write_text("v1", encoding="utf-8")

    fp1 = agent_cache.compute_fingerprint("demo", "ws", {}, definitions_dir=defs)
    fp_same = agent_cache.compute_fingerprint("demo", "ws", {}, definitions_dir=defs)
    assert fp1 == fp_same                    # stable when nothing changed

    instr.write_text("v2 longer content", encoding="utf-8")
    fp2 = agent_cache.compute_fingerprint("demo", "ws", {}, definitions_dir=defs)
    assert fp1 != fp2                        # instructions edit → new fingerprint
