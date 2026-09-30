/**
 * Mapping a failed or empty send to what the transcript shows for it.
 */

import { genId } from '../turnState';

// Turn a failed request into the error bubble appended to the transcript.
// Returns null for an aborted request (the user cancelled, or a new send
// started over it) — there is nothing to show for that.
function mapSendError(err, t) {
  if (err?.name === 'AbortError') return null;
  return {
    id: genId(),
    role: 'agent',
    content: err?.response?.data?.detail || err?.message || t('chat.failedToGetResponse'),
    error: true,
    run_id: null,
  };
}

// The stream ended without ever sending a `done` event (e.g. the connection
// dropped mid-turn). The bubble keeps whatever text it received; this patch
// is only applied when it received none.
function noStreamPatch(t) {
  return { content: t('chat.noStreamedOutput'), error: true };
}

export { mapSendError, noStreamPatch };
