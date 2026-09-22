"""One project root, computed once.

Before this pass, a dozen modules each recomputed the repository root from
their own ``__file__`` with a different ``parents[N]`` — easy to get wrong
(cli/backend.py's ``_ensure_importable`` did) and impossible to audit at a
glance. ``common.paths.PROJECT_ROOT`` is now the single source; every module
that needs the root either imports it from there or, where it genuinely
cannot yet (a subprocess entrypoint spawned before the repo root is on
sys.path), computes the identical value independently. These tests pin both
groups.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from common.paths import PROJECT_ROOT


# ── Modules that import PROJECT_ROOT from common.paths ──────────────────────

def test_agents_hub_launcher_matches():
    import agents_hub
    assert agents_hub.PROJECT_ROOT == PROJECT_ROOT


def test_rag_query_matches():
    import memory.rag_query as rag_query
    assert rag_query._PROJECT_ROOT == PROJECT_ROOT


def test_node_manager_matches():
    import managers.node_manager as node_manager
    assert node_manager.PROJECT_ROOT == PROJECT_ROOT


def test_container_manager_matches():
    import managers.container_manager as container_manager
    assert container_manager.PROJECT_ROOT == PROJECT_ROOT


def test_runs_lifecycle_matches():
    from managers.runs import lifecycle
    assert lifecycle.PROJECT_ROOT == PROJECT_ROOT


def test_run_manager_facade_still_reexports_the_same_root():
    """managers.run_manager re-exports lifecycle.PROJECT_ROOT (and HERE) by
    name for its own callers; the split must not change what they get."""
    import managers.run_manager as run_manager
    assert run_manager.PROJECT_ROOT == PROJECT_ROOT


def test_node_run_matches():
    import runtime.node_run as node_run
    assert node_run.PROJECT_ROOT == PROJECT_ROOT


def test_agent_launcher_matches():
    import agents.agent_launcher as agent_launcher
    assert agent_launcher.PROJECT_ROOT == PROJECT_ROOT


def test_flow_launcher_matches():
    import flow.launcher as flow_launcher
    assert flow_launcher.PROJECT_ROOT == PROJECT_ROOT


def test_cli_main_matches():
    from cli import main as cli_main
    assert cli_main.PROJECT_ROOT == PROJECT_ROOT


def test_vector_store_matches():
    """Imported the way the backend and tests both do: dashboard/backend on
    sys.path, then a bare ``import rag.vector_store`` (see test_vector_store.py)."""
    backend = str(PROJECT_ROOT / "dashboard" / "backend")
    added = backend not in sys.path
    if added:
        sys.path.insert(0, backend)
    try:
        import rag.vector_store as vector_store
        assert vector_store._PROJECT_ROOT == PROJECT_ROOT
    finally:
        if added:
            sys.path.remove(backend)


# ── cli.backend: the bug the audit flagged ───────────────────────────────────

def test_cli_backend_ensure_importable_uses_the_real_root(monkeypatch):
    """_ensure_importable used to compute its root as Path(__file__).parent,
    i.e. cli/ itself — masked because agents_hub/__init__.py had already put
    the real root on sys.path earlier in the same process. It now asks
    common.paths for the root directly, so it is right even standalone."""
    import cli.backend as cli_backend

    monkeypatch.setattr(sys, "path", [p for p in sys.path if p not in (
        str(PROJECT_ROOT), str(PROJECT_ROOT / "dashboard" / "backend"),
    )])
    cli_backend._ensure_importable()
    assert str(PROJECT_ROOT) in sys.path
    assert str(PROJECT_ROOT / "dashboard" / "backend") in sys.path
    # The old bug's path (cli/dashboard/backend) must not appear.
    assert str(Path(cli_backend.__file__).resolve().parent / "dashboard" / "backend") not in sys.path


# ── Modules that must bootstrap sys.path before common.paths is importable ──
#
# These are spawned as subprocess entrypoints (agent_run.py directly with cwd
# set to a workspace, agents_hub/__init__.py as the very first thing the
# ``agents-hub`` console script runs, cli/main.py callable as a bare
# ``python cli/main.py``): the repo root cannot be imported from common.paths
# before it is the thing that makes common.paths importable. They compute it
# with the same formula as common.paths.PROJECT_ROOT instead, so the *value*
# still agrees even though the *source* has to differ.

@pytest.mark.parametrize("rel_path", [
    "runtime/agent_run.py",
    "agents_hub/__init__.py",
    "cli/main.py",
])
def test_bootstrap_modules_compute_the_same_value(rel_path):
    module_file = PROJECT_ROOT / rel_path
    assert module_file.exists()
    assert module_file.resolve().parents[1] == PROJECT_ROOT
