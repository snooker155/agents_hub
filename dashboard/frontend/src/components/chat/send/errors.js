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

// The person stopped the turn: the bubble it was writing keeps what it got
// and says it was stopped. Stored with the transcript, so the mark is still
// there after the next turn and after a reload. A turn stopped before it drew
// a bubble (a flow before its first node) gets one of its own.
function markStopped(messages, bubbleId, runId = null) {
  if (bubbleId && messages.some((m) => m.id === bubbleId)) {
    return messages.map((m) => (m.id === bubbleId ? { ...m, stopped: true, running_tool: null } : m));
  }
  return [...messages, { id: genId(), role: 'agent', content: '', stopped: true, error: false, run_id: runId }];
}

export { mapSendError, markStopped, noStreamPatch };
