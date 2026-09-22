"""
AgentRunManager: shared run state, lifecycle tracking, and stop coordination.

Run records live in SQLite (``.agents_hub/agents_hub.db``, see ``common.db``).
Every execution channel — chat SSE, local subprocess, node worker, docker
container, flow node — records runs through this module, so all records share
ONE structure regardless of mode:

Run record (the ``runs`` table, returned as a dict):
- meta:   run_id, task_id, agent_id, session_id, session_type, channel,
          execution_mode, node_id, container_name, workspace, title,
          provider, model, message_origin
- state:  status (running|stop|completed|stopped|failed|error|pending|
          assigned|awaiting_approval), pid, exit_code, error,
          created_at / started_at / finished_at
- I/O:    input (raw instruction), output (final text),
          log_file (plain-text log on disk, linked — never stored inline)
- stats:  process = {token_usage, duration_ms}  (slim projection)

Structured heavy payload (the ``run_payloads`` table, via get_run_process):
- input_context   {system_prompt, history[], user_message}
- response        {text, structured}        — structured = AgentResponse JSON
- tool_calls      [{step, tool, input, output}, ...]
- reasoning       [trace lines]
- llm_invocations / llm_raw_responses / artifacts
Writers keep passing ``process=...`` payloads in any historical shape; they are
normalised via ``common.run_payloads.canonicalize`` at this single chokepoint.

Public API (state):
- load_runs() / save_runs(runs)
- upsert_run(run) / update_run(run_id, updates) / delete_run(run_id)
- get_run_by_id(run_id) / utc_now_iso()

Public API (lifecycle):
- get_status(task_id, run_id) / stop_run(task_id, run_id) / stop_run_by_id(run_id)
- get_in_progress_runs_for_node(node_id) / fail_in_progress_runs_for_node(...)
- run_log_path(run_id)

Where the code lives
--------------------
This module is a facade. The implementation was split into ``managers.runs``
once it had grown to four unrelated concerns in one file, and it stays a facade
because the whole codebase imports run bookkeeping from here:

- ``managers.runs.store``         — the ``runs`` / ``run_payloads`` tables,
                                    row mapping, queries, upsert/update/delete
- ``managers.runs.lifecycle``     — preopen/open/close, status, stop, node
                                    helpers, run_log_path
- ``managers.runs.task_finalize`` — how a finished run moves its task along
                                    (finalize, retry, review, continuations)
- ``managers.runs.notifications`` — instance bookkeeping, inbox notifications
                                    and the dashboard's live run deltas
- ``managers.runs.groups``        — the flow/loop/team/container run-group
                                    abstraction (imported on demand)

Every name below keeps the signature and behaviour it had before the split.
Private helpers are re-exported too: a few callers and tests reach for them by
name, and a facade that hides them would be a breaking change dressed up as a
refactor.
"""
from __future__ import annotations

from .runs.lifecycle import (  # noqa: F401
    HERE,
    PROJECT_ROOT,
    RUN_LOGS_DIR,
    _find_active_run_for_task,
    _pid_exists,
    _stop_run_record,
    close_run,
    close_run_from_result,
    fail_in_progress_runs_for_node,
    get_all_runs_for_node,
    get_in_progress_runs_for_node,
    get_run_by_id,
    get_status,
    new_unique_run_id,
    open_run,
    preopen_run,
    run_log_path,
    stop_run,
    stop_run_by_id,
    utc_now_iso,
)
from .runs.notifications import (  # noqa: F401
    _NOTIFY_TERMINAL_STATUSES,
    _ROUTING_AGENT_ID,
    _RUN_DELTA_FIELDS,
    _TERMINAL_RUN_STATUSES,
    _notify_task_run_finished,
    _notify_task_run_started,
    _publish_run_delta,
    _sync_instance,
    _task_already_announced,
)
from .runs.store import (  # noqa: F401
    RUN_PROCESS_DIR,
    _RUN_LIST_COLUMNS,
    _TOKEN_COLUMNS,
    _apply,
    _list_row_to_record,
    _load_runs,
    _merge_tokens,
    _row_to_record,
    _save_runs,
    _session_status_from_counts,
    _update_run,
    _upsert_run,
    _utc_now_iso,
    _write_payload_row,
    _write_record,
    compact_runs,
    delete_run,
    delete_run_process,
    get_run_process,
    get_runs_by_ids,
    load_runs,
    query_runs,
    save_runs,
    seed_run_input_context,
    session_run_stats,
    update_run,
    upsert_run,
)
from .runs.task_finalize import (  # noqa: F401
    _auto_start_review,
    _delete_runs_by_task_status,
    _maybe_retry_failed_run,
    _trigger_session_continuation,
    delete_assigned_run,
    delete_awaiting_approval_run,
    finalize_flow_task,
    finalize_task_from_run,
    park_task_awaiting_input,
)
