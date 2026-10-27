"""The tool gate refuses a URL the web domain policy rules out before anything
else runs (agents/hooks.py ``ToolGuard.evaluate``, tools/web.py
``precheck_call``): no hook, no classifier and no approval card for a fetch the
tool would refuse anyway. An allowed URL goes through the gate as before."""
from __future__ import annotations

import pytest

from agents import hooks
from agents.registry import AgentSpec
from tools import permission_policy as policy
from tools import web


@pytest.fixture
def lists(monkeypatch):
    from common.config import settings
    monkeypatch.setattr("common.config.read_dot_env", lambda: {})
    monkeypatch.setattr("common.config.live_setting", lambda key, default="": default)
    monkeypatch.setattr(settings, "web_deny_domains", ())
    monkeypatch.setattr(settings, "web_allow_domains", ())
    monkeypatch.setattr(settings, "web_domain_policy_enabled", False)
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    monkeypatch.delenv("AGENT_TASK_ID", raising=False)
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    monkeypatch.setattr(hooks, "load_hooks", lambda ws: {})
    monkeypatch.setattr("workspace.get_workspace_metadata", lambda name: {"settings": {}})
    ws: dict = {}
    monkeypatch.setattr(web, "_workspace_web_settings", lambda workspace=None: ws)
    monkeypatch.setattr(web, "_current_agent_spec", lambda: None)
    return ws


def _spec(mode="always_ask", blocked=()):
    return AgentSpec(id="webby", name="Webby", type="local", entrypoint="m:f",
                     tool_policy={"fetch_url": mode}, blocked_domains=list(blocked))


def _guard(spec):
    return hooks.ToolGuard(agent_id="webby", spec=spec, workspace="acme")


def test_a_denied_host_is_refused_before_the_card(lists, monkeypatch):
    lists["web_deny_domains"] = ["evil.example"]

    def no_hook(*a, **k):
        raise AssertionError("the hook must not run for a refused URL")

    monkeypatch.setattr(hooks, "run_pre_tool_use", no_hook)
    verdict = _guard(_spec()).evaluate("fetch_url", {"url": "https://cdn.evil.example/x"})
    assert verdict.pending is None
    assert verdict.refusal.startswith("fetch_url refused 'https://cdn.evil.example/x'")
    assert "deny list" in verdict.refusal
    row = policy.list_decisions(agent_id="webby", limit=5)[0]
    assert (row["decision"], row["by"]) == (policy.DENY, "domain")


def test_the_agent_own_blocked_list_counts_and_auto_never_asks_the_model(lists, monkeypatch):
    monkeypatch.setattr(policy, "classify", lambda **k: (_ for _ in ()).throw(
        AssertionError("the classifier must not run for a refused URL")))
    verdict = _guard(_spec("auto", blocked=["tracker.example"])).evaluate(
        "fetch_url", {"url": "https://tracker.example/"})
    assert "this agent's blocked_domains" in verdict.refusal


def test_off_allowlist_and_bad_scheme_are_refused(lists):
    lists["web_domain_policy_enabled"] = True
    lists["web_allow_domains"] = ["python.org"]
    guard = _guard(_spec())
    assert "not on the domain allowlist" in guard.evaluate(
        "fetch_url", {"url": "https://example.org/"}).refusal
    assert guard.evaluate("fetch_url", {"url": "file:///etc/passwd"}).refusal


def test_an_allowed_host_still_waits_for_a_person(lists):
    lists["web_deny_domains"] = ["evil.example"]
    guard = _guard(_spec())
    # No chat turn and no task: the hold falls back to the gate's own refusal,
    # which is not the domain policy's.
    verdict = guard.evaluate("fetch_url", {"url": "https://docs.python.org/3/"})
    assert "refused 'https://docs.python.org" not in (verdict.refusal or "")
    rows = policy.list_decisions(agent_id="webby", limit=5)
    assert rows and rows[0]["decision"] == policy.ASK


def test_precheck_ignores_other_tools_and_inputs(lists):
    lists["web_deny_domains"] = ["evil.example"]
    assert web.precheck_call("run_shell", {"url": "https://evil.example/"}) is None
    assert web.precheck_call("fetch_url", "https://evil.example/") is None
    assert web.precheck_call("fetch_url", {"url": ""}) is None


def test_the_reason_code(lists):
    assert policy.reason_code(by="domain", decision=policy.DENY) == "domain_policy"
    assert "domain_policy" in policy.REASON_CODES
