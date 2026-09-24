"""
Lab environment: a research group that tests one question by experiment.

Why a world and not a team chat: a claim is only as good as the numbers behind
it, and a model asked "is this true?" will happily answer without running
anything. Here a hypothesis can only be decided in state, experiments are real
programs executed in the ``run_code`` sandbox, their metrics land in a dataset
the environment builds itself, and the budget is a counter nobody can talk
their way past. Prose (the design, the analysis, the report) is the agents';
bookkeeping (who proposed what, which run produced which number, how many
experiments are left) is the environment's.

Determinism: everything here is pure Python except ``run_experiment``, which
calls :func:`tools.run_code.run_snippet`. That call is the one side effect and
its result is stored in state, so a replay with the same seed re-executes the
same code with the same stdin; a snapshot carries the stored result, so a
resume never runs an experiment twice.

Roles are prompts, not permissions: the lead decides, the theorist proposes
and writes formulas, the experimentalist designs and runs, the critic
critiques (and may ask for a repeat), the scribe writes the report. Any role
may take any action, the way a real lab works when somebody is out sick.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from playground.environments import register
from playground.environments.base import Environment
from playground.models import ActionResult

#: Hypothesis states. ``testing`` is set by the world when an experiment is
#: designed against a proposed hypothesis; the others by ``decide``.
PROPOSED, TESTING = "proposed", "testing"
CONFIRMED, REFUTED, NEEDS_REPEAT = "confirmed", "refuted", "needs_repeat"
VERDICTS = (CONFIRMED, REFUTED, NEEDS_REPEAT)

#: Experiment states.
DESIGNED, RUNNING, DONE, FAILED = "designed", "running", "done", "failed"

#: What a stopped lab says about why, read by the runner into SimRun.stop_reason.
STOP_DECIDED = "hypotheses_decided"
STOP_BUDGET = "budget_exhausted"

#: The five parts a lab scenario casts. Only prompts read these.
LAB_ROLES: Dict[str, str] = {
    "lead": "decides hypotheses (decide) once the evidence is in",
    "theorist": "proposes hypotheses and writes the formulas behind them",
    "experimentalist": "designs experiments as small Python programs and runs them",
    "critic": "critiques hypotheses and experiments, and may ask for a repeat",
    "scribe": "writes the report, section by section",
}

_TAIL = 1500          # chars of stdout/stderr kept per experiment
_CODE_LIMIT = 20000   # chars of experiment code accepted
_FRAME_CODE = 2000    # chars of code shown per experiment in a frame


def _tail(text: Any, limit: int = _TAIL) -> str:
    s = str(text or "")
    return s if len(s) <= limit else s[-limit:]


def _last_json_line(stdout: str) -> Optional[Dict[str, Any]]:
    """The program's metrics: its last non empty stdout line, as a JSON object."""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None
    return None


def _numeric(metrics: Any) -> Dict[str, float]:
    """Only the numeric entries of a metrics dict (booleans excluded)."""
    out: Dict[str, float] = {}
    for key, value in (metrics or {}).items() if isinstance(metrics, dict) else ():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out[str(key)] = float(value)
    return out


@register
class LabEnvironment(Environment):
    env_id = "lab"
    env_name = "Research lab"
    description = (
        "A research group tests one question. Hypotheses form a tree, "
        "experiments are Python programs run in the code sandbox against a "
        "budget, their metrics build a dataset, and the group writes a report. "
        "Ends when every root hypothesis is confirmed or refuted, or when the "
        "budget runs out."
    )
    renderer = "lab"

    PARAM_SCHEMA = [
        {"name": "question", "type": "string", "default": "",
         "description": "The research question the group works on."},
        {"name": "max_experiments", "type": "integer", "default": 12,
         "description": "Experiment budget: how many runs the group may make."},
        {"name": "experiment_timeout", "type": "integer", "default": 60,
         "description": "Seconds one experiment program may run."},
        {"name": "seed_experiments", "type": "boolean", "default": True,
         "description": "Hand every run a seed on stdin, so a repeat is reproducible."},
        {"name": "report_sections", "type": "list",
         "default": ["Abstract", "Method", "Results", "Discussion"],
         "description": "The report's sections, in order."},
    ]

    ACTIONS = [
        {"name": "propose_hypothesis",
         "description": "Propose a hypothesis. Give parent_id to refine an existing one.",
         "args": [{"name": "text", "type": "string"},
                  {"name": "parent_id", "type": "string"}]},
        {"name": "design_experiment",
         "description": (
             "Design an experiment for a hypothesis. code is a Python program: it "
             "reads {\"params\": ..., \"seed\": ...} as JSON from stdin and must "
             "print one JSON object of numeric metrics as its last stdout line. "
             "No network; keep it short enough to finish within the timeout."),
         "args": [{"name": "hypothesis_id", "type": "string"},
                  {"name": "design", "type": "string"},
                  {"name": "code", "type": "string"},
                  {"name": "params", "type": "object"}]},
        {"name": "run_experiment",
         "description": "Run a designed experiment in the sandbox. Costs one unit of budget.",
         "args": [{"name": "experiment_id", "type": "string"},
                  {"name": "seed", "type": "integer"}]},
        {"name": "analyze",
         "description": "Record your analysis of a finished experiment and the metrics you read from it.",
         "args": [{"name": "experiment_id", "type": "string"},
                  {"name": "analysis", "type": "string"},
                  {"name": "metrics", "type": "object"}]},
        {"name": "critique",
         "description": "Critique a hypothesis or an experiment, by id.",
         "args": [{"name": "target_id", "type": "string"},
                  {"name": "text", "type": "string"}]},
        {"name": "decide",
         "description": "Decide a hypothesis: verdict is confirmed, refuted or needs_repeat.",
         "args": [{"name": "hypothesis_id", "type": "string"},
                  {"name": "verdict", "type": "string"},
                  {"name": "note", "type": "string"}]},
        {"name": "write_up",
         "description": "Write (or rewrite) one section of the report.",
         "args": [{"name": "section", "type": "string"},
                  {"name": "text", "type": "string"}]},
        {"name": "add_formula",
         "description": "Add a formula to the report, as LaTeX.",
         "args": [{"name": "label", "type": "string"},
                  {"name": "latex", "type": "string"}]},
        {"name": "speak_to",
         "description": "Send a message to a colleague (delivered next tick).",
         "args": [{"name": "recipient", "type": "string"},
                  {"name": "text", "type": "string"}]},
        {"name": "observe", "description": "Do nothing this tick.", "args": []},
    ]

    OBJECTIVES = ["hypotheses_decided", "experiments_run", "report_complete"]
    IDLE_ACTIONS = ("observe",)
    # Checking a mean or a confidence bound by hand is what the calculator is
    # for; everything else a role needs goes through run_experiment.
    TOOL_ALLOWLIST = ("calculator",)

    def __init__(self, params: Optional[Dict[str, Any]] = None, seed: int = 42):
        super().__init__(params, seed=seed)
        self.hypotheses: Dict[str, Dict[str, Any]] = {}
        self.experiments: Dict[str, Dict[str, Any]] = {}
        self.datasets: Dict[str, Dict[str, Any]] = {}
        self.report: Dict[str, str] = {}
        self.formulas: List[Dict[str, str]] = []
        self.budget: Dict[str, int] = {
            "experiments_used": 0,
            "experiments_max": max(0, int(self.params.get("max_experiments") or 0)),
        }
        self.cast: Dict[str, str] = {}        # name -> role
        self.tally: Dict[str, Dict[str, int]] = {}
        self._h_seq = 0
        self._e_seq = 0

    # ── Setup ────────────────────────────────────────────────────────────────

    def register_agents(self, agents: List[str]) -> None:
        for name in agents:
            self.cast.setdefault(name, "")
            self.tally.setdefault(name, self._empty_tally())

    def register_cast(self, cast: List[Dict[str, str]]) -> None:
        for c in cast:
            name = str(c.get("name") or "")
            if not name:
                continue
            self.cast[name] = str(c.get("role") or "")
            self.tally.setdefault(name, self._empty_tally())

    @staticmethod
    def _empty_tally() -> Dict[str, int]:
        return {"proposed": 0, "designed": 0, "experiments_run": 0,
                "analyses": 0, "decisions": 0, "critiques": 0,
                "sections_written": 0, "formulas": 0}

    def _count(self, agent: str, key: str) -> None:
        self.tally.setdefault(agent, self._empty_tally())[key] += 1

    # ── Prompt text ──────────────────────────────────────────────────────────

    def world_brief(self, agent: str = "") -> str:
        question = str(self.params.get("question") or "").strip() or "(no question set)"
        roles = "\n".join(f"- {k}: {v}" for k, v in LAB_ROLES.items())
        mine = self.cast.get(agent, "")
        lines = [
            f"You work in a research lab on this question: {question}",
            "",
            "The lab's roles (any role may take any action, the role says what "
            "you are here for):",
            roles,
        ]
        if mine:
            lines += ["", f"You are cast as: {mine}."]
        lines += [
            "",
            "How an experiment works: design_experiment stores a Python program "
            "for a hypothesis. run_experiment executes it in a sandbox with no "
            "network and a timeout of "
            f"{int(self.params.get('experiment_timeout') or 60)} seconds. The "
            "program reads one JSON object from stdin, "
            '{"params": <the params you gave>, "seed": <an integer or null>}, '
            "and must print one JSON object of numeric metrics as its LAST line "
            "of stdout, for example {\"mean\": 0.498, \"within\": 0.97}. Seed "
            "your random generator from it so a repeat is reproducible. Split a "
            "long computation into several experiments instead of one slow one.",
            f"Budget: {self.budget['experiments_max']} runs in total. The run "
            "ends when every top level hypothesis is confirmed or refuted, or "
            "when the budget is spent.",
        ]
        return "\n".join(lines)

    # ── Observation ──────────────────────────────────────────────────────────

    def observe(self, agent: str) -> Dict[str, Any]:
        return {
            "tick": self.tick,
            "question": self.params.get("question") or "",
            "your_role": self.cast.get(agent, ""),
            "colleagues": sorted(n for n in self.cast if n != agent),
            "hypotheses": [
                {k: h[k] for k in ("id", "text", "parent", "author", "status",
                                   "decision_note")}
                for h in self.hypotheses.values()
            ],
            "experiments": [self._experiment_brief(e) for e in self.experiments.values()],
            "budget": dict(self.budget),
            "report_sections": {
                s: len(self.report.get(s, "")) for s in self._sections()
            },
            "formulas": [f["label"] for f in self.formulas],
            "messages": self.drain_inbox(agent),
        }

    @staticmethod
    def _experiment_brief(e: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": e["id"], "hypothesis_id": e["hypothesis_id"], "author": e["author"],
            "status": e["status"], "seed": e["seed"], "params": e["params"],
            "design": e["design"][:400], "result": e["result"],
            "stderr_tail": e["stderr_tail"][-300:], "analysis": e["analysis"][:400],
            "metrics": e["metrics"],
            "critiques": e["critiques"][-2:],
        }

    def _sections(self) -> List[str]:
        return [str(s) for s in (self.params.get("report_sections") or []) if str(s).strip()]

    # ── Actions ──────────────────────────────────────────────────────────────

    def apply(self, agent: str, action: str, args: Dict[str, Any]) -> ActionResult:
        args = dict(args or {})
        if agent not in self.cast:
            return ActionResult(agent, action, args, False, f"unknown agent {agent!r}")
        handler = {
            "propose_hypothesis": self._propose,
            "design_experiment": self._design,
            "run_experiment": self._run,
            "analyze": self._analyze,
            "critique": self._critique,
            "decide": self._decide,
            "write_up": self._write_up,
            "add_formula": self._add_formula,
            "speak_to": self._speak,
        }.get(action)
        if action == "observe":
            return ActionResult(agent, action, args, True, "observed")
        if handler is None:
            return ActionResult(agent, action, args, False, f"unknown action {action!r}")
        return handler(agent, args)

    def _propose(self, agent: str, args: Dict[str, Any]) -> ActionResult:
        text = str(args.get("text") or "").strip()
        if not text:
            return ActionResult(agent, "propose_hypothesis", args, False, "text is required")
        parent = str(args.get("parent_id") or "").strip() or None
        if parent and parent not in self.hypotheses:
            return ActionResult(agent, "propose_hypothesis", args, False,
                                f"no hypothesis {parent!r}")
        self._h_seq += 1
        hid = f"H{self._h_seq}"
        self.hypotheses[hid] = {
            "id": hid, "text": text[:2000], "parent": parent, "author": agent,
            "status": PROPOSED, "decided_by": "", "decision_note": "",
            "created_tick": self.tick,
        }
        self._count(agent, "proposed")
        self.log_event(f"{agent} proposed {hid}: {text[:80]}")
        return ActionResult(agent, "propose_hypothesis", args, True, f"proposed {hid}",
                            {"hypothesis_id": hid})

    def _design(self, agent: str, args: Dict[str, Any]) -> ActionResult:
        hid = str(args.get("hypothesis_id") or "").strip()
        if hid not in self.hypotheses:
            return ActionResult(agent, "design_experiment", args, False, f"no hypothesis {hid!r}")
        code = str(args.get("code") or "")
        if not code.strip():
            return ActionResult(agent, "design_experiment", args, False,
                                "code is required: a Python program that prints a JSON "
                                "object of metrics as its last line")
        if len(code) > _CODE_LIMIT:
            return ActionResult(agent, "design_experiment", args, False,
                                f"code is {len(code)} chars; the limit is {_CODE_LIMIT}")
        params = args.get("params")
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (TypeError, ValueError):
                params = None
        if params is not None and not isinstance(params, dict):
            return ActionResult(agent, "design_experiment", args, False,
                                "params must be a JSON object")
        self._e_seq += 1
        eid = f"E{self._e_seq}"
        self.experiments[eid] = {
            "id": eid, "hypothesis_id": hid, "author": agent,
            "design": str(args.get("design") or "")[:4000], "code": code,
            "params": dict(params or {}), "seed": None, "status": DESIGNED,
            "result": {}, "stdout_tail": "", "stderr_tail": "", "duration_ms": 0,
            "analysis": "", "metrics": {}, "critiques": [], "tick": self.tick,
            # Every run of this experiment, oldest first: a rerun with another
            # seed replaces the current result but not the record of the last.
            "runs": [],
        }
        if self.hypotheses[hid]["status"] in (PROPOSED, NEEDS_REPEAT):
            self.hypotheses[hid]["status"] = TESTING
        self._count(agent, "designed")
        self.log_event(f"{agent} designed {eid} for {hid}")
        return ActionResult(agent, "design_experiment", {**args, "code": f"({len(code)} chars)"},
                            True, f"designed {eid}", {"experiment_id": eid})

    def _run(self, agent: str, args: Dict[str, Any]) -> ActionResult:
        eid = str(args.get("experiment_id") or "").strip()
        exp = self.experiments.get(eid)
        if exp is None:
            return ActionResult(agent, "run_experiment", args, False, f"no experiment {eid!r}")
        if self._budget_left() <= 0:
            return ActionResult(
                agent, "run_experiment", args, False,
                f"the experiment budget is spent ({self.budget['experiments_used']} of "
                f"{self.budget['experiments_max']} runs used); decide with the evidence "
                "you have and write up")

        seed: Optional[int] = None
        if args.get("seed") not in (None, ""):
            try:
                seed = int(args.get("seed"))
            except (TypeError, ValueError):
                return ActionResult(agent, "run_experiment", args, False,
                                    "seed must be an integer")
        elif self.params.get("seed_experiments"):
            seed = self.seed + self.budget["experiments_used"]

        from tools.run_code import run_snippet
        exp["status"] = RUNNING
        exp["seed"] = seed
        stdin = json.dumps({"params": exp["params"], "seed": seed})
        timeout = max(1, int(self.params.get("experiment_timeout") or 60))
        try:
            out = run_snippet("python", exp["code"], timeout=timeout, stdin=stdin)
        except Exception as e:  # noqa: BLE001 - run_snippet never raises; a bug there must not end the world
            out = {"ok": False, "sandbox": "error", "error": f"{type(e).__name__}: {e}",
                   "stdout": "", "stderr": ""}

        stdout = str(out.get("stdout") or "")
        exp["stdout_tail"] = _tail(stdout)
        exp["stderr_tail"] = _tail(out.get("stderr"))
        exp["duration_ms"] = int(out.get("duration_ms") or 0)
        exp["tick"] = self.tick

        if out.get("sandbox") == "unavailable":
            # Nothing ran, so nothing is charged: the sandbox being down is
            # the host's problem, not the group's spending.
            exp["status"] = FAILED
            exp["result"] = {"error": str(out.get("error") or "sandbox unavailable")}
            self.log_event(f"{agent} could not run {eid}: the sandbox is unavailable")
            return ActionResult(agent, "run_experiment", args, False,
                                f"{eid} did not run: {exp['result']['error']}",
                                {"experiment_id": eid, "charged": False})

        self.budget["experiments_used"] += 1
        self._count(agent, "experiments_run")
        parsed = _last_json_line(stdout)
        exp["runs"].append({"seed": seed, "ok": bool(out.get("ok")), "tick": self.tick,
                            "result": parsed if parsed is not None else {"stdout": _tail(stdout, 300)}})
        exp["runs"] = exp["runs"][-20:]
        if out.get("ok") and parsed is not None:
            exp["status"] = DONE
            exp["result"] = parsed
            # The program's numbers are the metrics; an earlier analyze's
            # extra metrics stay on top of them.
            exp["metrics"] = {**exp["metrics"], **_numeric(parsed)}
            self._rebuild_dataset()
            self.log_event(f"{agent} ran {eid}: {json.dumps(parsed)[:80]}")
            return ActionResult(agent, "run_experiment", args, True,
                                f"{eid} finished: {json.dumps(parsed)[:300]}",
                                {"experiment_id": eid, "result": parsed, "seed": seed})
        exp["status"] = DONE if out.get("ok") else FAILED
        exp["result"] = {"stdout": _tail(stdout, 500)}
        if out.get("error"):
            exp["result"]["error"] = str(out.get("error"))
        self.log_event(f"{agent} ran {eid}: {exp['status']}")
        hint = ("" if out.get("ok") else f" ({out.get('error') or 'non zero exit'})")
        return ActionResult(
            agent, "run_experiment", args, bool(out.get("ok")),
            f"{eid} {exp['status']}{hint}; its last stdout line was not a JSON object"
            if out.get("ok") else f"{eid} failed{hint}: {exp['stderr_tail'][-300:]}",
            {"experiment_id": eid, "seed": seed})

    def _analyze(self, agent: str, args: Dict[str, Any]) -> ActionResult:
        eid = str(args.get("experiment_id") or "").strip()
        exp = self.experiments.get(eid)
        if exp is None:
            return ActionResult(agent, "analyze", args, False, f"no experiment {eid!r}")
        metrics = args.get("metrics")
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except (TypeError, ValueError):
                metrics = None
        if metrics is not None and not isinstance(metrics, dict):
            return ActionResult(agent, "analyze", args, False,
                                "metrics must be an object of numbers")
        exp["analysis"] = str(args.get("analysis") or "")[:4000]
        numeric = _numeric(metrics)
        if numeric:
            exp["metrics"] = {**exp["metrics"], **numeric}
        self._rebuild_dataset()
        self._count(agent, "analyses")
        self.log_event(f"{agent} analyzed {eid}")
        return ActionResult(agent, "analyze", args, True, f"analysis of {eid} recorded")

    def _critique(self, agent: str, args: Dict[str, Any]) -> ActionResult:
        target = str(args.get("target_id") or "").strip()
        text = str(args.get("text") or "").strip()
        if not text:
            return ActionResult(agent, "critique", args, False, "text is required")
        entry = {"author": agent, "text": text[:2000], "tick": self.tick}
        if target in self.experiments:
            self.experiments[target]["critiques"].append(entry)
            owner = self.experiments[target]["author"]
        elif target in self.hypotheses:
            self.hypotheses[target].setdefault("critiques", []).append(entry)
            owner = self.hypotheses[target]["author"]
        else:
            return ActionResult(agent, "critique", args, False, f"no hypothesis or experiment {target!r}")
        self._count(agent, "critiques")
        self.log_event(f"{agent} critiqued {target}")
        if owner and owner != agent:
            self.poke(owner, f"{agent} critiqued your {target}")
        return ActionResult(agent, "critique", args, True, f"critique of {target} recorded")

    def _decide(self, agent: str, args: Dict[str, Any]) -> ActionResult:
        hid = str(args.get("hypothesis_id") or "").strip()
        verdict = str(args.get("verdict") or "").strip().lower()
        if hid not in self.hypotheses:
            return ActionResult(agent, "decide", args, False, f"no hypothesis {hid!r}")
        if verdict not in VERDICTS:
            return ActionResult(agent, "decide", args, False,
                                f"verdict must be one of {', '.join(VERDICTS)}")
        h = self.hypotheses[hid]
        h["status"] = verdict
        h["decided_by"] = agent
        h["decision_note"] = str(args.get("note") or "")[:2000]
        self._count(agent, "decisions")
        self.log_event(f"{agent} decided {hid}: {verdict}")
        for name in self.cast:
            if name != agent:
                self.poke(name, f"{agent} decided {hid}: {verdict}")
        return ActionResult(agent, "decide", args, True, f"{hid} is now {verdict}")

    def _write_up(self, agent: str, args: Dict[str, Any]) -> ActionResult:
        section = str(args.get("section") or "").strip()
        text = str(args.get("text") or "").strip()
        if not section or not text:
            return ActionResult(agent, "write_up", args, False, "section and text are required")
        # Match a declared section case insensitively so "results" fills "Results".
        for known in self._sections():
            if known.lower() == section.lower():
                section = known
                break
        self.report[section] = text[:12000]
        self._count(agent, "sections_written")
        self.log_event(f"{agent} wrote the {section} section")
        return ActionResult(agent, "write_up", {"section": section, "text": text[:200]},
                            True, f"section {section} written")

    def _add_formula(self, agent: str, args: Dict[str, Any]) -> ActionResult:
        label = str(args.get("label") or "").strip()
        latex = str(args.get("latex") or "").strip()
        if not latex:
            return ActionResult(agent, "add_formula", args, False, "latex is required")
        self.formulas.append({"label": label or f"({len(self.formulas) + 1})",
                              "latex": latex[:2000], "author": agent})
        self._count(agent, "formulas")
        self.log_event(f"{agent} added a formula {label}")
        return ActionResult(agent, "add_formula", args, True, "formula added")

    def _speak(self, agent: str, args: Dict[str, Any]) -> ActionResult:
        target = str(args.get("recipient") or args.get("agent") or "").strip()
        if target not in self.cast:
            return ActionResult(agent, "speak_to", args, False, f"no such colleague {target!r}")
        return self.queue_message(agent, target, str(args.get("text") or ""))

    # ── Derived state ────────────────────────────────────────────────────────

    def _budget_left(self) -> int:
        return self.budget["experiments_max"] - self.budget["experiments_used"]

    def _rebuild_dataset(self) -> None:
        """One row per finished experiment: ids, seed and every numeric metric."""
        finished = [e for e in self.experiments.values() if e["status"] == DONE]
        metric_names: List[str] = []
        for e in finished:
            for key in e["metrics"]:
                if key not in metric_names:
                    metric_names.append(key)
        columns = ["experiment", "hypothesis", "seed"] + metric_names
        rows = [
            [e["id"], e["hypothesis_id"], e["seed"]]
            + [e["metrics"].get(m) for m in metric_names]
            for e in finished
        ]
        self.datasets = {"results": {"columns": columns, "rows": rows}} if finished else {}

    def stop_reason(self) -> str:
        roots = [h for h in self.hypotheses.values() if not h["parent"]]
        if roots and all(h["status"] in (CONFIRMED, REFUTED) for h in roots):
            return STOP_DECIDED
        running = any(e["status"] == RUNNING for e in self.experiments.values())
        if self.budget["experiments_max"] and self._budget_left() <= 0 and not running:
            return STOP_BUDGET
        return ""

    def is_done(self) -> bool:
        return bool(self.stop_reason())

    @property
    def ending(self) -> str:
        """What the runner prints as the detail of a terminal stop."""
        reason = self.stop_reason()
        if reason == STOP_DECIDED:
            return "every top level hypothesis is confirmed or refuted"
        if reason == STOP_BUDGET:
            return "the experiment budget is spent"
        return ""

    def _tree(self) -> List[Dict[str, Any]]:
        by_parent: Dict[Optional[str], List[str]] = {}
        for hid, h in self.hypotheses.items():
            by_parent.setdefault(h["parent"], []).append(hid)

        def node(hid: str, depth: int) -> Dict[str, Any]:
            h = self.hypotheses[hid]
            return {
                **{k: v for k, v in h.items()},
                "depth": depth,
                "experiments": [e["id"] for e in self.experiments.values()
                                if e["hypothesis_id"] == hid],
                "children": [node(c, depth + 1) for c in by_parent.get(hid, [])],
            }
        return [node(hid, 0) for hid in by_parent.get(None, [])]

    # ── Frame / scoring ──────────────────────────────────────────────────────

    def frame(self) -> Dict[str, Any]:
        sections = self._sections()
        ordered = [s for s in sections if s in self.report] + \
                  [s for s in self.report if s not in sections]
        return {
            "renderer": self.renderer,
            "tick": self.tick,
            "question": self.params.get("question") or "",
            "hypotheses": self._tree(),
            "experiments": [
                {**{k: v for k, v in e.items() if k != "code"},
                 "code": e["code"][:_FRAME_CODE]}
                for e in self.experiments.values()
            ],
            "datasets": {k: {"columns": list(v["columns"]), "rows": [list(r) for r in v["rows"]]}
                         for k, v in self.datasets.items()},
            "report": [{"section": s, "text": self.report[s]} for s in ordered],
            "report_sections": sections,
            "formulas": [dict(f) for f in self.formulas],
            "budget": dict(self.budget),
            "cast": [{"name": n, "role": r} for n, r in sorted(self.cast.items())],
            "stop_reason": self.stop_reason(),
        }

    def objectives(self) -> Dict[str, float]:
        sections = self._sections()
        filled = sum(1 for s in sections if self.report.get(s, "").strip())
        return {
            "hypotheses_decided": sum(1 for h in self.hypotheses.values()
                                      if h["status"] in (CONFIRMED, REFUTED)),
            "experiments_run": self.budget["experiments_used"],
            "report_complete": round(filled / len(sections), 3) if sections else 0.0,
        }

    def score(self) -> Dict[str, Any]:
        shared = self.objectives()
        return {name: {**tally, **shared} for name, tally in sorted(self.tally.items())}

    def state(self) -> Dict[str, Any]:
        return {**self.frame(), "objectives": self.objectives(),
                "experiments_full": {k: dict(v) for k, v in self.experiments.items()}}

    # ── Views ────────────────────────────────────────────────────────────────

    def views(self) -> List[Dict[str, Any]]:
        """What the lab publishes as views while it runs.

        The table and chart appear with the first finished experiment, the
        formulas with the first formula, the report with the first section.
        Keys are stable so the runner updates one view per key in place.
        """
        out: List[Dict[str, Any]] = []
        question = str(self.params.get("question") or "").strip()
        data = self.datasets.get("results")
        if data and data["rows"]:
            out.append({
                "key": "results_table", "kind": "table", "title": "Lab results",
                "spec": {"columns": list(data["columns"]),
                         "rows": [list(r) for r in data["rows"]]},
            })
            metric = next((c for c in data["columns"][3:]
                           if any(isinstance(r[data["columns"].index(c)], (int, float))
                                  for r in data["rows"])), None)
            if metric:
                idx = data["columns"].index(metric)
                values = [{"experiment": r[0], "hypothesis": r[1], "value": r[idx]}
                          for r in data["rows"] if isinstance(r[idx], (int, float))]
                out.append({
                    "key": "results_chart", "kind": "chart", "title": f"Lab results: {metric}",
                    "spec": {"vega_lite": {
                        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
                        "description": f"{metric} per experiment",
                        "data": {"values": values},
                        "mark": {"type": "bar", "tooltip": True},
                        "encoding": {
                            "x": {"field": "experiment", "type": "nominal", "sort": None,
                                  "title": "Experiment"},
                            "y": {"field": "value", "type": "quantitative", "title": metric},
                            "color": {"field": "hypothesis", "type": "nominal",
                                      "title": "Hypothesis"},
                        },
                    }},
                })
        if self.formulas:
            rows = " \\\\ ".join(
                f"\\text{{{_latex_text(f['label'])}}}:\\quad {f['latex']}"
                for f in self.formulas
            )
            out.append({"key": "formulas", "kind": "latex", "title": "Lab formulas",
                        "spec": {"latex": f"\\begin{{gathered}} {rows} \\end{{gathered}}"}})
        if self.report:
            out.append({"key": "report", "kind": "document", "title": "Lab report",
                        "spec": {"title": question[:120] or "Lab report",
                                 "markdown": self.report_markdown(), "css": ""}})
        return out

    def report_markdown(self) -> str:
        """The report as one markdown document, sections in declared order."""
        question = str(self.params.get("question") or "").strip()
        parts = [f"# {question or 'Lab report'}"]
        sections = self._sections()
        for s in [s for s in sections if s in self.report] + \
                 [s for s in self.report if s not in sections]:
            parts.append(f"## {s}\n\n{self.report[s]}")
        if self.formulas:
            parts.append("## Formulas\n\n" + "\n\n".join(
                f"**{f['label']}**\n\n$$\n{f['latex']}\n$$" for f in self.formulas))
        decided = [h for h in self.hypotheses.values() if h["status"] != PROPOSED]
        if decided:
            parts.append("## Hypotheses\n\n" + "\n".join(
                f"- **{h['id']}** ({h['status']}): {h['text']}"
                + (f" *{h['decision_note']}*" if h["decision_note"] else "")
                for h in decided))
        return "\n\n".join(parts)


def _latex_text(text: str) -> str:
    """A label made safe for ``\\text{}``: drop the characters LaTeX treats specially."""
    return re.sub(r"[\\{}$&#^_%~]", "", str(text or ""))[:60]


__all__ = ["LabEnvironment", "LAB_ROLES", "STOP_DECIDED", "STOP_BUDGET"]
