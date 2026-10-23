/**
 * The run log as rows: one row per agent node, not two. The node's
 * `agent_start` line is the row while it works; when the node ends, its
 * `agent_finish` / `agent_error` / `agent_stopped` line takes that row's
 * place instead of being added below, so "Running X" turns into
 * "Completed X" where it stood. The raw log is unchanged; only the Logs panel
 * reads it this way (node statuses and History still use every event).
 *
 * A row still open when the run ends (stopped before the node said so) is
 * closed as stopped, so nothing keeps spinning on a run that is over.
 */
import { settleTurn } from '../chat/turnEvents';

const END_TYPES = new Set(['agent_finish', 'agent_error', 'agent_stopped']);
const FLOW_END_TYPES = new Set(['flow_finish', 'flow_stopped']);

// One node pass: its run when the line names one, else the node within its
// conversation or run.
function nodeKey(log) {
  return `${log.run_group || ''}|${log.node_id || ''}`;
}

export function logRows(logs) {
  const rows = [];
  // node key -> index of its still open row
  const open = new Map();
  for (const log of logs || []) {
    if (log.type === 'flow_state') continue;
    if (log.type === 'agent_start' && log.node_id) {
      open.set(nodeKey(log), rows.length);
      rows.push(log);
      continue;
    }
    if (END_TYPES.has(log.type) && log.node_id && open.has(nodeKey(log))) {
      const at = open.get(nodeKey(log));
      open.delete(nodeKey(log));
      const start = rows[at];
      rows[at] = {
        ...log,
        // The row keeps the time the node started, and what it was given
        // when the end line does not repeat it.
        timestamp: start.timestamp || log.timestamp,
        input: log.input || start.input,
        run_id: log.run_id || start.run_id,
      };
      continue;
    }
    if (FLOW_END_TYPES.has(log.type)) {
      for (const at of open.values()) {
        const start = rows[at];
        rows[at] = {
          ...start,
          type: 'agent_stopped',
          content: '',
          ...(start.turn ? { turn: settleTurn(start.turn) } : {}),
        };
      }
      open.clear();
    }
    rows.push(log);
  }
  return rows;
}
