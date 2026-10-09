"""
Batch eval runs: the cells go through the provider batch APIs at half price.

A live eval run (evals/runner.py ``run_eval``) calls the target once per cell
and waits. A batch run (``mode="batch"``) sends what it can to OpenAI's Batch
API or Anthropic's Message Batches API (providers/batch_api.py) and finishes
when the provider does, usually within the hour and at most 24 hours later,
billed at 50%. Two phases, one provider batch per phase and endpoint:

1. **Target.** An agent cell is sent as the agent's first model call: the
   same system prompt, tools and parameters its live run would send (built
   with the agent's own chat model, so the body is byte for byte what the
   live call carries). When the reply is a final answer, it is recorded as the
   cell's run (channel ``eval``, ``price_factor`` 0.5 so costs show the batch
   price). When the model asks for a tool instead, one answer is not enough:
   that cell runs live from the start, graded like any live cell, and its
   trajectory says why.
2. **Judge.** The ``llm_judge`` and ``rubric`` calls of the cells that came
   back from phase 1 go out as a second batch when their judge model has a
   batch API; the other graders score straight away.

What runs live instead, right away, with the reason on the cell's trajectory:
flow, team, loop and scenario targets (a container is many calls, not one);
agents on a provider without a batch API (Google, Ollama, LM Studio, any
OpenAI-compatible server other than OpenAI itself); agents whose loop changes
the first call or the answer (tool search, a structured output schema) or
that a guardrail checks (the checks apply exactly only live); and an agent
that cannot be built. A request the provider rejects or lets expire runs
live as well; a cancelled one is recorded as stopped.

The run's status is ``batch_pending`` until every batch it submitted has been
processed; :func:`poll_pending` does that from the plan scheduler's tick
(plans/scheduler.py, which only the ``scheduler`` lease holder runs), every
``AGENTS_HUB_EVAL_BATCH_POLL_SECONDS`` (60). An ended batch is claimed
(``submitted`` → ``processing``) before it is processed, in a thread of its
own so a slow live fallback never holds the scheduler; each cell is marked
done in the row as it is recorded, so a process that dies half way picks up
where it stopped (a ``processing`` row not touched for 30 minutes is
reclaimed). The provider key is never stored: the row keeps provider,
endpoint, model and agent, and the key is resolved again when polled.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from common import db
from evals import store
from evals.models import EVAL_CHANNEL, Case, EvalResult, EvalRun, EvalSet, RunConfig, utc_iso

log = logging.getLogger(__name__)

MODE_LIVE = "live"
MODE_BATCH = "batch"
STATUS_PENDING = "batch_pending"

PHASE_TARGET = "target"
PHASE_JUDGE = "judge"

POLL_ENV = "AGENTS_HUB_EVAL_BATCH_POLL_SECONDS"
DEFAULT_POLL_SECONDS = 60
#: A row left in ``processing`` this long belonged to a process that died.
RECLAIM_AFTER = timedelta(minutes=30)

#: Loop extensions that leave the first model call and the answer as they
#: are: steering only injects messages that arrive during a run, compaction
#: only folds a long loop, fallback only acts on an error (and an errored
#: batch request runs live, where it applies).
_BATCH_SAFE_EXTENSIONS = {"SteeringExtension", "CompactionExtension", "FallbackExtension"}

_JUDGE_KINDS = ("llm_judge", "rubric")

#: Per-process cache of resolved endpoints by batch row id, so a poll does not
#: rebuild an agent to find its key every minute.
_TARGETS: Dict[str, Any] = {}
_LAST_POLL = 0.0
_POLL_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _poll_seconds() -> float:
    try:
        return max(5.0, float(os.environ.get(POLL_ENV, DEFAULT_POLL_SECONDS)))
    except (TypeError, ValueError):
        return float(DEFAULT_POLL_SECONDS)


def _custom_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


# ── the eval_batches table ───────────────────────────────────────────────────

def _row(row: Any) -> Dict[str, Any]:
    return {
        "batch_row_id": row["batch_row_id"], "eval_run_id": row["eval_run_id"],
        "phase": row["phase"], "provider": row["provider"], "base_url": row["base_url"] or "",
        "model": row["model"] or "", "agent_id": row["agent_id"] or "",
        "provider_batch_id": row["provider_batch_id"] or "", "status": row["status"],
        "items": db.loads(row["items"], {}) or {}, "counts": db.loads(row["counts"], {}) or {},
        "error": row["error"], "submitted_at": row["submitted_at"],
        "checked_at": row["checked_at"], "finished_at": row["finished_at"],
    }


def _insert_row(rec: Dict[str, Any]) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO eval_batches (batch_row_id, eval_run_id, phase, provider, base_url, model, "
            "agent_id, provider_batch_id, status, items, counts, error, submitted_at, checked_at, "
            "finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rec["batch_row_id"], rec["eval_run_id"], rec["phase"], rec["provider"],
             rec.get("base_url") or "", rec.get("model") or "", rec.get("agent_id") or "",
             rec.get("provider_batch_id") or "", rec["status"], db.dumps(rec["items"]),
             db.dumps(rec.get("counts") or {}), rec.get("error"), rec["submitted_at"],
             None, None))


def _update_row(batch_row_id: str, **fields: Any) -> None:
    sets, args = [], []
    for key, value in fields.items():
        if key in ("items", "counts"):
            value = db.dumps(value)
        sets.append(f"{key} = ?")
        args.append(value)
    args.append(batch_row_id)
    with db.transaction() as conn:
        conn.execute(f"UPDATE eval_batches SET {', '.join(sets)} WHERE batch_row_id = ?", tuple(args))


def get_row(batch_row_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute("SELECT * FROM eval_batches WHERE batch_row_id = ?",
                                (batch_row_id,)).fetchone()
    return _row(row) if row else None


def rows_for_run(eval_run_id: str) -> List[Dict[str, Any]]:
    rows = db.get_conn().execute(
        "SELECT * FROM eval_batches WHERE eval_run_id = ? ORDER BY submitted_at",
        (eval_run_id,)).fetchall()
    return [_row(r) for r in rows]


def progress(eval_run_id: str) -> Dict[str, Any]:
    """What the UI shows for a batch run: each provider batch with its phase,
    state and counts (never the items or the provider's own record)."""
    out = []
    for r in rows_for_run(eval_run_id):
        items = r["items"]
        out.append({
            "batch_row_id": r["batch_row_id"], "phase": r["phase"], "provider": r["provider"],
            "model": r["model"], "provider_batch_id": r["provider_batch_id"],
            "status": r["status"], "counts": r["counts"], "error": r["error"],
            "requests": len(items), "recorded": sum(1 for m in items.values() if m.get("done")),
            "submitted_at": r["submitted_at"], "checked_at": r["checked_at"],
            "finished_at": r["finished_at"],
        })
    return {"batches": out,
            "pending": sum(1 for b in out if b["status"] in ("submitted", "processing"))}


# ── endpoints and keys ───────────────────────────────────────────────────────

def _target_for_model(provider: str, model: str):
    from agents.agent_utils import build_chat_model
    from providers.batch_api import chat_model_target
    llm = build_chat_model(provider=provider or None, model=model or None, temperature=0.0)
    return llm, chat_model_target(llm)


def _resolve_target(row: Dict[str, Any]):
    """The endpoint and key of a row's batch: from this process's cache, else
    rebuilt the way the requests were built (the agent for a target batch, so
    a per agent or per workspace key is found again; the judge model for a
    judge batch)."""
    cached = _TARGETS.get(row["batch_row_id"])
    if cached is not None:
        return cached
    target = None
    if row["phase"] == PHASE_TARGET and row["agent_id"]:
        try:
            from agents.agent_factory import create_agent
            from agents.agent_utils import build_chat_model
            from providers.batch_api import chat_model_target
            run = store.get_eval_run(row["eval_run_id"])
            agent = create_agent(row["agent_id"], run.workspace if run else None,
                                 provider=row["provider"], model=row["model"])
            llm = build_chat_model(provider=agent.provider, model=agent.model,
                                   api_key=agent.api_key, base_url=agent.base_url)
            target = chat_model_target(llm)
        except Exception:  # noqa: BLE001 - fall through to the provider default below
            log.warning("eval batch %s: could not rebuild agent %s", row["batch_row_id"],
                        row["agent_id"], exc_info=True)
    if target is None:
        _llm, target = _target_for_model(row["provider"], row["model"])
    if target is None:
        raise RuntimeError(f"no batch endpoint for {row['provider']}/{row['model']}")
    _TARGETS[row["batch_row_id"]] = target
    return target


# ── preparing a cell ─────────────────────────────────────────────────────────

def prepare_agent_cell(case: Case, cfg: RunConfig, workspace: Optional[str], *,
                       prompt: str, work_dir: Optional[str]) -> Tuple[Optional[Dict[str, Any]], str]:
    """``(request, "")`` for an agent cell that can go to a batch, or
    ``(None, reason)`` when it has to run live. ``request`` carries the
    endpoint (``target``), the provider request ``body`` and what the result
    needs to be recorded later."""
    if cfg.target_kind != "agent":
        return None, f"a {cfg.target_kind} target runs many calls, not one"
    overrides: Dict[str, Any] = {}
    if cfg.provider:
        overrides["provider"] = cfg.provider
    if cfg.model:
        overrides["model"] = cfg.model
    try:
        from agents.agent_factory import create_agent
        agent = create_agent(cfg.target_id, work_dir or workspace, **overrides)
        agent.executor  # noqa: B018 - builds the model and loads the loop extensions
    except Exception as e:  # noqa: BLE001 - the live path reports the build error on the cell
        return None, f"could not build the agent ({type(e).__name__})"
    llm = getattr(agent, "_llm", None)
    if llm is None:
        return None, "this kind of agent has no model call to batch"
    from providers.batch_api import chat_model_target, request_body
    target = chat_model_target(llm)
    if target is None:
        return None, f"provider {agent.effective_provider(llm) or '?'} has no batch API"
    extensions = {type(e).__name__ for e in getattr(agent, "_loop_extensions", [])}
    blocking = sorted(extensions - _BATCH_SAFE_EXTENSIONS)
    if blocking:
        return None, f"the agent's loop uses {', '.join(blocking)}"
    try:
        from agents.agent_loop import new_state
        from guardrails.runtime import has_guardrails
        if has_guardrails(agent, new_state(agent, run_id=None, workspace=workspace)):
            return None, "guardrails check this agent"
    except Exception:  # noqa: BLE001 - unknown means guarded: run live
        return None, "guardrails could not be checked"
    from langchain_core.messages import HumanMessage
    try:
        body = request_body(llm, [agent._system_message(llm), HumanMessage(content=prompt)],
                            list(getattr(agent, "_tools", []) or []))
    except Exception as e:  # noqa: BLE001 - a body that cannot be built is a live cell
        return None, f"could not build the request ({type(e).__name__})"
    return {
        "target": target, "body": body,
        "agent_id": cfg.target_id,
        "provider": target.provider, "model": target.model,
        "system_prompt": getattr(agent, "system_prompt", "") or "",
    }, ""


# ── starting a run ───────────────────────────────────────────────────────────

def _group_key(target: Any, agent_id: str) -> tuple:
    # One batch per endpoint, key and agent: the agent is what re-resolves the
    # key after a restart, so requests of two agents never share a batch.
    return (*target.key(), agent_id)


def start_batch_run(evalset: EvalSet, configs: List[RunConfig], workspace: Optional[str], *,
                    cost_ceiling: Optional[float] = None,
                    on_progress: Optional[Any] = None) -> EvalRun:
    """Prepare every cell, submit what can be batched, run the rest live.

    Returns the run: ``batch_pending`` when a batch was submitted (the poller
    finishes it), otherwise already finished like a live run.
    """
    from evals import runner
    from providers.batch_api import BatchAPIError, submit

    projected = runner.project_cost(evalset, configs, mode=MODE_BATCH)
    if cost_ceiling is not None and projected.get("estimated_total_cost", 0) > cost_ceiling:
        raise ValueError(
            f"the projected batch cost ${projected['estimated_total_cost']:.4f} is over the "
            f"ceiling ${cost_ceiling:.2f}; a batch cannot stop half way, so raise the ceiling "
            "or run live")
    from common.budget import check_budget
    check_budget(workspace)

    run = EvalRun(eval_set_id=evalset.eval_set_id, workspace=workspace, configs=configs,
                  status=STATUS_PENDING, mode=MODE_BATCH)
    store.save_eval_run(run)

    groups: "OrderedDict[tuple, Dict[str, Any]]" = OrderedDict()
    live: List[Tuple[Case, RunConfig, int, str]] = []
    for cfg in configs:
        for case in evalset.cases:
            for attempt in range(1, cfg.resolved_repeats() + 1):
                try:
                    work_dir = runner.prepare_work_dir(case, run.eval_run_id, workspace, attempt)
                except Exception as e:  # noqa: BLE001 - the live path records the same failure on the cell
                    live.append((case, cfg, attempt, f"could not prepare the case's files ({type(e).__name__})"))
                    continue
                prompt = runner.compose_input(case, work_dir)
                request, reason = prepare_agent_cell(case, cfg, workspace, prompt=prompt,
                                                     work_dir=work_dir)
                if request is None:
                    live.append((case, cfg, attempt, reason))
                    continue
                key = _group_key(request["target"], request["agent_id"])
                group = groups.setdefault(key, {"target": request["target"],
                                                "agent_id": request["agent_id"], "items": {}})
                group["items"][_custom_id("t")] = {
                    "case_id": case.case_id, "config_label": cfg.resolved_label(),
                    "attempt": attempt, "prompt": prompt, "work_dir": work_dir,
                    "agent_id": request["agent_id"], "system_prompt": request["system_prompt"],
                    "body": request["body"], "done": False,
                }

    for group in groups.values():
        target, items = group["target"], group["items"]
        row_id = f"evb_{uuid.uuid4().hex[:16]}"
        try:
            created = submit(target, [{"custom_id": cid, "body": meta["body"]}
                                      for cid, meta in items.items()],
                             metadata={"eval_run_id": run.eval_run_id})
        except BatchAPIError as e:
            log.warning("eval %s: batch submit failed, running its cells live: %s",
                        run.eval_run_id, e)
            for meta in items.values():
                case = _case(evalset, meta["case_id"])
                cfg = _config(run, meta["config_label"])
                if case and cfg:
                    live.append((case, cfg, meta["attempt"], f"the batch was refused: {e}"))
            continue
        for meta in items.values():
            meta.pop("body", None)  # the provider holds it now; the row stays small
        _TARGETS[row_id] = target
        _insert_row({
            "batch_row_id": row_id, "eval_run_id": run.eval_run_id, "phase": PHASE_TARGET,
            "provider": target.provider, "base_url": target.base_url, "model": target.model,
            "agent_id": group["agent_id"], "provider_batch_id": created["batch_id"],
            "status": "submitted", "items": items, "submitted_at": _now(),
        })

    done = 0
    for case, cfg, attempt, reason in live:
        result = runner.run_case(case, cfg, evalset, run.eval_run_id, workspace, attempt=attempt)
        _note_live(result, reason)
        done += 1
        if on_progress:
            try:
                on_progress({"eval_run_id": run.eval_run_id, "done": done, "total": len(live),
                             "case_id": case.case_id, "config_label": cfg.resolved_label(),
                             "attempt": attempt, "score": result.score, "live": True})
            except Exception:  # noqa: BLE001 - a progress callback never stops the run
                log.debug("eval batch progress callback failed", exc_info=True)

    return _maybe_finish(run.eval_run_id) or store.get_eval_run(run.eval_run_id) or run


def _note_live(result: EvalResult, reason: str) -> None:
    result.trajectory = list(result.trajectory or []) + [
        {"kind": "note", "summary": f"ran live: {reason}"}]
    store.save_result(result)


def _case(evalset: Optional[EvalSet], case_id: str) -> Optional[Case]:
    for case in (evalset.cases if evalset else []):
        if case.case_id == case_id:
            return case
    return None


def _config(run: EvalRun, label: str) -> Optional[RunConfig]:
    for cfg in run.configs:
        if cfg.resolved_label() == label:
            return cfg
    return None


# ── recording a batched answer ───────────────────────────────────────────────

def _record_run(run: EvalRun, meta: Dict[str, Any], item: Any, row: Dict[str, Any],
                evalset: EvalSet) -> str:
    """The cell's run record, like a live eval run's but priced at the batch
    rate and marked with the batch it came from."""
    from managers import run_manager as rm
    from providers.batch_api import PRICE_FACTOR

    run_id = rm.new_unique_run_id()
    title = f"Eval {evalset.name or evalset.eval_set_id}: {meta['case_id']} (batch)"
    if int(meta.get("attempt") or 1) > 1:
        title += f" (attempt {meta['attempt']})"
    rm.open_run(
        run_id, meta["agent_id"], workspace=run.workspace, title=title,
        channel=EVAL_CHANNEL, execution_mode=EVAL_CHANNEL, session_type=EVAL_CHANNEL,
        message_origin=EVAL_CHANNEL, provider=row["provider"], model=row["model"],
        input=meta["prompt"], link_to_session=False, price_factor=PRICE_FACTOR,
        batch={"provider": row["provider"], "provider_batch_id": row["provider_batch_id"],
               "custom_id": item.custom_id},
    )
    rm.seed_run_input_context(run_id, meta.get("system_prompt") or "", meta["prompt"])
    rm.close_run(run_id, status="completed", exit_code=0, output=item.text, process={
        "response": {"text": item.text, "structured": None},
        "token_usage": {"inbound_tokens": item.input_tokens, "outbound_tokens": item.output_tokens,
                        "total_tokens": item.input_tokens + item.output_tokens,
                        "cached_tokens": item.cached_tokens},
        "tool_calls": [],
        "duration_ms": 0,
    })
    return run_id


def _judge_specs(evalset: EvalSet) -> List[Any]:
    return [s for s in (evalset.graders or []) if s.kind in _JUDGE_KINDS]


def _judge_requests(result: EvalResult, case: Case, evalset: EvalSet
                    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """The judge calls of one cell as batch requests, and the grades that are
    already final (a judge with no rubric, a judge whose model has no batch
    API: graded live here)."""
    from evals import graders
    from providers.batch_api import request_body

    requests: List[Dict[str, Any]] = []
    finished: Dict[str, Any] = {}
    payload = graders._load_run_payload(result.run_id)
    for spec in _judge_specs(evalset):
        params = dict(spec.params or {})
        if spec.kind == "llm_judge":
            req = graders.judge_request(result.output, case, params)
            state: Dict[str, Any] = {}
            if isinstance(req, graders.GradeResult):
                finished[spec.kind] = req
                continue
            messages, ref = req
        else:
            req = graders.rubric_request(result.output, case, params, payload)
            if isinstance(req, graders.GradeResult):
                finished[spec.kind] = req
                continue
            messages, ref, state = req
        try:
            llm, target = _target_for_model(ref["provider"], ref["model"])
        except Exception:  # noqa: BLE001 - an unbuildable judge grades live below and fails there
            llm, target = None, None
        if target is None:
            finished[spec.kind] = graders.grade(result.output, case, spec, run_id=result.run_id)
            continue
        ref = {"provider": target.provider, "model": target.model}
        requests.append({
            "target": target, "kind": spec.kind, "params": params, "ref": ref, "state": state,
            "body": request_body(llm, messages),
        })
    return requests, finished


def _finish_cell(result: EvalResult, case: Case, evalset: EvalSet,
                 judged: Optional[Dict[str, Any]] = None) -> EvalResult:
    from evals import graders
    from evals.snapshot import locate_isolation_dir
    # A cell may be graded in a later process than the one that ran it, so
    # its working directory is found again rather than carried: the eval
    # run's workspace is the set's (start_batch_run runs under it).
    run = store.get_eval_run(result.eval_run_id)
    workspace = (run.workspace if run else None) or evalset.workspace
    work_dir = locate_isolation_dir(workspace, result.eval_run_id, case.case_id, result.attempt)
    scores, combined, passed = graders.grade_all(result.output, case, evalset.graders,
                                                 run_id=result.run_id, precomputed=judged or {},
                                                 work_dir=work_dir)
    result.scores, result.score, result.passed = scores, combined, passed
    return store.save_result(result)


def _process_target_row(row: Dict[str, Any], status: Any) -> None:
    from evals import runner
    from providers.batch_api import PRICE_FACTOR, results
    from evals.graders import model_call_cost

    run = store.get_eval_run(row["eval_run_id"])
    evalset = store.get_eval_set(run.eval_set_id) if run else None
    if run is None or evalset is None:
        _update_row(row["batch_row_id"], status="failed", error="the eval run or set is gone",
                    finished_at=_now())
        return
    target = _resolve_target(row)
    by_id = results(target, status)
    items = row["items"]

    for custom_id, meta in items.items():
        if meta.get("done"):
            continue
        case = _case(evalset, meta["case_id"])
        cfg = _config(run, meta["config_label"])
        if case is None or cfg is None:
            meta["done"] = True
            continue
        item = by_id.get(custom_id)
        if item is not None and item.ok and not item.tool_calls:
            run_id = _record_run(run, meta, item, row, evalset)
            result = EvalResult(
                eval_run_id=run.eval_run_id, case_id=case.case_id,
                config_label=meta["config_label"], attempt=int(meta.get("attempt") or 1),
                target_kind="agent", run_id=run_id, ok=True, output=item.text,
                inbound_tokens=item.input_tokens, outbound_tokens=item.output_tokens,
                cost=round(model_call_cost(row["provider"], row["model"], item.input_tokens,
                                           item.output_tokens) * PRICE_FACTOR, 6),
                trajectory=[{"run_id": run_id, "kind": "run", "summary": f"{meta['agent_id']} (batch)"}],
            )
            requests, finished = _judge_requests(result, case, evalset)
            if requests:
                store.save_result(result)
                # Kept on the row until they are submitted, so a process that
                # dies before the judge batch goes out submits them on reclaim.
                meta["judges"] = [{
                    "result_id": result.result_id, "case_id": case.case_id,
                    "kind": req["kind"], "params": req["params"], "ref": req["ref"],
                    "state": req["state"], "body": req["body"],
                    "finished": {k: v.to_dict() for k, v in finished.items()},
                } for req in requests]
            else:
                _finish_cell(result, case, evalset, finished)
        elif item is not None and item.ok and item.tool_calls:
            result = runner.run_case(case, cfg, evalset, run.eval_run_id, run.workspace,
                                     attempt=int(meta.get("attempt") or 1))
            _note_live(result, "the batched answer asked for a tool, so the cell ran live")
        elif item is not None and (item.error or "").startswith("canceled"):
            store.save_result(EvalResult(
                eval_run_id=run.eval_run_id, case_id=case.case_id,
                config_label=meta["config_label"], attempt=int(meta.get("attempt") or 1),
                ok=False, error="the batch was cancelled", target_kind="agent"))
        else:
            reason = item.error if item is not None else "the batch returned no result"
            result = runner.run_case(case, cfg, evalset, run.eval_run_id, run.workspace,
                                     attempt=int(meta.get("attempt") or 1))
            _note_live(result, f"the batched request failed ({reason})")
        meta["done"] = True
        _update_row(row["batch_row_id"], items=items, checked_at=_now())

    _submit_judges(run, row["batch_row_id"], items)
    _update_row(row["batch_row_id"], status="done", items=items, finished_at=_now(),
                counts=status.counts)


def _submit_judges(run: EvalRun, batch_row_id: str, items: Dict[str, Any]) -> None:
    """Send the judge calls the target row's cells are waiting on, one batch
    per judge endpoint. Each group's calls leave the row once their batch is
    created, so a crash between two groups never submits a group twice."""
    from providers.batch_api import BatchAPIError, submit

    evalset = store.get_eval_set(run.eval_set_id)
    endpoints: Dict[tuple, Any] = {}

    def endpoint(ref: Dict[str, str]):
        key = (ref.get("provider") or "", ref.get("model") or "")
        if key not in endpoints:
            try:
                endpoints[key] = _target_for_model(*key)[1]
            except Exception:  # noqa: BLE001 - an unbuildable judge is graded live below
                endpoints[key] = None
        return endpoints[key]

    groups: "OrderedDict[tuple, Dict[str, Any]]" = OrderedDict()
    no_endpoint: Dict[str, Any] = {}
    for source_id, meta in items.items():
        for judge in meta.get("judges") or []:
            target = endpoint(judge["ref"])
            if target is None:
                no_endpoint[_custom_id("j")] = judge
                continue
            group = groups.setdefault(target.key(), {"target": target, "requests": {}, "sources": set()})
            group["requests"][_custom_id("j")] = {**judge, "done": False}
            group["sources"].add(source_id)
    if no_endpoint:
        _grade_live(no_endpoint, evalset)
    for group in groups.values():
        target, requests = group["target"], group["requests"]
        row_id = f"evb_{uuid.uuid4().hex[:16]}"
        try:
            created = submit(target, [{"custom_id": cid, "body": m["body"]} for cid, m in requests.items()],
                             metadata={"eval_run_id": run.eval_run_id, "phase": PHASE_JUDGE})
        except BatchAPIError as e:
            log.warning("eval %s: judge batch refused, grading live: %s", run.eval_run_id, e)
            _grade_live(requests, evalset)
        else:
            for meta in requests.values():
                meta.pop("body", None)
            _TARGETS[row_id] = target
            _insert_row({
                "batch_row_id": row_id, "eval_run_id": run.eval_run_id, "phase": PHASE_JUDGE,
                "provider": target.provider, "base_url": target.base_url, "model": target.model,
                "agent_id": "", "provider_batch_id": created["batch_id"], "status": "submitted",
                "items": requests, "submitted_at": _now(),
            })
        judged_endpoint = target.key()
        for source_id in group["sources"]:
            meta = items[source_id]
            meta["judges"] = [j for j in meta.get("judges") or []
                              if endpoint(j["ref"]) is not None
                              and endpoint(j["ref"]).key() != judged_endpoint]
            if not meta["judges"]:
                meta.pop("judges")
        _update_row(batch_row_id, items=items)
    for meta in items.values():
        if meta.get("judges") is not None and all(endpoint(j["ref"]) is None for j in meta["judges"]):
            meta.pop("judges")
    _update_row(batch_row_id, items=items)


def _grade_live(items: Dict[str, Any], evalset: Optional[EvalSet]) -> None:
    """Judge requests that could not go to a batch: every cell graded live."""
    seen = set()
    for meta in items.values():
        if meta["result_id"] in seen:
            continue
        seen.add(meta["result_id"])
        result = store.get_result(meta["result_id"])
        case = _case(evalset, meta["case_id"])
        if result is not None and case is not None:
            _finish_cell(result, case, evalset)


def _process_judge_row(row: Dict[str, Any], status: Any) -> None:
    from evals import graders
    from providers.batch_api import PRICE_FACTOR, results

    run = store.get_eval_run(row["eval_run_id"])
    evalset = store.get_eval_set(run.eval_set_id) if run else None
    target = _resolve_target(row)
    by_id = results(target, status)
    items = row["items"]

    # A cell may have two judge calls (llm_judge and rubric): collect both,
    # then grade the cell once.
    per_result: Dict[str, Dict[str, Any]] = {}
    for custom_id, meta in items.items():
        if meta.get("done"):
            continue
        item = by_id.get(custom_id)
        ref, params = meta["ref"], meta["params"]
        if meta["kind"] == "llm_judge":
            grade = (graders.judge_result(item.text, params) if item is not None and item.ok
                     else graders.GradeResult("llm_judge", 0.0, False,
                                              f"judge call failed: {item.error if item else 'no result'}"))
        else:
            grade = (graders.rubric_result(item.text, ref, meta["state"], inbound=item.input_tokens,
                                           outbound=item.output_tokens, price_factor=PRICE_FACTOR)
                     if item is not None and item.ok
                     else graders.rubric_failed(ref, meta["state"], item.error if item else "no result",
                                                "The grader failed: the batched call did not complete."))
        entry = per_result.setdefault(meta["result_id"], {"case_id": meta["case_id"], "grades": {},
                                                          "finished": meta.get("finished") or {}})
        entry["grades"][meta["kind"]] = grade
        meta["done"] = True

    for result_id, entry in per_result.items():
        result = store.get_result(result_id)
        case = _case(evalset, entry["case_id"])
        if result is None or case is None:
            continue
        judged = {k: graders.GradeResult(v["kind"], v["score"], v["passed"], v.get("detail", ""),
                                         v.get("extra") or {})
                  for k, v in entry["finished"].items()}
        judged.update(entry["grades"])
        _finish_cell(result, case, evalset, judged)
    _update_row(row["batch_row_id"], status="done", items=items, finished_at=_now(),
                counts=status.counts)


def _fail_row(row: Dict[str, Any], state: str, error: Optional[str]) -> None:
    """A batch the provider rejected, expired with nothing done, or that was
    cancelled: its cells are recorded (live for a failure, stopped for a
    cancel) so the run can finish."""
    from evals import runner

    run = store.get_eval_run(row["eval_run_id"])
    evalset = store.get_eval_set(run.eval_set_id) if run else None
    items = row["items"]
    if row["phase"] == PHASE_JUDGE:
        if state != "cancelled":
            _grade_live({k: m for k, m in items.items() if not m.get("done")}, evalset)
    elif run is not None and evalset is not None:
        for meta in items.values():
            if meta.get("done"):
                continue
            case, cfg = _case(evalset, meta["case_id"]), _config(run, meta["config_label"])
            if case is None or cfg is None:
                continue
            if state == "cancelled":
                store.save_result(EvalResult(
                    eval_run_id=run.eval_run_id, case_id=case.case_id,
                    config_label=meta["config_label"], attempt=int(meta.get("attempt") or 1),
                    ok=False, error="the batch was cancelled", target_kind="agent"))
            else:
                result = runner.run_case(case, cfg, evalset, run.eval_run_id, run.workspace,
                                         attempt=int(meta.get("attempt") or 1))
                _note_live(result, f"the batch {state}: {error or 'no detail'}")
            meta["done"] = True
    _update_row(row["batch_row_id"], status="cancelled" if state == "cancelled" else "failed",
                items=items, error=error or state, finished_at=_now())


# ── finishing a run ──────────────────────────────────────────────────────────

def _maybe_finish(eval_run_id: str) -> Optional[EvalRun]:
    """Summarize and close the run once no batch of it is still open."""
    from evals.runner import summarize

    run = store.get_eval_run(eval_run_id)
    if run is None or run.status != STATUS_PENDING:
        return run
    if any(r["status"] in ("submitted", "processing") for r in rows_for_run(eval_run_id)):
        return None
    results = store.list_results(eval_run_id)
    run.summary = summarize(eval_run_id, run.configs)
    run.total_cost = round(sum(r.cost for r in results), 6)
    cancelled = any(r["status"] == "cancelled" for r in rows_for_run(eval_run_id))
    run.status = "stopped" if cancelled else "completed"
    if cancelled:
        run.error = run.error or "cancelled"
    run.finished_at = utc_iso()
    return store.save_eval_run(run)


def cancel_run(eval_run_id: str) -> EvalRun:
    """Cancel every open provider batch of a run. Requests already answered
    are still collected by the next poll; the rest are recorded as stopped."""
    from providers.batch_api import BatchAPIError, cancel

    run = store.get_eval_run(eval_run_id)
    if run is None:
        raise ValueError(f"Eval run not found: {eval_run_id}")
    if run.status != STATUS_PENDING:
        raise ValueError(f"eval run {eval_run_id} is {run.status}, not waiting on a batch")
    for row in rows_for_run(eval_run_id):
        if row["status"] != "submitted":
            continue
        try:
            cancel(_resolve_target(row), row["provider_batch_id"])
        except (BatchAPIError, RuntimeError) as e:
            log.warning("eval batch %s: cancel failed: %s", row["batch_row_id"], e)
            _update_row(row["batch_row_id"], error=f"cancel failed: {e}")
    run.error = "cancelled"
    return store.save_eval_run(run)


# ── polling ──────────────────────────────────────────────────────────────────

def _claim(batch_row_id: str) -> bool:
    with db.transaction() as conn:
        cursor = conn.execute(
            "UPDATE eval_batches SET status = 'processing', checked_at = ? "
            "WHERE batch_row_id = ? AND status = 'submitted'", (_now(), batch_row_id))
    return bool(cursor.rowcount)


def _reclaim_stale() -> None:
    cutoff = (datetime.now(timezone.utc) - RECLAIM_AFTER).isoformat()
    with db.transaction() as conn:
        conn.execute(
            "UPDATE eval_batches SET status = 'submitted' WHERE status = 'processing' "
            "AND (checked_at IS NULL OR checked_at < ?)", (cutoff,))


def _process(row: Dict[str, Any], status: Any) -> None:
    try:
        if row["phase"] == PHASE_TARGET:
            _process_target_row(row, status)
        else:
            _process_judge_row(row, status)
    except Exception as e:  # noqa: BLE001 - a crash leaves the row to be reclaimed, never a lost run
        log.exception("eval batch %s: processing failed", row["batch_row_id"])
        _update_row(row["batch_row_id"], status="submitted", error=f"processing failed: {e}")
        return
    _maybe_finish(row["eval_run_id"])


def poll_pending(*, force: bool = False, background: bool = True) -> Dict[str, int]:
    """Check every open provider batch once. Called from the scheduler tick;
    self-throttled to ``AGENTS_HUB_EVAL_BATCH_POLL_SECONDS`` unless ``force``.
    An ended batch is processed in a thread of its own (``background``)."""
    global _LAST_POLL
    from providers.batch_api import BatchAPIError, retrieve

    with _POLL_LOCK:
        now = time.monotonic()
        if not force and now - _LAST_POLL < _poll_seconds():
            return {"checked": 0, "ended": 0}
        _LAST_POLL = now
    _reclaim_stale()
    rows = db.get_conn().execute(
        "SELECT * FROM eval_batches WHERE status = 'submitted'").fetchall()
    checked = ended = 0
    for raw in rows:
        row = _row(raw)
        checked += 1
        try:
            status = retrieve(_resolve_target(row), row["provider_batch_id"])
        except (BatchAPIError, RuntimeError) as e:
            _update_row(row["batch_row_id"], checked_at=_now(), error=str(e)[:500])
            continue
        if status.state == "in_progress":
            _update_row(row["batch_row_id"], checked_at=_now(), counts=status.counts, error=None)
            continue
        if not _claim(row["batch_row_id"]):
            continue
        ended += 1
        if status.state == "ended":
            if background:
                threading.Thread(target=_process, args=(row, status), daemon=True,
                                 name=f"eval-batch-{row['batch_row_id']}").start()
            else:
                _process(row, status)
        else:
            _fail_row(row, status.state, status.error)
            _maybe_finish(row["eval_run_id"])
    return {"checked": checked, "ended": ended}


__all__ = [
    "MODE_BATCH", "MODE_LIVE", "STATUS_PENDING", "cancel_run", "get_row", "poll_pending",
    "prepare_agent_cell", "progress", "rows_for_run", "start_batch_run",
]
