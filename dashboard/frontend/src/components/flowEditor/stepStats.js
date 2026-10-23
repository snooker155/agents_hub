/**
 * The counts a flow node's folded steps show on their header: tool calls
 * (the think/plan pseudo-tools are thoughts, not calls), the ones that failed,
 * and thoughts. The same counts the backend writes on a node's log line
 * (chat/flow_driver.py `_node_step_stats`) and a run trail carries
 * (routes/messages.py build_run_trail), computed here for a live turn.
 */
import { toolStatus } from '../toolStatus';

const THOUGHT_TOOLS = new Set(['think', 'plan']);

export function trailStats(timeline) {
  const stats = { tools: 0, errors: 0, thoughts: 0, files: 0 };
  for (const e of timeline || []) {
    if (e?.type === 'tool' && !THOUGHT_TOOLS.has(e.tool)) {
      stats.tools += 1;
      if (toolStatus(e) === 'error') stats.errors += 1;
    } else if (e?.type === 'reasoning') {
      stats.thoughts += 1;
    } else if (e?.type === 'artifact') {
      stats.files += 1;
    } else if (e?.type === 'delegation') {
      stats.tools += 1;
      if (e.ok === false) stats.errors += 1;
    }
  }
  return stats;
}
