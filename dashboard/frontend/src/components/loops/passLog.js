import { nodeStatusesFor } from '../flowEditor/nodeStatus';

/**
 * A pass's flow log, closed off once the pass is over: a loop pass ends
 * without a flow_finish line of its own (the loop, not runtime/flow_run.py,
 * drives it), and without one a node that was working when it ended would
 * keep spinning.
 */
export function passEvents(events, iteration) {
  const list = events || [];
  if (iteration.status === 'running') return list;
  if (list.some((e) => e.type === 'flow_finish' || e.type === 'flow_stopped')) return list;
  return [...list, {
    type: iteration.status === 'stopped' ? 'flow_stopped' : 'flow_finish',
    timestamp: iteration.finished_at || '',
  }];
}

/**
 * Every node has had its turn while the pass is still open: the judge has
 * the result now.
 */
export function isEvaluating(nodes, events, iteration) {
  if (iteration.status !== 'running' || !nodes?.length) return false;
  const statuses = nodeStatusesFor(nodes, passEvents(events, iteration), true);
  return statuses.some((s) => s.status === 'done')
    && statuses.every((s) => ['done', 'skipped', 'error'].includes(s.status));
}

/**
 * The agent replies of a run's passes, one per node run in their logs, in
 * the shape the chat's artifact readers take (chat/artifactOps.js,
 * chat/turnViews.js) once their trails fill in the files and views.
 */
export function passReplies(iterations, logs) {
  const out = [];
  const seen = new Set();
  for (const it of iterations || []) {
    for (const ev of logs[it.iteration] || []) {
      const runId = ev?.run_id;
      if (!runId || seen.has(runId) || (ev.type !== 'agent_finish' && ev.type !== 'agent_error')) continue;
      seen.add(runId);
      out.push({ id: `${it.iteration}:${runId}`, role: 'agent', run_id: runId, content: '' });
    }
  }
  return out;
}
