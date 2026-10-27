"""The hub's state on a Docker volume (docker-compose.volume.yml): the way a
hub in Docker on Docker Desktop can run agents in containers. Every path under
the state dir reaches an agent container as a ``--mount`` of that volume with
a ``volume-subpath``, never as a host folder (which would break SQLite), and
the container runs as the hub's user, since a volume keeps real ownership.
"""
import os

import pytest

from common.paths import AGENTS_HUB_ROOT
from managers import container_manager as cm


@pytest.fixture
def on_volume(monkeypatch):
    monkeypatch.setenv(cm.STATE_VOLUME_ENV, "agents_hub_state")


def test_a_state_path_mounts_the_volume_with_its_subpath(on_volume):
    ws = AGENTS_HUB_ROOT / "workspaces" / "alpha"
    assert cm.mount_flags(ws, "/workspace") == [
        "--mount", "type=volume,src=agents_hub_state,dst=/workspace,volume-subpath=workspaces/alpha"]


def test_the_state_root_mounts_the_whole_volume_read_only_when_asked(on_volume):
    assert cm.mount_flags(AGENTS_HUB_ROOT, "/app/.agents_hub", readonly=True) == [
        "--mount", "type=volume,src=agents_hub_state,dst=/app/.agents_hub,readonly"]


def test_a_path_outside_the_state_stays_a_bind_mount(on_volume, tmp_path):
    flags = cm.mount_flags(tmp_path, "/x")
    assert flags[0] == "-v" and flags[1].endswith(":/x")


def test_without_a_volume_everything_is_a_bind_mount(monkeypatch):
    monkeypatch.delenv(cm.STATE_VOLUME_ENV, raising=False)
    flags = cm.mount_flags(AGENTS_HUB_ROOT / "workspaces" / "alpha", "/workspace", readonly=True)
    assert flags[0] == "-v" and flags[1].endswith(":/workspace:ro")
    assert cm.user_flags() == []


def test_containers_run_as_the_hub_user_on_a_volume(on_volume):
    assert cm.user_flags() == ["--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp"]


def test_a_run_container_gets_the_volume_not_host_folders(on_volume):
    argv = cm.build_run_command(
        container_name="c", agent_id="a", image="img", network="agents-hub",
        translated_cmd=["python", "-m", "runtime.agent_run"],
        state_dir=str(AGENTS_HUB_ROOT), tasks_dir="/repo/tasks",
        workspace=str(AGENTS_HUB_ROOT / "workspaces" / "alpha"),
        snapshot_dir=str(AGENTS_HUB_ROOT / "run_snapshots" / "r1"),
    )
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
    assert "type=volume,src=agents_hub_state,dst=/app/.agents_hub" in mounts
    assert ("type=volume,src=agents_hub_state,dst=/workspace,volume-subpath=workspaces/alpha"
            in mounts)
    assert any("volume-subpath=run_snapshots/r1,readonly" in m for m in mounts)
    assert "--user" in argv


def test_a_shell_container_mounts_its_folder_from_the_volume(on_volume):
    argv = cm.build_shell_command(container_name="c", agent_id="a", image="img", network="none",
                                  workspace=str(AGENTS_HUB_ROOT / "workspaces" / "alpha"), command="ls")
    assert "type=volume,src=agents_hub_state,dst=/workspace,volume-subpath=workspaces/alpha" in argv


def test_run_code_mounts_its_snippet_and_workspace_from_the_volume(on_volume):
    from sandbox.docker import _volume_mounts
    code, ws = _volume_mounts(str(AGENTS_HUB_ROOT / "code_runs" / "run-1"),
                              str(AGENTS_HUB_ROOT / "workspaces" / "alpha"))
    assert code == ["--mount", "type=volume,src=agents_hub_state,dst=/code,volume-subpath=code_runs/run-1,readonly"]
    assert ws == ["--mount", "type=volume,src=agents_hub_state,dst=/work,volume-subpath=workspaces/alpha,readonly"]


def test_the_hub_state_location_never_reaches_a_run_container():
    env = cm.container_env({"AGENTS_HUB_ROOT": "/data/agents_hub",
                            "AGENTS_HUB_STATE_VOLUME": "agents_hub_state", "AGENT_WORKSPACE": "alpha"})
    assert env == {"AGENT_WORKSPACE": "alpha"}


def test_a_hub_with_state_on_a_volume_may_share_the_database(monkeypatch):
    """Inside the hub container the volume reads as the VM's own disk."""
    monkeypatch.setattr(cm.sys, "platform", "linux")
    monkeypatch.setattr(cm, "_fs_type", lambda path: "ext4")
    assert cm.db_shared_with_containers() is True
