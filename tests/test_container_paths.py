"""Bind-mount paths when the backend is itself containerized.

`docker run -v A:B` is resolved by the daemon, not by the process calling it.
So when the backend runs in a container, the paths it knows (`/app/...`) are
meaningless to the daemon: it would create empty host directories under a
non-existent `/app` and the agent would start against blank state. The
HOST_PROJECT_ROOT translation is what keeps the mounts pointing at the real
repository, and these tests pin its edges.
"""
from __future__ import annotations

import pytest

from managers import container_manager as cm


@pytest.fixture
def in_container(monkeypatch):
    """Pretend we are the compose backend: /app here, a real path on the host."""
    monkeypatch.setenv("HOST_PROJECT_ROOT", "/Users/dev/agents_hub")
    return "/Users/dev/agents_hub"


def test_untranslated_on_a_host_run_backend(monkeypatch):
    monkeypatch.delenv("HOST_PROJECT_ROOT", raising=False)
    path = cm.PROJECT_ROOT / "tasks"
    assert cm._host_path(path) == str(path)


def test_project_paths_are_rebased_onto_the_host_root(in_container):
    assert cm._host_path(cm.PROJECT_ROOT / "tasks") == f"{in_container}/tasks"
    assert cm._host_path(cm.PROJECT_ROOT / ".agents_hub") == f"{in_container}/.agents_hub"


def test_the_root_itself_maps_to_the_root(in_container):
    assert cm._host_path(cm.PROJECT_ROOT) == in_container


def test_a_trailing_slash_does_not_leak_into_the_mount(monkeypatch):
    monkeypatch.setenv("HOST_PROJECT_ROOT", "/Users/dev/agents_hub/")
    assert cm._host_path(cm.PROJECT_ROOT / "tasks") == "/Users/dev/agents_hub/tasks"


def test_paths_outside_the_project_are_passed_through(in_container, tmp_path):
    # Nothing to rebase them onto; the daemon may still resolve them, and
    # mangling them here would be a guess. (tmp_path rather than a literal:
    # macOS resolves /var through a symlink, which is not what is under test.)
    outside = tmp_path / "shared"
    outside.mkdir()
    assert cm._host_path(outside) == str(outside)


def test_an_empty_setting_means_no_translation(monkeypatch):
    # Compose always sets the variable, so "" is the signal that we are not in
    # a container — it must not be read as "rebase onto the filesystem root".
    monkeypatch.setenv("HOST_PROJECT_ROOT", "")
    path = cm.PROJECT_ROOT / "tasks"
    assert cm._host_path(path) == str(path)
