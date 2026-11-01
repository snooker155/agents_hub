"""Record checks by route for evals and the playground
(common/record_kinds/evals.py): the record's own workspace decides."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from common import record_access
from common.record_kinds import evals as kinds


def _role_in(ws):
    return {"alpha": "editor", "default": "viewer"}.get(ws)


def _check(method, template, **params):
    return record_access.check_route(method=method, template=template, params=params,
                                     named_workspace="alpha", role_in=_role_in)


@pytest.fixture
def stores(monkeypatch):
    from evals import store as evals_store
    from managers import run_manager
    from common import entity_runs
    from playground import store as play

    sets = {"s-beta": NS(workspace="beta"), "s-alpha": NS(workspace="alpha"), "s-none": NS(workspace=None)}
    runs = {
        "r-beta": NS(workspace="beta", eval_set_id="s-alpha"),
        "r-alpha": NS(workspace="alpha", eval_set_id="s-beta"),
        # an older run: no workspace of its own, its set's
        "r-old-beta": NS(workspace=None, eval_set_id="s-beta"),
        "r-old-none": NS(workspace="", eval_set_id="s-none"),
        "r-orphan": NS(workspace=None, eval_set_id="gone"),
    }
    suggestions = {"p-beta": NS(workspace="beta"), "p-alpha": NS(workspace="alpha"), "p-none": NS(workspace=None)}
    monkeypatch.setattr(evals_store, "get_eval_set", sets.get)
    monkeypatch.setattr(evals_store, "get_eval_run", runs.get)
    monkeypatch.setattr(evals_store, "get_prompt_suggestion", suggestions.get)

    agent_runs = {"a-beta": {"workspace": "beta"}, "a-alpha": {"workspace": "alpha"}, "a-none": {"workspace": None}}
    entity = {"f-beta": {"workspace": "beta"}, "f-alpha": {"workspace": "alpha"}}
    monkeypatch.setattr(run_manager, "get_run_by_id", agent_runs.get)
    monkeypatch.setattr(entity_runs, "get", entity.get)

    sims = {
        "m-beta": NS(workspace="beta", scenario_id="c-alpha"),
        "m-alpha": NS(workspace="alpha", scenario_id="c-beta"),
        "m-old-beta": NS(workspace=None, scenario_id="c-beta"),
        "m-old-none": NS(workspace=None, scenario_id="c-none"),
    }
    scenarios = {"c-beta": NS(workspace="beta"), "c-alpha": NS(workspace="alpha"), "c-none": NS(workspace=None)}
    worlds = {"w-beta": NS(workspace="beta"), "w-alpha": NS(workspace="alpha"), "w-none": NS(workspace="")}
    monkeypatch.setattr(play, "get_sim_run", sims.get)
    monkeypatch.setattr(play, "get_scenario", scenarios.get)
    monkeypatch.setattr(play, "get_world", worlds.get)


# (read template, write template, id of beta, id of alpha, id with no workspace, param)
CASES = [
    ("/api/evals/{eval_set_id}", "/api/evals/{eval_set_id}", "s-beta", "s-alpha", "s-none", "eval_set_id"),
    ("/api/eval-runs/{eval_run_id}", "/api/eval-runs/{eval_run_id}/cancel", "r-beta", "r-alpha", "r-old-none", "eval_run_id"),
    ("/api/prompt-suggestions/{suggestion_id}/dismiss", "/api/prompt-suggestions/{suggestion_id}/apply",
     "p-beta", "p-alpha", "p-none", "suggestion_id"),
    ("/api/playground/runs/{sim_run_id}", "/api/playground/runs/{sim_run_id}/stop", "m-beta", "m-alpha", "m-old-none", "sim_run_id"),
    ("/api/playground/scenarios/{scenario_id}", "/api/playground/scenarios/{scenario_id}", "c-beta", "c-alpha", "c-none", "scenario_id"),
    ("/api/playground/worlds/{world_id}", "/api/playground/worlds/{world_id}", "w-beta", "w-alpha", "w-none", "world_id"),
    ("/api/evals/for-run/{run_id}", "/api/evals/for-run/{run_id}", "a-beta", "a-alpha", "a-none", "run_id"),
]


@pytest.mark.parametrize("read,write,beta,alpha,none,param", CASES)
def test_the_records_own_workspace_decides(stores, read, write, beta, alpha, none, param):
    for method, template in (("GET", read), ("DELETE", write)):
        refused = _check(method, template, **{param: beta})
        assert isinstance(refused, str) and refused
        assert _check(method, template, **{param: alpha}) is None
    # No workspace: default's, where this caller is a viewer
    assert _check("GET", read, **{param: none}) is None
    refused = _check("DELETE", write, **{param: none})
    assert isinstance(refused, str) and refused


@pytest.mark.parametrize("read,write,beta,alpha,none,param", CASES)
def test_a_missing_record_passes(stores, read, write, beta, alpha, none, param):
    assert _check("GET", read, **{param: "nope"}) is None
    assert _check("DELETE", write, **{param: "nope"}) is None


def test_eval_run_without_a_workspace_follows_its_set(stores):
    assert _check("GET", "/api/eval-runs/{eval_run_id}", eval_run_id="r-old-beta")
    assert _check("GET", "/api/eval-runs/{eval_run_id}", eval_run_id="r-orphan") is None
    # the first run of a diff
    assert _check("GET", "/api/evals/runs/{run_a_id}/diff/{run_b_id}", run_a_id="r-beta", run_b_id="r-alpha")
    assert _check("GET", "/api/evals/runs/{run_a_id}/diff/{run_b_id}", run_a_id="r-alpha", run_b_id="r-beta") is None


def test_a_sim_run_without_a_workspace_follows_its_scenario(stores):
    assert _check("GET", "/api/playground/runs/{sim_run_id}/ticks", sim_run_id="m-old-beta")
    assert _check("GET", "/api/playground/runs/{sim_run_id}/ticks", sim_run_id="m-old-none") is None


def test_for_run_names_an_entity_run_too(stores):
    assert _check("GET", "/api/evals/for-run/{run_id}", run_id="f-beta")
    assert _check("GET", "/api/evals/for-run/{run_id}", run_id="f-alpha") is None


@pytest.mark.parametrize("template,param,beta,alpha", [
    ("/api/evals/{eval_set_id}/estimate", "eval_set_id", "s-beta", "s-alpha"),
    ("/api/playground/scenarios/{scenario_id}/estimate", "scenario_id", "c-beta", "c-alpha"),
])
def test_estimates_are_reads(stores, template, param, beta, alpha):
    assert template in kinds.READ_ROUTES
    # a viewer in the record's workspace is enough, a stranger is still refused
    viewer = lambda ws: "viewer" if ws == "beta" else None  # noqa: E731
    assert record_access.check_route(method="POST", template=template, params={param: beta},
                                     named_workspace="alpha", role_in=viewer) is None
    assert _check("POST", template, **{param: beta})
    assert _check("POST", template, **{param: alpha}) is None


def test_every_route_of_these_prefixes_is_covered():
    from common import record_kinds
    for key in kinds.ROUTES:
        assert record_kinds.covered(key)
    assert not kinds.EXEMPT
