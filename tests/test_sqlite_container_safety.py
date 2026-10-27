"""A container must never open the hub's SQLite database where the docker
daemon does not share this machine's kernel (Docker Desktop on macOS or
Windows): SQLite's locks and shared memory do not cross that VM, a container
closing the database deletes the WAL under the hub, and the hub's later
writes are lost on restart. Task runs then relay their records over http
with the state dir read-only; teams, flows, loops and instances run locally.
"""
import pytest

from managers import container_manager as cm


@pytest.fixture
def desktop(monkeypatch):
    """Docker Desktop: the hub on a macOS host."""
    monkeypatch.setattr(cm.sys, "platform", "darwin")


def test_docker_desktop_cannot_share_the_database(desktop):
    assert cm.db_shared_with_containers() is False


def test_a_linux_host_with_local_state_can(monkeypatch):
    monkeypatch.setattr(cm.sys, "platform", "linux")
    monkeypatch.setattr(cm, "_fs_type", lambda path: "ext4")
    assert cm.db_shared_with_containers() is True


@pytest.mark.parametrize("fstype", ["fakeowner", "virtiofs", "fuse.grpcfuse", "osxfs", "nfs4", "cifs"])
def test_a_hub_in_docker_on_a_mac_cannot(monkeypatch, fstype):
    """Inside a container the platform reads linux; the state folder carried
    in from macOS gives it away. Measured: two containers on such a folder
    lose the WAL just like a host process and a container do."""
    monkeypatch.setattr(cm.sys, "platform", "linux")
    monkeypatch.setattr(cm, "_fs_type", lambda path: fstype)
    assert cm.db_shared_with_containers() is False


def test_fs_type_takes_the_longest_mount(monkeypatch):
    mounts = "overlay / overlay rw 0 0\n/run/host_mark/x /app fakeowner rw 0 0\n/dev/vda1 /app/data ext4 rw 0 0\n"
    monkeypatch.setattr(cm.Path, "read_text", lambda self, encoding=None: mounts)
    monkeypatch.setattr(cm.Path, "resolve", lambda self: self)
    assert cm._fs_type(cm.Path("/app/.agents_hub")) == "fakeowner"
    assert cm._fs_type(cm.Path("/app/data/x")) == "ext4"
    assert cm._fs_type(cm.Path("/etc")) == "overlay"


def test_task_containers_relay_their_records_on_docker_desktop(desktop):
    assert cm.container_state_transport() == "http"


def test_task_containers_keep_the_configured_transport_where_shared(monkeypatch):
    monkeypatch.setattr(cm, "db_shared_with_containers", lambda: True)
    assert cm.container_state_transport() == "db"


def test_every_hub_process_stays_local_on_docker_desktop(desktop, monkeypatch):
    """Task runs too: a read-only relay does not cover what an agent build
    reads from the database, so a container run could only fail."""
    monkeypatch.setattr("common.config.agent_execution_mode", lambda workspace=None: "docker")
    from common.config import run_execution_mode
    from instances.carrier import _execution_mode
    from runtime.entity_launch import execution_mode_for
    assert run_execution_mode("w") == "local"   # task runs (agent_launcher)
    assert execution_mode_for("w") == "local"   # teams, flows, loops
    assert _execution_mode("w") == "local"      # instances


def test_shell_commands_still_go_into_a_container_on_docker_desktop(desktop, monkeypatch):
    from common.config import settings
    from tools import shell
    monkeypatch.setattr(settings, "agent_mode", "docker")
    monkeypatch.delenv("AGENTS_HUB_RUN_NETWORK", raising=False)
    assert shell._shell_in_container() is True


def test_the_override_hint_says_no_on_docker_desktop(desktop, monkeypatch):
    from agents.capability_guard import override_honoured_at_build
    from common.config import settings
    monkeypatch.setattr(settings, "capability_override_requires_container", True)
    monkeypatch.setattr(settings, "agent_mode", "docker")
    monkeypatch.setattr(settings, "agent_docker_network", "none")
    assert override_honoured_at_build("a") is False


def test_a_docker_spec_launches_locally_on_docker_desktop(desktop, monkeypatch, tmp_path):
    from runtime import entity_launch
    marks = {}
    monkeypatch.setattr(entity_launch, "spawn_docker", lambda spec, env: pytest.fail("container started"))
    monkeypatch.setattr(entity_launch, "spawn_local", lambda spec, env: marks.setdefault("header", spec["header"]) and 4242)
    monkeypatch.setattr("common.entity_runs.mark_running", lambda run_id, **kw: marks.update(kw))
    out = entity_launch.launch_prepared({
        "kind": "flow", "run_id": "r", "entity_id": "f", "workspace": "", "header": "Flow run started",
        "log_file": str(tmp_path / "r.log"), "execution_mode": "docker",
    })
    assert out["execution_mode"] == "local" and marks["execution_mode"] == "local"
    assert "on the hub" in marks["header"]


def test_a_container_log_path_reads_as_the_host_path():
    from common.paths import AGENTS_HUB_ROOT, host_state_path
    assert host_state_path("/app/.agents_hub/run_logs/a.log") == str(AGENTS_HUB_ROOT / "run_logs" / "a.log")
    assert host_state_path("/elsewhere/a.log") == "/elsewhere/a.log"
    assert host_state_path(None) is None


def test_flow_run_accepts_the_container_log_flag():
    import subprocess
    import sys
    from common.paths import PROJECT_ROOT
    out = subprocess.run([sys.executable, str(PROJECT_ROOT / "runtime" / "flow_run.py"), "--help"],
                         capture_output=True, text=True, timeout=60)
    assert "--write-stdout-to-log" in out.stdout
