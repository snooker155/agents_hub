/**
 * Each node's status in the run on screen, for the node list on the run page
 * and the status bar over the log.
 *
 * Only the latest pass counts (the events from the last `flow_start` on): a
 * chat replays the flow on every turn, and a node's outcome on the previous
 * turn says nothing about this one. Once the pass has ended, nothing in it is
 * still running or waiting: a node that had started when the run was stopped
 * reads as stopped, one that never started as ready.
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
  const lastType = {};
  for (const log of pass) {
    if (FLOW_END[log.type]) ended = FLOW_END[log.type];
    if (log.node_id && NODE_LIFECYCLE.has(log.type)) lastType[log.node_id] = log.type;
  }
  // The log is the authority on a pass it has seen end; the flow's running
  // flag only covers a run whose first events have not arrived yet.
  const runActive = !ended && (from >= 0 || Boolean(runningFlag));
  return (nodes || []).map((node) => {
    const ty = lastType[node.id];
    let status;
    if (ty === 'agent_start') status = ended ? 'stopped' : 'running';
    else if (ty === 'agent_stopped') status = 'stopped';
    else if (ty === 'agent_error') status = 'error';
    else if (ty === 'agent_finish' || ty === 'node_skip') status = 'done';
    else status = runActive ? 'pending' : 'ready';
    return { id: node.id, label: node.data?.label || node.data?.agent_id || 'Agent', status };
  });
}
