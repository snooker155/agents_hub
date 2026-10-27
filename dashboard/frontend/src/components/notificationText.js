/**
 * A notification's title and text in the reader's language.
 *
 * The backend does not know which language the reader uses, so a notification
 * it raises also carries `source.message`: a key and its values
 * (managers/runs/notifications.py, notification_message). The text is built
 * here from components.notifications.messages.<key>; the stored title and body
 * stay as the fallback for an older notification or a key this build does not
 * know.
 */

// Which body a message uses when a value it names may be missing.
const BODY_VARIANT = {
  approvalNeeded: (p) => (p.reason ? 'bodyWithReason' : 'body'),
  toolApprovalNeeded: (p) => (p.reason ? 'bodyWithReason' : 'body'),
  toolApprovalNeededTeam: (p) => (p.reason ? 'bodyWithReason' : 'body'),
  taskFailed: (p) => (p.error ? 'body' : 'bodyNoError'),
  routingFailed: (p) => (p.error ? 'body' : 'bodyNoError'),
  taskNeedsInput: (p) => (p.question ? 'body' : 'bodyNoQuestion'),
  scheduledTaskStarted: (p) => (p.agent ? 'body' : 'bodyNoAgent'),
  scheduledJobPaused: (p) => (p.error ? 'body' : 'bodyNoError'),
  scheduledJobFailed: (p) => (p.error ? 'body' : 'bodyNoError'),
  scheduledJobTargetMissing: (p) => (p.error ? 'body' : 'bodyNoError'),
};

export function notificationText(n, t) {
  const message = n?.source?.message;
  if (!message?.key) return { title: n?.title || '', body: n?.body || '' };
  const params = message.params || {};
  const base = `components.notifications.messages.${message.key}`;
  const variant = BODY_VARIANT[message.key]?.(params) || 'body';
  return {
    title: t(`${base}.title`, { ...params, defaultValue: n.title || '' }),
    body: t(`${base}.${variant}`, { ...params, defaultValue: n.body || '' }),
  };
}

// Where a tool call held in a background run is answered besides the bell:
// the team run, the loop's run, else the flow run (common/tool_approvals.py).
export function approvalRunPath(source) {
  if (source?.team_id && source?.team_run_id) {
    return `/teams/${encodeURIComponent(source.team_id)}?run=${encodeURIComponent(source.team_run_id)}`;
  }
  if (source?.loop_id && source?.loop_run_id) {
    return `/loops/${encodeURIComponent(source.loop_id)}/runs/${encodeURIComponent(source.loop_run_id)}`;
  }
  if (source?.flow_id && source?.flow_run_id) {
    return `/flows/${encodeURIComponent(source.flow_id)}/runs/${encodeURIComponent(source.flow_run_id)}`;
  }
  return null;
}
