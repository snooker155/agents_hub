"""
Regression replay: re-run a recorded run against the same (or an overridden)
model/prompt and diff the outputs.

Every run stores its full structured input (``run_payloads.input_context`` =
{system_prompt, history, user_message}) and final ``response``, so a faithful
re-invocation only needs the original ``user_message`` plus the agent id +
workspace. The agent is rebuilt via ``agent_factory.create_agent`` (optionally
overriding provider/model), re-invoked through the shared ``invoke_agent`` core,
and recorded as a *new* run tagged ``channel="replay"`` so it is excluded from
production cost/usage/budget aggregation while still being fully inspectable.

This module owns the replay mechanics; the HTTP surface is ``routes/replay.py``.
"""
from __future__ import annotations

import difflib
from typing import Any, Dict, Optional

# Runs created by a replay carry this channel so cost/budget aggregation can
# skip them (re-invocation has real token cost, but it is evaluation spend, not
# the workspace's production spend).
REPLAY_CHANNEL = "replay"


class ReplayError(Exception):
    """The original run cannot be replayed (missing, no input, no agent)."""


def _run_cost(provider: str, model: str, inbound: int, outbound: int) -> float:
    try:
        from common.pricing import load_price_map, run_cost_usd
        fake = {
            "provider": provider, "model": model,
            "process": {"token_usage": {"inbound_tokens": inbound, "outbound_tokens": outbound}},
        }
        return round(run_cost_usd(fake, load_price_map()), 6)
    except Exception:
        return 0.0


def replay_run(
    run_id: str,
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Replay ``run_id`` and return a structured diff of original vs replay.

    ``provider`` / ``model`` optionally override the model the replay uses (to
    compare a run across models). Raises :class:`ReplayError` when the original
    run has nothing replayable.
    """
    from managers import run_manager as rm

    original = rm.get_run_by_id(run_id)
    if not original:
        raise ReplayError(f"Run not found: {run_id}")

    agent_id = str(original.get("agent_id") or "").strip()
    if not agent_id:
        raise ReplayError("Original run has no agent to replay")

    proc = rm.get_run_process(run_id) or {}
    input_ctx = proc.get("input_context") or {}
    user_message = str(input_ctx.get("user_message") or original.get("input") or "").strip()
    if not user_message:
        raise ReplayError("Original run has no recorded input to replay")

    workspace = original.get("workspace") or None
    original_text = str((proc.get("response") or {}).get("text") or original.get("output") or "")
    orig_tu = (proc.get("token_usage") or {})
    orig_inbound = int(orig_tu.get("inbound_tokens") or 0)
    orig_outbound = int(orig_tu.get("outbound_tokens") or 0)

    overrides: Dict[str, Any] = {}
    if provider:
        overrides["provider"] = provider
    if model:
        overrides["model"] = model

    from agents.agent_factory import create_agent
    agent = create_agent(agent_id, workspace, **overrides)

    resolved_provider = getattr(agent, "provider", "") or ""
    resolved_model = getattr(agent, "model", "") or ""

    # Record the replay as its own run, clearly tagged so it never counts as
    # production spend. link_to_session=False keeps it out of the source session.
    replay_run_id = rm.new_unique_run_id()
    rm.open_run(
        replay_run_id,
        agent_id,
        workspace=workspace,
        title=f"Replay of {run_id}",
        channel=REPLAY_CHANNEL,
        execution_mode=REPLAY_CHANNEL,
        session_type=REPLAY_CHANNEL,
        message_origin=REPLAY_CHANNEL,
        provider=resolved_provider,
        model=resolved_model,
        input=user_message,
        replay_of=run_id,
        link_to_session=False,
    )
    rm.seed_run_input_context(replay_run_id, getattr(agent, "system_prompt", "") or "", user_message)

    from agents.agent_invoke import invoke_agent
    invocation = invoke_agent(agent, user_message, run_id=replay_run_id)
    result = invocation.result
    rm.close_run_from_result(replay_run_id, result, process=invocation.process)

    replay_ok = bool(getattr(result, "ok", False))
    replay_text = str(getattr(result, "agent_output", "") or "") if replay_ok else ""
    replay_error = None if replay_ok else str(getattr(result, "error", "") or "agent error")

    tu = (invocation.process or {}).get("token_usage") or {}
    rep_inbound = int(tu.get("inbound_tokens") or 0)
    rep_outbound = int(tu.get("outbound_tokens") or 0)

    diff = "".join(difflib.unified_diff(
        original_text.splitlines(keepends=True),
        replay_text.splitlines(keepends=True),
        fromfile=f"original ({original.get('model') or '?'})",
        tofile=f"replay ({resolved_model or '?'})",
    ))

    return {
        "original_run_id": run_id,
        "replay_run_id": replay_run_id,
        "agent_id": agent_id,
        "workspace": workspace,
        "identical": original_text.strip() == replay_text.strip(),
        "original": {
            "provider": original.get("provider") or "",
            "model": original.get("model") or "",
            "text": original_text,
            "inbound_tokens": orig_inbound,
            "outbound_tokens": orig_outbound,
            "cost": _run_cost(original.get("provider") or "", original.get("model") or "", orig_inbound, orig_outbound),
        },
        "replay": {
            "provider": resolved_provider,
            "model": resolved_model,
            "ok": replay_ok,
            "error": replay_error,
            "text": replay_text,
            "inbound_tokens": rep_inbound,
            "outbound_tokens": rep_outbound,
            "duration_ms": invocation.duration_ms,
            "cost": _run_cost(resolved_provider, resolved_model, rep_inbound, rep_outbound),
        },
        "diff": diff,
    }
