/**
 * A tool call that waits for a person inside a chat turn (docs/hooks.md, "In
 * chat"). The turn streams `tool_approval` when a call starts waiting and
 * `tool_approval_resolved` when it was answered, timed out or the run was
 * stopped; both carry `approval_id`. The message keeps them as `approvals`,
 * one entry per call, newest state wins.
 */

const FIELDS = ['approval_id', 'run_id', 'tool', 'input', 'reason', 'by', 'status',
  'created_at', 'expires_at', 'note', 'decided_by_name', 'decided_at'];

function pick(event) {
  const out = {};
  for (const key of FIELDS) {
    if (event[key] !== undefined) out[key] = event[key];
  }
  return out;
}

/** `approvals` with the event's call added or updated. */
export function upsertApproval(approvals, event) {
  if (!event?.approval_id) return approvals || [];
  const list = approvals || [];
  const idx = list.findIndex((a) => a.approval_id === event.approval_id);
  if (idx === -1) return [...list, pick(event)];
  const next = [...list];
  next[idx] = { ...next[idx], ...pick(event) };
  return next;
}

/** `messages` with the approval event applied to the message `targetId`. */
export function applyApprovalEvent(messages, targetId, event) {
  if (!targetId) return messages;
  return messages.map((m) => (m.id === targetId ? { ...m, approvals: upsertApproval(m.approvals, event) } : m));
}

/** The call a message's turn waits on right now, or null. */
export function pendingApproval(msg) {
  return (msg?.approvals || []).find((a) => a.status === 'pending') || null;
}

/** Calls the server says are waiting, merged under what the stream already said. */
export function mergeServerApprovals(local, server) {
  let out = local || [];
  for (const row of server || []) {
    const known = out.find((a) => a.approval_id === row.approval_id);
    // The stream is newer than a read that started before it: keep its status.
    out = known ? out : upsertApproval(out, row);
  }
  return out;
}
