"""
``managers.run_manager`` is a facade over ``managers.runs``.

The split was a refactor, so the tests that matter are the ones a refactor can
break without any single behaviour test noticing: that every name the rest of
the codebase imports from the old module is still there with the same
signature, that patching a name on the facade still reaches the code that uses
it, that the modules are layered the way they claim (nothing imports upwards),
and that the two things which silently depend on a module's *location* — the
walk up to the repository root, and the relative import of the container
manager — survived being moved a directory deeper.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import subprocess
from pathlib import Path

from managers import run_manager as rm

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULES = ("notifications", "store", "lifecycle", "task_finalize", "groups")


def _names_in_head_revision() -> dict:
    """Top-level names defined by run_manager.py before the split, with the
    parameter list of each function, read straight out of git."""
    src = subprocess.run(
        ["git", "show", "HEAD:managers/run_manager.py"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, check=True,
    ).stdout
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            params = ([a.arg for a in args.posonlyargs] + [a.arg for a in args.args]
                      + ([args.vararg.arg] if args.vararg else [])
                      + [a.arg for a in args.kwonlyargs]
                      + ([args.kwarg.arg] if args.kwarg else []))
            out[node.name] = params
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = None
    return out


# ── The facade ────────────────────────────────────────────────────────────────

def test_every_name_the_old_module_defined_is_still_importable():
    """Nine packages and three dozen test modules import from here. A name that
    quietly stopped being re-exported is an import error at someone else's
    startup, not a failure here."""
    missing = sorted(name for name in _names_in_head_revision() if not hasattr(rm, name))
    assert missing == []


def test_every_call_that_worked_before_still_works():
    """Every parameter the old module took is still taken, positionals in the
    same order. A parameter may be *added* (query_runs grew a flow_run_id
    filter for the run groups), but only with a default, so no existing call
    site has to change."""
    expected = _names_in_head_revision()
    problems = []
    for name, params in expected.items():
        if params is None:
            continue
        signature = inspect.signature(getattr(rm, name))
        actual = list(signature.parameters)
        if [p for p in actual if p in params] != params:
            problems.append((name, params, actual))
            continue
        for added in (p for p in actual if p not in params):
            if signature.parameters[added].default is inspect.Parameter.empty:
                problems.append((name, "new required parameter", added))
    assert problems == []


def test_private_helpers_other_modules_reach_for_are_re_exported():
    """The watchdog and the team runner name these directly; a facade that
    hid them would be a breaking change dressed up as a refactor."""
    for name in ("_pid_exists", "_stop_run_record", "_update_run", "_upsert_run",
                 "_apply", "_utc_now_iso", "_publish_run_delta", "_sync_instance",
                 "_notify_task_run_started", "_notify_task_run_finished",
                 "_task_already_announced", "_trigger_session_continuation"):
        assert callable(getattr(rm, name)), name


def test_patching_a_name_on_the_facade_still_reaches_the_caller(monkeypatch):
    """Callers import from the facade inside their functions, and tests patch
    it there. Re-exporting has to leave that working."""
    monkeypatch.setattr(rm, "get_run_by_id", lambda run_id: {"status": "sentinel"})
    from managers.run_manager import get_run_by_id
    assert get_run_by_id("anything") == {"status": "sentinel"}


# ── The layering ──────────────────────────────────────────────────────────────

def test_each_concern_lives_in_its_own_module():
    """The map, asserted rather than described in a comment: a function that
    drifts back into the wrong module shows up here."""
    where = {
        "store": ["query_runs", "get_runs_by_ids", "session_run_stats", "upsert_run",
                  "update_run", "delete_run", "load_runs", "save_runs", "_apply",
                  "get_run_process", "seed_run_input_context", "compact_runs"],
        "lifecycle": ["preopen_run", "open_run", "close_run", "close_run_from_result",
                      "new_unique_run_id", "utc_now_iso", "run_log_path",
                      "get_run_by_id", "stop_run", "stop_run_by_id", "_stop_run_record",
                      "get_status", "_pid_exists", "get_in_progress_runs_for_node"],
        "task_finalize": ["finalize_task_from_run", "finalize_flow_task",
                          "park_task_awaiting_input", "_auto_start_review",
                          "_maybe_retry_failed_run", "_trigger_session_continuation",
                          "delete_awaiting_approval_run", "delete_assigned_run"],
        "notifications": ["_notify_task_run_finished", "_notify_task_run_started",
                          "_task_already_announced", "_publish_run_delta",
                          "_sync_instance"],
    }
    for module, names in where.items():
        for name in names:
            actual = getattr(rm, name).__module__
            assert actual == f"managers.runs.{module}", f"{name} is in {actual}"


def test_imports_point_one_way_only():
    """notifications ← store ← lifecycle ← task_finalize. A top-level import
    pointing back up the list is an import cycle waiting for the first caller
    that happens to load the modules in the other order."""
    allowed = {
        "notifications": set(),
        "store": {"notifications"},
        "lifecycle": {"notifications", "store"},
        "task_finalize": {"notifications", "store", "lifecycle"},
    }
    for module, may_import in allowed.items():
        path = PROJECT_ROOT / "managers" / "runs" / f"{module}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 1 or not node.module:
                continue
            # A deferred import inside a function is how the store reaches back
            # up for the one legacy fallback that needs it; only module-level
            # imports can deadlock at import time.
            at_top = any(node is child for child in tree.body)
            if at_top:
                assert node.module in may_import, f"{module} imports {node.module}"


def test_every_submodule_imports_on_its_own():
    """Import order must not matter: each module has to load first."""
    for name in MODULES:
        importlib.import_module(f"managers.runs.{name}")


# ── What moving a directory deeper could have broken ──────────────────────────

def test_the_walk_up_to_the_repository_root_still_lands_on_the_repository():
    """``PROJECT_ROOT`` and the continuation launcher both count directories up
    from ``__file__``; the code moved from managers/ into managers/runs/."""
    assert rm.PROJECT_ROOT == PROJECT_ROOT
    assert (rm.PROJECT_ROOT / "runtime" / "agent_run.py").exists()

    from managers.runs import task_finalize
    src = inspect.getsource(task_finalize._trigger_session_continuation)
    assert "parents[2]" in src


def test_the_container_manager_is_still_reachable_from_the_stop_path():
    """It was a sibling module (``from .container_manager import ...``) and is
    now a sibling of the package instead."""
    from managers.runs import lifecycle
    src = inspect.getsource(lifecycle)
    assert "from ..container_manager import" in src
    assert "from .container_manager import" not in src


def test_the_run_log_path_is_unchanged():
    assert rm.run_log_path("abc").name == "agent_run_abc.log"
    assert rm.run_log_path("abc").parent == rm.RUN_LOGS_DIR


# ── Behaviour still flows through the seams ───────────────────────────────────

def test_a_record_written_through_the_facade_reads_back_through_it():
    rm.upsert_run({"run_id": "r-1", "agent_id": "a", "status": "running",
                   "workspace": "ws", "custom_key": {"kept": True}})
    rec = rm.get_run_by_id("r-1")
    assert rec["agent_id"] == "a"
    assert rec["custom_key"] == {"kept": True}

    rm.update_run("r-1", {"status": "completed", "exit_code": 0})
    rec = rm.get_run_by_id("r-1")
    assert rec["status"] == "completed"
    assert rec["custom_key"] == {"kept": True}

    assert rm.query_runs(workspace="ws")["total"] == 1
    assert rm.delete_run("r-1") is True
    assert rm.get_run_by_id("r-1") is None


def test_the_store_still_fans_a_status_change_out_to_the_notifications(monkeypatch):
    """The one call the split had to keep wired: the store's single
    read-modify-write is what every execution channel funnels through, so the
    inbox hook has to hang off it and not off any one caller."""
    seen = []
    from managers.runs import store
    monkeypatch.setattr(store, "_notify_task_run_finished",
                        lambda old, new: seen.append((old.get("status"), new.get("status"))))

    rm.upsert_run({"run_id": "r-2", "agent_id": "a", "status": "running"})
    rm.update_run("r-2", {"status": "completed"})
    assert seen == [("running", "completed")]
