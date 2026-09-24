"""
Ready made scenarios a person (or the Scenario Creator) starts from.

Why templates and not only generation: a research lab needs five roles whose
goals fit together (somebody proposes, somebody tests, somebody doubts,
somebody decides, somebody writes), a question an experiment can settle
cheaply, and a budget that ends the run. Getting all of that right from a
one line prompt is luck; a template gets it right once and every copy starts
runnable. The agent slots are left empty on purpose: the caller fills them
with one default agent, or hands the scenario to a team
(``Scenario.team_id``), see :func:`scenario_from_template`.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

#: id -> a complete scenario payload, in ``Scenario.from_dict`` shape.
TEMPLATES: Dict[str, Dict[str, Any]] = {
    "lab": {
        "name": "Research lab",
        "description": (
            "A research group of five tests a cheap, checkable claim by running "
            "real experiments in the code sandbox, decides it, and writes a report."
        ),
        "narrative": (
            "A small lab with a modest budget and one question on the whiteboard. "
            "Nobody gets to call a claim true until a program has produced the "
            "numbers, somebody has tried to knock them down, and the lead has "
            "signed off. The report is what leaves the building."
        ),
        "environment": "lab",
        "env_params": {
            "question": (
                "For n up to 10000, the sample mean of n uniform(0,1) draws is "
                "within 0.01 of 0.5 at least 95% of the time."
            ),
            "max_experiments": 12,
            "experiment_timeout": 60,
            "seed_experiments": True,
            "report_sections": ["Abstract", "Method", "Results", "Discussion"],
        },
        "roles": [
            {
                "agent_id": "", "name": "Lead", "role": "lead",
                "goal": (
                    "Steer the group to a verdict on every top level hypothesis. "
                    "Decide a hypothesis only when an experiment backs it, and "
                    "ask for a repeat when the critic has a point."
                ),
                "private_knowledge": (
                    "The budget is small. A claim about 'n up to 10000' is really "
                    "several claims, one per n; the smallest n is where it is "
                    "most likely to fail."
                ),
                "starts": True,
            },
            {
                "agent_id": "", "name": "Theorist", "role": "theorist",
                "goal": (
                    "Turn the question into precise hypotheses, refine them into "
                    "sub hypotheses when the data asks for it, and add the "
                    "formulas that predict the outcome."
                ),
                "private_knowledge": (
                    "The standard deviation of the mean of n uniform draws is "
                    "1/sqrt(12 n). A 95% band of 0.01 needs roughly "
                    "1.96/sqrt(12 n) <= 0.01."
                ),
            },
            {
                "agent_id": "", "name": "Experimentalist", "role": "experimentalist",
                "goal": (
                    "Design small, fast Python experiments for each hypothesis, "
                    "run them, and analyze what they print. Seed everything."
                ),
                "private_knowledge": (
                    "The sandbox has no network and a timeout, so estimate a "
                    "coverage rate from a few thousand trials per n, not millions. "
                    "Only the standard library is guaranteed."
                ),
            },
            {
                "agent_id": "", "name": "Critic", "role": "critic",
                "goal": (
                    "Find what is wrong with each hypothesis and experiment: too "
                    "few trials, a single seed, a range of n that skips the hard "
                    "cases. Ask for a repeat when a result could be luck."
                ),
                "private_knowledge": (
                    "A single run with one seed proves little; two seeds that "
                    "agree are worth more than one big run."
                ),
            },
            {
                "agent_id": "", "name": "Scribe", "role": "scribe",
                "goal": (
                    "Write the report as the work happens: Abstract, Method, "
                    "Results with the numbers, Discussion with what is still open."
                ),
                "private_knowledge": (
                    "A report that states the verdict without the numbers behind "
                    "it will be sent back."
                ),
            },
        ],
        "mode": "personas",
        "activation": "triggered",
        "max_ticks": 30,
        "documents": [],
    },
}


def list_templates() -> List[Dict[str, Any]]:
    """The catalogue: id, name, description and environment of each template."""
    return [
        {"id": tid, "name": t["name"], "description": t["description"],
         "environment": t["environment"], "roles": len(t.get("roles") or [])}
        for tid, t in TEMPLATES.items()
    ]


def scenario_from_template(template: str, *, workspace: Optional[str] = None,
                           agent_id: Optional[str] = None,
                           team_id: Optional[str] = None) -> Dict[str, Any]:
    """A scenario payload built from a template, ready for ``Scenario.from_dict``.

    ``agent_id`` fills every role's empty agent slot. ``team_id`` instead
    hands the cast to that team: the template's roles are dropped and the
    team's members play the scenario at run time
    (``playground.runner.roles_from_team``). Raises ``KeyError`` for an
    unknown template and ``ValueError`` when neither an agent nor a team is
    given.
    """
    if template not in TEMPLATES:
        raise KeyError(template)
    agent_id = (agent_id or "").strip() or None
    team_id = (team_id or "").strip() or None
    if not agent_id and not team_id:
        raise ValueError("give an agent_id for every role, or a team_id to cast the scenario")
    payload = copy.deepcopy(TEMPLATES[template])
    payload["workspace"] = workspace
    if team_id:
        payload["team_id"] = team_id
        payload["roles"] = []
    else:
        for role in payload["roles"]:
            role["agent_id"] = role.get("agent_id") or agent_id
    return payload


__all__ = ["TEMPLATES", "list_templates", "scenario_from_template"]
