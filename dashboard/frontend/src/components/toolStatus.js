/**
 * How one tool call went: running, succeeded or failed. ToolStatusMark draws
 * it; every surface that shows a call reads it through here so they agree.
 */
import { policyVerdict } from './policyVerdict';

const FAILED_JSON = /^\s*\{\s*"ok"\s*:\s*false\b/;
const FAILED_TEXT = /^\s*(ERROR|Error):/;

/**
 * 'running' | 'ok' | 'error' | 'unknown' for one call record. The backend
 * says it in `status` (agents/callbacks/run_statistics.tool_status); a record
 * stored before that is read from its output the same way.
 */
export function toolStatus(entry) {
  if (!entry) return 'unknown';
  if (entry.running) return 'running';
  if (entry.status === 'ok' || entry.status === 'error') return entry.status;
  if (entry.error) return 'error';
  const output = entry.output;
  if (output == null) return 'unknown';
  const text = typeof output === 'string' ? output : JSON.stringify(output);
  if (FAILED_TEXT.test(text) || FAILED_JSON.test(text)) return 'error';
  if (typeof output === 'object' && output?.ok === false) return 'error';
  return 'ok';
}

/** The fields a live tool_end / tool_error event adds to its call's record. */
export function toolOutcome(event) {
  const status = event?.status === 'ok' || event?.status === 'error' ? { status: event.status } : {};
  return { ...policyVerdict(event), ...status };
}
