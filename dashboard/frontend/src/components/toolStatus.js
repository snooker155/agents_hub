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

/**
 * What went wrong in a failed call, as one message: the `error` of a
 * `{"ok": false, "error": ...}` result (whole or cut short in a run log), the
 * text after "ERROR:" of a call that raised, or the record's own `error`.
 * Empty for a call that did not fail.
 */
export function toolErrorText(entry) {
  if (toolStatus(entry) !== 'error') return '';
  if (entry.error) return String(entry.error);
  const output = entry.output;
  if (output && typeof output === 'object') return String(output.error || JSON.stringify(output));
  const text = String(output ?? '').trim();
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === 'object' && parsed.error) return String(parsed.error);
  } catch { /* cut short in the log, or not JSON */ }
  const cut = /"error"\s*:\s*"((?:[^"\\]|\\.)*)/.exec(text);
  if (cut) {
    try { return JSON.parse(`"${cut[1]}"`); } catch { return cut[1]; }
  }
  return text.replace(/^(ERROR|Error):\s*/, '');
}
