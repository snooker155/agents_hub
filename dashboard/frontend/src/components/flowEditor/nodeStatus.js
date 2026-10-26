/**
 * Each node's status in the run on screen, for the node list on the run page
 * and the status bar over the log.
 *
 * Only the latest pass counts (the events from the last `flow_start` on): a
 * chat replays the flow on every turn, and a node's outcome on the previous
 * turn says nothing about this one. Once the pass has ended, nothing in it is
 * still running or waiting: a node that had started when the run was stopped
 * reads as stopped, one that never started as ready.
 *
 * A node the pass went past is `skipped`, with the engine's `reason` beside
 * it (a branch not chosen, a failed step before it), not `done`: it did not
 * run. One a run again from a node took from the turn before (already_done)
 * did run, and reads as done.
 */
const FLOW_END = { flow_finish: 'finished', flow_stopped: 'stopped' };
const NODE_LIFECYCLE = new Set(['agent_start', 'agent_finish', 'agent_error', 'agent_stopped', 'node_skip']);

export function nodeStatusesFor(nodes, logs, runningFlag = false) {
  const list = logs || [];
  let from = -1;
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (list[i].type === 'flow_start') { from = i; break; }
  }
  const pass = from >= 0 ? list.slice(from) : list;
  let ended = null;
  const lastEvent = {};
  for (const log of pass) {
    if (FLOW_END[log.type]) ended = FLOW_END[log.type];
    if (log.node_id && NODE_LIFECYCLE.has(log.type)) lastEvent[log.node_id] = log;
  }
  // The log is the authority on a pass it has seen end; the flow's running
  // flag only covers a run whose first events have not arrived yet.
  const runActive = !ended && (from >= 0 || Boolean(runningFlag));
  return (nodes || []).map((node) => {
    const ev = lastEvent[node.id];
    const ty = ev?.type;
    const skipReason = ty === 'node_skip' ? ev.reason || '' : '';
    let status;
    if (ty === 'agent_start') status = ended ? 'stopped' : 'running';
    else if (ty === 'agent_stopped') status = 'stopped';
    else if (ty === 'agent_error') status = 'error';
    else if (ty === 'agent_finish' || skipReason === 'already_done') status = 'done';
    else if (ty === 'node_skip') status = 'skipped';
    else status = runActive ? 'pending' : 'ready';
    return {
      id: node.id, label: node.data?.label || node.data?.agent_id || 'Agent', status,
      ...(status === 'skipped' ? { reason: skipReason } : {}),
    };
  });
}

// A status row's badge and its tooltip: a skipped node says why in a few
// words on the badge and in full on hover.
export function nodeStatusText(t, row) {
  if (row.status !== 'skipped') return { badge: t(`flowEditor.status.${row.status}`, { defaultValue: row.status }), title: undefined };
  const reason = row.reason || '';
  return {
    badge: t(`flowEditor.skipShort.${reason}`, { defaultValue: t('flowEditor.status.skipped') }),
    title: reason ? t(`flowEditor.skipReasons.${reason}`, { defaultValue: '' }) || undefined : undefined,
  };
}
