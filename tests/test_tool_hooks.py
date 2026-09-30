"""PreToolUse / PostToolUse hooks: decisions, matching, failure modes, scrubbing."""
import copy
import json
import os
import stat

import pytest

from agents import hooks


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A workspace folder whose hook config the loader will find."""
    ws = tmp_path / "hooked"
    ws.mkdir()
    monkeypatch.setattr("workspace.storage.WORKSPACES_ROOT", tmp_path)
    monkeypatch.setenv("AGENT_WORKSPACE", "hooked")
    return ws


def _write_hooks(ws, config):
    (ws / hooks.HOOKS_FILENAME).write_text(json.dumps(config), encoding="utf-8")


def _script(ws, name, body):
    """An executable hook script inside the workspace."""
    path = ws / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


# -------------------- matcher --------------------

def test_the_matcher_is_a_whole_name_regex():
    assert hooks.matches("run_shell|write_file", "run_shell")
    assert hooks.matches("delete_.*", "delete_file")
    # Not a substring match: a partial name must not drag in its neighbours.
    assert not hooks.matches("run_shell", "run_shell_async")
    # No matcher means every tool.
    assert hooks.matches("", "anything")
    assert hooks.matches("*", "anything")


def test_an_invalid_matcher_selects_nothing():
    # A typo must not accidentally gate the whole tool set.
    assert not hooks.matches("([unclosed", "run_shell")


# -------------------- command hooks --------------------

def test_a_command_hook_exit_zero_allows(workspace):
    _write_hooks(workspace, {"PreToolUse": [
        {"matcher": "run_shell", "type": "command",
         "command": _script(workspace, "ok.sh", "exit 0")},
    ]})
    outcome = hooks.run_pre_tool_use("run_shell", {"command": "ls"}, workspace="hooked")
    assert outcome.decision == "allow"


def test_a_command_hook_exit_two_denies_with_its_stderr(workspace):
    _write_hooks(workspace, {"PreToolUse": [
        {"matcher": "run_shell", "type": "command",
         "command": _script(workspace, "no.sh", "echo 'no shell in this workspace' >&2\nexit 2")},
    ]})
    outcome = hooks.run_pre_tool_use("run_shell", {"command": "ls"}, workspace="hooked")
    assert outcome.denied
    assert "no shell in this workspace" in outcome.reason


def test_a_hook_that_does_not_match_is_not_run(workspace):
    marker = workspace / "ran"
    _write_hooks(workspace, {"PreToolUse": [
        {"matcher": "delete_file", "type": "command",
         "command": _script(workspace, "touch.sh", f"touch {marker}\nexit 2")},
    ]})
    outcome = hooks.run_pre_tool_use("read_file", {"path": "a.txt"}, workspace="hooked")
    assert outcome.decision == "allow"
    assert not marker.exists()


def test_a_timeout_fails_open_but_fail_closed_flips_it(workspace):
    slow = _script(workspace, "slow.sh", "sleep 5\nexit 0")
    _write_hooks(workspace, {"PreToolUse": [
        {"matcher": ".*", "type": "command", "command": slow, "timeout": 0.2},
    ]})
    # A hook that cannot answer is an infrastructure problem, not a verdict:
    # freezing every agent in the workspace is the worse failure.
    assert hooks.run_pre_tool_use("run_shell", {}, workspace="hooked").decision == "allow"

    _write_hooks(workspace, {"PreToolUse": [
        {"matcher": ".*", "type": "command", "command": slow, "timeout": 0.2,
         "fail_closed": True},
    ]})
    assert hooks.run_pre_tool_use("run_shell", {}, workspace="hooked").denied


def test_an_unexpected_exit_code_is_allowed(workspace):
    _write_hooks(workspace, {"PreToolUse": [
        {"matcher": ".*", "type": "command",
         "command": _script(workspace, "broken.sh", "exit 7")},
    ]})
    assert hooks.run_pre_tool_use("run_shell", {}, workspace="hooked").decision == "allow"


def test_a_command_hook_reads_the_call_on_stdin(workspace):
    out = workspace / "payload.json"
    _write_hooks(workspace, {"PreToolUse": [
        {"matcher": ".*", "type": "command",
         "command": _script(workspace, "dump.sh", f"cat > {out}\nexit 0")},
    ]})
    hooks.run_pre_tool_use(
        "run_shell", {"command": "ls"},
        agent_id="swe_agent", run_id="r-1", task_id="t-1", workspace="hooked",
    )
    payload = json.loads(out.read_text())
    assert payload == {
        "hook": "PreToolUse", "agent_id": "swe_agent", "run_id": "r-1",
        "task_id": "t-1", "workspace": "hooked", "tool": "run_shell",
        "input": {"command": "ls"},
    }


def test_a_hook_runs_without_the_provider_keys(workspace, monkeypatch):
    # A hook is operator code, but it is spawned inside an agent run and gets
    # what an agent's shell gets: no keys.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("HARMLESS_SETTING", "kept")
    out = workspace / "env.txt"
    _write_hooks(workspace, {"PreToolUse": [
        {"matcher": ".*", "type": "command",
         "command": _script(workspace, "env.sh", f"env > {out}\nexit 0")},
    ]})
    hooks.run_pre_tool_use("run_shell", {}, workspace="hooked")
    env = out.read_text()
    assert "sk-secret" not in env
    assert "HARMLESS_SETTING=kept" in env


# -------------------- http hooks --------------------

class _Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        return self._body


@pytest.fixture
def http(monkeypatch):
    """Capture the POSTs an HTTP hook makes and script its answers."""
    calls = []
    scripted = {"body": {"decision": "allow"}, "status": 200}

    def _post(url, json=None, timeout=None):
        # Copied: the runner reuses one payload dict across hooks, so keeping the
        # reference would show a later rewrite as if it had been sent.
        calls.append({"url": url, "json": copy.deepcopy(json), "timeout": timeout})
        return _Response(scripted["body"], scripted["status"])

    import requests
    monkeypatch.setattr(requests, "post", _post)
    return {"calls": calls, "scripted": scripted}


@pytest.mark.parametrize("decision", ["allow", "deny", "ask"])
def test_an_http_hook_decision_is_carried_through(workspace, http, decision):
    http["scripted"]["body"] = {"decision": decision, "reason": "because"}
    _write_hooks(workspace, {"PreToolUse": [
        {"matcher": ".*", "type": "http", "url": "http://hook.test/check"},
    ]})
    outcome = hooks.run_pre_tool_use("run_shell", {"command": "ls"}, workspace="hooked")
    assert outcome.decision == decision
    # A reason only travels with a decision that stops something: an allowed
    # call has nothing to explain to anybody.
    assert outcome.reason == ("" if decision == "allow" else "because")
    assert http["calls"][0]["url"] == "http://hook.test/check"
    assert http["calls"][0]["json"]["tool"] == "run_shell"


def test_an_unreachable_http_hook_fails_open(workspace, http):
    http["scripted"]["status"] = 503
    _write_hooks(workspace, {"PreToolUse": [
        {"matcher": ".*", "type": "http", "url": "http://hook.test/check"},
    ]})
    assert hooks.run_pre_tool_use("run_shell", {}, workspace="hooked").decision == "allow"


def test_a_deny_stops_at_the_first_hook(workspace, http):
    http["scripted"]["body"] = {"decision": "deny", "reason": "nope"}
    _write_hooks(workspace, {"PreToolUse": [
        {"matcher": ".*", "type": "http", "url": "http://hook.test/one"},
        {"matcher": ".*", "type": "http", "url": "http://hook.test/two"},
    ]})
    assert hooks.run_pre_tool_use("run_shell", {}, workspace="hooked").denied
    assert [c["url"] for c in http["calls"]] == ["http://hook.test/one"]


# -------------------- post tool use --------------------

def test_an_http_post_hook_can_rewrite_the_output(workspace, http):
    http["scripted"]["body"] = {"decision": "allow", "output": "[redacted]"}
    _write_hooks(workspace, {"PostToolUse": [
        {"matcher": "read_file", "type": "http", "url": "http://hook.test/redact"},
    ]})
    result = hooks.run_post_tool_use("read_file", {"path": "secrets"}, "sk-123", workspace="hooked")
    assert result == "[redacted]"
    assert http["calls"][0]["json"]["output"] == "sk-123"


def test_a_command_post_hook_only_logs(workspace):
    # Its stdout is a log line, not the tool's result: an echo in an audit
    # script must not silently overwrite what the agent reads.
    out = workspace / "audit.log"
    _write_hooks(workspace, {"PostToolUse": [
        {"matcher": ".*", "type": "command",
         "command": _script(workspace, "audit.sh", f"cat >> {out}\necho rewritten\nexit 0")},
    ]})
    result = hooks.run_post_tool_use("run_shell", {"command": "ls"}, "exit_code: 0", workspace="hooked")
    assert result == "exit_code: 0"
    assert json.loads(out.read_text())["output"] == "exit_code: 0"


def test_no_configuration_means_no_work(workspace):
    assert hooks.load_hooks("hooked") == {}
    assert hooks.run_pre_tool_use("run_shell", {}, workspace="hooked").decision == "allow"
    assert hooks.run_post_tool_use("run_shell", {}, "out", workspace="hooked") == "out"


def test_a_broken_config_file_does_not_stop_the_workspace(workspace):
    (workspace / hooks.HOOKS_FILENAME).write_text("{not json", encoding="utf-8")
    assert hooks.load_hooks("hooked") == {}


def test_workspace_metadata_hooks_win_over_the_file(workspace, monkeypatch):
    _write_hooks(workspace, {"PreToolUse": [
        {"matcher": ".*", "type": "command",
         "command": _script(workspace, "file.sh", "exit 2")},
    ]})
    monkeypatch.setattr(
        "workspace.storage.get_workspace_metadata",
        lambda name: {"hooks": {"PreToolUse": []}},
    )
    monkeypatch.setattr("workspace.get_workspace_metadata",
                        lambda name: {"hooks": {"PreToolUse": []}})
    assert hooks.load_hooks("hooked") == {"PreToolUse": []}
    assert hooks.run_pre_tool_use("run_shell", {}, workspace="hooked").decision == "allow"


def test_the_hook_runs_in_the_workspace_folder(workspace):
    out = workspace / "cwd.txt"
    _write_hooks(workspace, {"PreToolUse": [
        {"matcher": ".*", "type": "command", "command": f"pwd > {out}; exit 0"},
    ]})
    hooks.run_pre_tool_use("run_shell", {}, workspace="hooked")
    assert os.path.realpath(out.read_text().strip()) == os.path.realpath(str(workspace))
