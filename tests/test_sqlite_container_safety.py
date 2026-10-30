"""A container must never open the hub's SQLite database where the docker
daemon does not share this machine's kernel (Docker Desktop on macOS or
Windows): SQLite's locks and shared memory do not cross that VM, a container
closing the database deletes the WAL under the hub, and the hub's later
writes are lost on restart. No agent container opens it any more: the state
dir is not mounted and every store call goes to the hub over HTTP
(common/remote_state.py), so docker mode puts every kind of run in a
container there too. db_shared_with_containers still answers for the
operator tools that ask it (the state volume and its sync).
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


def test_task_containers_relay_their_records_even_where_the_database_could_be_shared(monkeypatch):
    monkeypatch.setattr("common.config.run_state_transport", lambda: "db")
    monkeypatch.setattr(cm, "db_shared_with_containers", lambda: True)
    assert cm.container_state_transport() == "http"


def test_every_hub_process_goes_into_a_container_on_docker_desktop(desktop, monkeypatch):
    """Task runs, teams, flows, loops and instances: none of them opens the
    database in its container, so Docker Desktop no longer keeps them local."""
    monkeypatch.setattr("common.config.agent_execution_mode", lambda workspace=None: "docker")
    from common.config import run_execution_mode
    from instances.carrier import _execution_mode
    from runtime.entity_launch import execution_mode_for
    assert run_execution_mode("w") == "docker"   # task runs (agent_launcher)
    assert execution_mode_for("w") == "docker"   # teams, flows, loops
    assert _execution_mode("w") == "docker"      # instances


def test_shell_commands_still_go_into_a_container_on_docker_desktop(desktop, monkeypatch):
    from common.config import settings
    from tools import shell
    monkeypatch.setattr(settings, "agent_mode", "docker")
    monkeypatch.delenv("AGENTS_HUB_RUN_NETWORK", raising=False)
    assert shell._shell_in_container() is True


def test_the_override_hint_says_yes_on_docker_desktop(desktop, monkeypatch):
    from agents.capability_guard import override_honoured_at_build
    from common.config import settings
    monkeypatch.setattr(settings, "capability_override_requires_container", True)
    monkeypatch.setattr(settings, "agent_mode", "docker")
    monkeypatch.setattr(settings, "agent_docker_network", "none")
    assert override_honoured_at_build("a") is True


def test_a_docker_spec_launches_in_a_container_on_docker_desktop(desktop, monkeypatch, tmp_path):
    from runtime import entity_launch
    marks = {}
    monkeypatch.setattr(entity_launch, "spawn_docker",
                        lambda spec, env: {"container_name": "agents-hub-run-r"})
    monkeypatch.setattr(entity_launch, "spawn_local", lambda spec, env: pytest.fail("ran locally"))
    monkeypatch.setattr("common.entity_runs.mark_running", lambda run_id, **kw: marks.update(kw))
    out = entity_launch.launch_prepared({
        "kind": "flow", "run_id": "r", "entity_id": "f", "workspace": "", "header": "Flow run started",
        "log_file": str(tmp_path / "r.log"), "execution_mode": "docker",
    })
    assert out["execution_mode"] == "docker" and marks["container_name"] == "agents-hub-run-r"


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


def test_postgres_runs_go_into_containers_without_the_database(monkeypatch):
    """An agent container gets no database URL and is not on the database's
    network: an instance in one used to write its ready mark and heartbeat to
    an SQLite file in the state mount. It now has no state mount and no
    database URL at all, and its store calls go to the hub."""
    from common import db
    monkeypatch.setattr(cm.sys, "platform", "linux")
    monkeypatch.setattr(cm, "_fs_type", lambda path: "ext4")
    monkeypatch.setattr(db, "is_postgres", lambda: True)
    assert cm.db_shared_with_containers() is False
    from common import config
    monkeypatch.setattr(config, "agent_execution_mode", lambda workspace=None: "docker")
    assert config.run_execution_mode("dev") == "docker"
    env = cm.remote_container_env({
        "AGENTS_HUB_DATABASE_URL": "postgresql://hub:pw@postgres/hub", "POSTGRES_PASSWORD": "pw",
        "AGENTS_HUB_BROKER_URL": "redis://:pw@redis:6379/0", "AGENTS_HUB_SECRET_KEY": "k",
        "AGENTS_HUB_BLOB_SECRET": "s", "AWS_SECRET_ACCESS_KEY": "s", "OPENAI_API_KEY": "sk",
        "AGENTS_HUB_RUN_TOKEN": "ahrun_x", "AGENT_WORKSPACE": "dev"})
    for gone in ("AGENTS_HUB_DATABASE_URL", "POSTGRES_PASSWORD", "AGENTS_HUB_BROKER_URL",
                 "AGENTS_HUB_SECRET_KEY", "AGENTS_HUB_BLOB_SECRET", "AWS_SECRET_ACCESS_KEY"):
        assert gone not in env, gone
    assert env["AGENTS_HUB_REMOTE_STATE"] == "1" and env["AGENT_RUN_STATE_TRANSPORT"] == "http"
    assert env["AGENTS_HUB_RUN_TOKEN"] == "ahrun_x" and env["AGENT_WORKSPACE"] == "dev"
    assert env["AGENTS_HUB_CALLBACK_URL"].startswith("http://")


def test_a_run_container_mounts_no_state_but_its_logs():
    argv = cm.build_run_command(
        container_name="c", agent_id="a", image="img", network="agents-hub",
        translated_cmd=["python", "-m", "runtime.agent_run"], state_dir="/state",
        tasks_dir="/repo/tasks", workspace="/state/workspaces/w",
        env={"AGENTS_HUB_DATABASE_URL": "postgresql://x", "AGENTS_HUB_RUN_TOKEN": "ahrun_x"})
    joined = " ".join(argv)
    assert "/state:/app/.agents_hub" not in joined and "dst=/app/.agents_hub," not in joined
    assert "/app/.agents_hub:rw,mode=1777" in argv
    assert any("/state/run_logs" in a for a in argv) and any("/state/instance_logs" in a for a in argv)
    assert "AGENTS_HUB_REMOTE_STATE=1" in argv and "AGENTS_HUB_RUN_TOKEN=ahrun_x" in argv
    assert not any(a.startswith("AGENTS_HUB_DATABASE_URL=") for a in argv)


def test_the_hub_alias_is_the_containers_address_in_compose(monkeypatch):
    monkeypatch.setattr(cm, "_host_project_root", lambda: "/host/repo")
    monkeypatch.setattr("common.config.live_setting", lambda key, default="": "")
    assert cm.container_hub_url() == f"http://{cm.HUB_NETWORK_ALIAS}:8000"
    monkeypatch.setattr(cm, "_host_project_root", lambda: "")
    assert cm.container_hub_url() == "http://host.docker.internal:8000"
    monkeypatch.setattr("common.config.live_setting",
                        lambda key, default="": "http://10.0.0.5:8000/" if key == cm.CONTAINER_HUB_URL_SETTING else "")
    assert cm.container_hub_url() == "http://10.0.0.5:8000"


def test_service_agents_stay_local_in_docker_mode(monkeypatch):
    """They see every workspace, which a container's token never reaches."""
    monkeypatch.setattr("common.config.agent_execution_mode", lambda workspace=None: "docker")
    from common.config import run_execution_mode
    from instances.carrier import _execution_mode
    assert run_execution_mode("w", "service_agent") == "local"
    assert run_execution_mode("w", "swe_agent") == "docker"
    assert _execution_mode("w", {"execution_mode": "docker"}, "system_doctor") == "local"
    assert _execution_mode("w", None, "swe_agent") == "docker"
