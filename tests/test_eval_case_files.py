"""An eval case's workspace files must belong to the eval set's workspace:
the routes refuse another workspace's id, and the runner copies only the
run's own workspace files whatever a stored case says."""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))

from evals import runner, store
from evals.models import Case, EvalSet
from files import service
from routes import evals as eval_routes


def run(coro):
    return asyncio.run(coro)


def _set(workspace):
    es = EvalSet(name="files", workspace=workspace)
    es.set_target({"kind": "agent", "id": "helper"})
    return store.save_eval_set(es)


def test_adding_a_case_with_another_workspace_file_is_refused():
    theirs = service.create_file("beta", "theirs.md", b"secret")
    evalset = _set("acme")
    with pytest.raises(HTTPException) as exc:
        run(eval_routes.add_eval_case(evalset.eval_set_id,
                                      eval_routes.CaseIn(input="q", file_ids=[theirs["file_id"]])))
    assert exc.value.status_code == 400


def test_own_files_are_kept_and_deduplicated():
    mine = service.create_file("acme", "mine.md", b"mine")
    evalset = _set("acme")
    out = run(eval_routes.add_eval_case(evalset.eval_set_id, eval_routes.CaseIn(
        input="q", file_ids=[mine["file_id"], mine["file_id"]])))
    assert out["case"]["file_ids"] == [mine["file_id"]]


def test_the_runner_copies_only_the_runs_own_workspace_files(tmp_path):
    mine = service.create_file("acme", "mine.md", b"mine")
    theirs = service.create_file("beta", "theirs.md", b"secret")
    case = Case(case_id="c1", input="q", file_ids=[mine["file_id"], theirs["file_id"]])
    directory = Path(runner.prepare_work_dir(case, "evrun_x", "acme"))
    names = {p.name for p in directory.rglob("*") if p.is_file()}
    assert "mine.md" in names and "theirs.md" not in names
