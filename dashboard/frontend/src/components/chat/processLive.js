/**
 * Delegated runs in the Process panel while the turn is live.
 *
 * The panel's run rows list their tool calls. A delegation call gets a
 * `delegation` record holding the worker's own steps in the same shape as a
 * row (tools, reasoning, output), so the panel draws the worker with the same
 * graph from the moment it starts and adds each of its steps as it happens,
 * instead of fetching the run once it has finished. A worker that delegates in
 * turn gets a record inside its own tool calls, the same way.
 *
 * Once the turn is over the row is reloaded from the run log, and the node
 * fetches the finished run as before.
 */
import { DELEGATION_TOOLS } from './trail';
import { policyVerdict } from '../policyVerdict';

// Apply `fn` to the record of delegation `runId`, wherever it is nested.
// Returns the same array when nothing matched.
function mapLive(tools, runId, fn) {
  if (!Array.isArray(tools)) return tools;
  let changed = false;
  const next = tools.map((tc) => {
    const d = tc?.delegation;
    if (!d) return tc;
    if (d.run_id === runId) { changed = true; return { ...tc, delegation: fn(d) }; }
    const inner = mapLive(d.tools, runId, fn);
    if (inner === d.tools) return tc;
    changed = true;
    return { ...tc, delegation: { ...d, tools: inner } };
  });
  return changed ? next : tools;
}

// Give the record to the delegation call it stands for: the last one still
// running without one. A call that was not seen gets a stand-in.
function attach(tools, record) {
  const list = [...(tools || [])];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const tc = list[i];
    if (DELEGATION_TOOLS.has(tc?.tool) && tc.running && !tc.delegation) {
      list[i] = { ...tc, delegation: record };
      return list;
    }
  }
  list.push({ tool: 'run_agent_tool', input: record.input, output: null, running: true, delegation: record });
  return list;
}

function resolveLast(tools, patch) {
  const list = [...(tools || [])];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (list[i]?.running) { list[i] = { ...list[i], ...patch, running: false }; break; }
  }
  return list;
}

// What one event of a delegated run does to its record.
function patchFor(event) {
  switch (event.type) {
    case 'delegation_end':
      return (d) => ({
        ...d,
        running: false,
        ok: event.ok,
        output: event.output || d.output || '',
        error: event.error || '',
        duration_ms: event.duration_ms,
      });
    case 'think':
    case 'plan':
      return (d) => ({ ...d, reasoning: [...(d.reasoning || []), { step: event.step, content: event.content }] });
    case 'tool_start':
      return (d) => ({
        ...d,
        tools: [...(d.tools || []), { step: event.step, tool: event.tool, input: event.input, output: null, running: true }],
      });
    case 'tool_end':
      return (d) => ({ ...d, tools: resolveLast(d.tools, { output: event.output, ...policyVerdict(event) }) });
    case 'tool_error':
      return (d) => ({ ...d, tools: resolveLast(d.tools, { output: `ERROR: ${event.error}`, error: true, ...policyVerdict(event) }) });
    case 'text':
      // What the worker said last is its answer so far.
      return (event.content || '').trim() ? (d) => ({ ...d, output: event.content }) : null;
    default:
      return null;
  }
}

/** The panel's run rows with one event of a delegated run applied. */
export function applyDelegationEvent(messageRuns, event) {
  const runs = messageRuns || [];
  if (!runs.length) return runs;
  if (event.type === 'delegation_start') {
    const record = {
      run_id: event.run_id,
      agent_id: event.agent_id,
      agent_name: event.agent_name || event.agent_id,
      input: event.input || '',
      running: true,
      ok: null,
      output: '',
      tools: [],
      reasoning: [],
    };
    const parent = event.parent_run_id;
    // Started by a row's own run, by a worker nested in some row, or, when
    // neither is known, by the run in progress (the last row).
    const own = parent ? runs.findIndex((mr) => mr.run_id === parent) : -1;
    if (own !== -1 || !parent) {
      const at = own !== -1 ? own : runs.length - 1;
      return runs.map((mr, i) => (i === at ? { ...mr, tools: attach(mr.tools, record) } : mr));
    }
    let placed = false;
    const next = runs.map((mr) => {
      if (placed) return mr;
      const tools = mapLive(mr.tools, parent, (d) => ({ ...d, tools: attach(d.tools, record) }));
      if (tools === mr.tools) return mr;
      placed = true;
      return { ...mr, tools };
    });
    if (placed) return next;
    const last = runs.length - 1;
    return runs.map((mr, i) => (i === last ? { ...mr, tools: attach(mr.tools, record) } : mr));
  }
  const fn = patchFor(event);
  if (!fn || !event.run_id) return runs;
  let changed = false;
  const next = runs.map((mr) => {
    const tools = mapLive(mr.tools, event.run_id, fn);
    if (tools === mr.tools) return mr;
    changed = true;
    return { ...mr, tools };
  });
  return changed ? next : runs;
}
