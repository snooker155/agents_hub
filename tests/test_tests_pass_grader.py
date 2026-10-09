"""The ``tests_pass`` grader: the case's working directory, as the agent left
it, passes its tests. The command runs through the sandbox provider; here
the local provider, so the suite needs no docker. The folder under test is
built per test, with pytest files that pass or fail on purpose.

Run: ``python -m pytest tests/test_tests_pass_grader.py -q``
"""
from __future__ import annotations

from pathlib import Path

import pytest

from evals import graders
from evals.graders import grade, grade_all, parse_test_counts
from evals.models import Case, GraderSpec
from evals.snapshot import isolation_dir, locate_isolation_dir
from sandbox import registry


@pytest.fixture
def local_sandbox(monkeypatch):
    monkeypatch.setattr(registry, "resolve", lambda environment, settings=None: "local")


def _project(root: Path, *, failing: int = 0, passing: int = 1) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "test_suite.py").write_text(
        "\n".join([f"def test_ok_{i}():\n    assert True" for i in range(passing)]
                  + [f"def test_bad_{i}():\n    assert False, 'broken'" for i in range(failing)]) + "\n")
    return root


SPEC = GraderSpec(kind="tests_pass", params={}, weight=1.0)
CASE = Case(input="fix the code")


# ── reading a runner's summary ──────────────────────────────────────────────

def test_counts_are_read_from_pytest_jest_and_tap():
    assert parse_test_counts("== 3 passed, 1 failed in 0.1s ==") == {"passed": 3, "failed": 1}
    assert parse_test_counts("2 passed, 1 error in 1s") == {"passed": 2, "failed": 1}
    assert parse_test_counts("Tests:       1 failed, 4 passed, 5 total") == {"passed": 4, "failed": 1}
    assert parse_test_counts("# tests 3\n# pass 2\n# fail 1\n") == {"passed": 2, "failed": 1}
    assert parse_test_counts("Traceback ... ModuleNotFoundError") is None
    assert parse_test_counts("") is None


# ── the grader against a real folder ────────────────────────────────────────

def test_a_passing_suite_scores_one_and_passes(tmp_path, local_sandbox):
    work = _project(tmp_path / "case", passing=2)
    result = grade("", CASE, SPEC, work_dir=str(work))
    assert result.passed is True and result.score == 1.0, result.detail
    assert result.extra["passed"] == 2 and result.extra["failed"] == 0
    assert result.extra["provider"] == "local" and result.extra["exit_code"] == 0
    assert "2 passed" in result.detail


def test_a_partly_failing_suite_scores_the_share_and_fails(tmp_path, local_sandbox):
    work = _project(tmp_path / "case", passing=3, failing=1)
    result = grade("", CASE, SPEC, work_dir=str(work))
    assert result.passed is False
    assert result.score == 0.75
    assert "3 passed, 1 failed" in result.detail
    # the tail of the runner's output is kept for the result page
    assert "broken" in result.extra["output_tail"] or "test_bad" in result.extra["output_tail"]


def test_the_case_folder_is_left_as_the_agent_wrote_it(tmp_path, local_sandbox):
    work = _project(tmp_path / "case", passing=1)
    before = sorted(p.name for p in work.iterdir())
    grade("", CASE, SPEC, work_dir=str(work))
    assert sorted(p.name for p in work.iterdir()) == before, "pytest caches must land in the copy, not here"


def test_a_custom_command_and_exit_code_scoring(tmp_path, local_sandbox):
    work = _project(tmp_path / "case", passing=1)
    (work / "check.sh").write_text("test -f test_suite.py && echo fine\n")
    spec = GraderSpec(kind="tests_pass", params={"command": "sh check.sh"}, weight=1.0)
    result = grade("", CASE, spec, work_dir=str(work))
    assert result.passed is True and result.score == 1.0
    assert "scored by exit code" in result.detail
    spec = GraderSpec(kind="tests_pass", params={"command": "exit 3"}, weight=1.0)
    result = grade("", CASE, spec, work_dir=str(work))
    assert result.passed is False and result.score == 0.0 and result.extra["exit_code"] == 3


def test_no_working_directory_is_a_fail_with_a_reason(local_sandbox):
    result = grade("", CASE, SPEC, work_dir=None)
    assert result.passed is False and result.score == 0.0
    assert "no working directory" in result.detail


def test_an_unavailable_provider_is_a_fail_not_a_crash(tmp_path, monkeypatch):
    class Down:
        name = "docker"

        def is_available(self):
            return False, "daemon not running"

        def run(self, request):
            raise AssertionError("must not run")

    monkeypatch.setattr(registry, "resolve", lambda environment, settings=None: "docker")
    monkeypatch.setattr(registry, "get_provider", lambda name: Down())
    work = _project(tmp_path / "case")
    result = grade("", CASE, SPEC, work_dir=str(work))
    assert result.passed is False and "not available" in result.detail
    assert "CODE_RUNNER_FALLBACK=local" in result.detail


def test_grade_all_hands_the_work_dir_only_to_work_dir_graders(monkeypatch):
    seen = {}

    def fake_run(command, work_dir, timeout, image):
        seen["work_dir"] = work_dir
        from sandbox.base import SandboxResult
        return SandboxResult(exit_code=0, stdout="1 passed", provider="fake")

    monkeypatch.setattr(graders, "_run_tests_in_sandbox", fake_run)
    specs = [SPEC, GraderSpec(kind="substring", params={}, weight=1.0)]
    case = Case(input="fix the code", expected="done")
    per_grader, combined, passed = grade_all("all done", case, specs, work_dir="/case/dir")
    assert seen["work_dir"] == "/case/dir"
    assert per_grader["tests_pass"]["passed"] is True and per_grader["substring"]["passed"] is True
    assert passed is True and combined == 1.0


# ── finding the folder again, for a batch run graded later ──────────────────

def test_locate_isolation_dir_finds_what_isolation_dir_made(monkeypatch, tmp_path):
    from evals.snapshot import EVAL_ROOT_ENV
    monkeypatch.setenv(EVAL_ROOT_ENV, str(tmp_path / "er"))
    made = isolation_dir("ws9", "run1", "c1", attempt=2)
    assert locate_isolation_dir("ws9", "run1", "c1", attempt=2) == str(made)
    assert locate_isolation_dir("ws9", "run1", "c1", attempt=3) is None
    assert locate_isolation_dir(None, "run1", "c1") is None
