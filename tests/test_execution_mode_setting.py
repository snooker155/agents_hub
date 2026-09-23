"""Agent execution mode: resolved live, not frozen at import.

The mode decides whether a node becomes a subprocess of the backend or a
container of its own. It used to be read off the ``settings`` object, which
absorbs .env once at import, so flipping it on the Settings page did nothing
until the process was restarted. These tests pin the resolution order and that
a change reaches the next node start.
"""
import pytest

from common import config
from managers import node_manager


@pytest.fixture
def dot_env(monkeypatch):
    """Control what the live resolvers read, without touching the real .env."""
    state = {}
    monkeypatch.setattr(config, "read_dot_env", lambda: dict(state))
    monkeypatch.delenv("AGENT_EXECUTION_MODE", raising=False)
    return state


# ── live_setting ─────────────────────────────────────────────────────────────

def test_the_file_wins_over_the_process_environment(dot_env, monkeypatch):
    monkeypatch.setenv("AGENT_DOCKER_IMAGE", "from-env:latest")
    assert config.live_setting("AGENT_DOCKER_IMAGE") == "from-env:latest"
    dot_env["AGENT_DOCKER_IMAGE"] = "from-file:latest"
    assert config.live_setting("AGENT_DOCKER_IMAGE") == "from-file:latest"


def test_a_cleared_field_does_not_fall_back_to_the_environment(dot_env, monkeypatch):
    """Emptying the box in the UI means empty, not "whatever the env holds"."""
    monkeypatch.setenv("AGENT_DOCKER_IMAGE", "from-env:latest")
    dot_env["AGENT_DOCKER_IMAGE"] = ""
    assert config.live_setting("AGENT_DOCKER_IMAGE") == ""
    assert config.live_setting("AGENT_DOCKER_IMAGE", "fallback") == "fallback"


def test_the_default_is_the_floor(dot_env):
    assert config.live_setting("AGENT_DOCKER_IMAGE", "agents-hub/base:latest") == "agents-hub/base:latest"


# ── agent_execution_mode ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("docker", "docker"), ("Docker", "docker"), ("  docker  ", "docker"),
    ("local", "local"), ("", "local"),
    ("kubernetes", "local"), ("dcoker", "local"),
])
def test_mode_shapes(dot_env, raw, expected):
    """Anything unrecognised reads as local: the mode that needs no daemon."""
    dot_env["AGENT_EXECUTION_MODE"] = raw
    assert config.agent_execution_mode() == expected


def test_a_container_can_force_the_mode_without_the_file(dot_env, monkeypatch):
    monkeypatch.setenv("AGENT_EXECUTION_MODE", "docker")
    assert config.agent_execution_mode() == "docker"


def test_the_value_is_re_read_per_call(dot_env):
    dot_env["AGENT_EXECUTION_MODE"] = "local"
    assert config.agent_execution_mode() == "local"
    dot_env["AGENT_EXECUTION_MODE"] = "docker"   # the Settings page just wrote
    assert config.agent_execution_mode() == "docker"


# ── what the launcher does with it ───────────────────────────────────────────

@pytest.fixture
def launched(monkeypatch):
    """Capture how a node would be started, without starting anything."""
    calls = {"container": [], "subprocess": []}

    import managers.container_manager as cm

    class _Spec:
        name = "Code Reviewer"
        node_type = "worker"
        http_expose = False

    # The test state root holds no agents.json, and which agent this is does
    # not matter to the mode decision.
    monkeypatch.setattr(node_manager, "get_agent", lambda agent_id: _Spec())

    def _fake_start_node_container(node_id, agent_id, inner_cmd, workspace=None,
                                   env=None, **kwargs):
        calls["container"].append(agent_id)
        return {"success": True, "container_id": "deadbeef",
                "container_name": f"agents-hub-node-{node_id[:12]}",
                "image": "agents-hub/base:latest", "error": None}

    class _FakeProc:
        pid = 4242

    monkeypatch.setattr(cm, "start_node_container", _fake_start_node_container)
    monkeypatch.setattr(
        node_manager.subprocess, "Popen",
        lambda *a, **k: calls["subprocess"].append(a) or _FakeProc(),
    )
    # get_node()/_sync_status() probes a docker-mode node with container_running(),
    # which shells out to `docker ps`. `subprocess.Popen` above is the same global
    # module object container_manager's `subprocess.run` uses internally, and the
    # bare `_FakeProc` fake is not a context manager, so without this the probe
    # would hit a real (or broken-fake) subprocess call the fixture never intended
    # to exercise. Pretend nothing is running, the same as a docker-less test host.
    monkeypatch.setattr(cm, "container_running", lambda name: False)
    return calls


def test_a_change_on_the_settings_page_reaches_the_next_node(dot_env, launched):
    """No restart in between: the second node starts in the other mode."""
    dot_env["AGENT_EXECUTION_MODE"] = "local"
    node_manager.start_node("code_reviewer", label="first")
    assert launched["subprocess"] and not launched["container"]

    dot_env["AGENT_EXECUTION_MODE"] = "docker"
    node_id = node_manager.start_node("code_reviewer", label="second")
    assert launched["container"] == ["code_reviewer"]
    assert node_manager.get_node(node_id)["execution_mode"] == "docker"
