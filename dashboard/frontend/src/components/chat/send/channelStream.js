/**
 * A chat turn delivered over the tab's one SSE connection (/api/stream)
 * instead of a streaming response of its own.
 *
 * `streamChat` holds a POST open for the whole turn. A browser allows about
 * six connections per origin over HTTP/1.1, shared by every tab, and the SSE
 * stream already takes one: with a few conversations answering at once the
 * pool runs out, and everything else on the page (a steering message, a save)
 * waits behind them. Here the POST (/api/chat/stream-sse) returns at once and
 * the turn's events arrive on `chat:<conversation id>`, which this tab follows
 * anyway, so any number of turns share one connection.
 *
 * Same contract as `streamChat`: `onEvent` gets the turn's events, the promise
 * settles when the turn is over (`chat_stream_end`), and an aborted `signal`
 * rejects it with an AbortError. The channel also carries other turns of the
 * same conversation (another tab, Telegram), saves, and the last events of a
 * turn this tab stopped; `client_turn_id` (stamped by chat/broadcast.py) is
 * what picks out this one.
 */
import { startChatOverSSE } from '../../../api';

/** The abort reason for letting go of a turn without stopping it (the Chat
 *  page is going away): nothing is listened to after it. */
export const DETACHED = 'detached';

/** How long a stopped turn is still listened to for its run id, so the run can
 *  be stopped when the stop came before the server said which run it was. */
export const LATE_RUN_WINDOW_MS = 120000;

function abortError() {
  try {
    return new DOMException('The turn was stopped', 'AbortError');
  } catch {
    const err = new Error('The turn was stopped');
    err.name = 'AbortError';
    return err;
  }
}

/**
 * @param {object} args
 * @param {object} args.body the request `streamChat` would post; must carry
 *   `conversation_id`, `client_id` and `client_turn_id`.
 * @param {Function} args.onEvent called with each event of this turn.
 * @param {AbortSignal} [args.signal] stops following the turn.
 * @param {{on: Function, acquireChannel: Function}} args.stream the page's
 *   stream context (components/stream.js).
 * @param {Function} [args.onLateRun] called with a run id that arrives after
 *   the turn was aborted, so the caller can stop that run.
 * @returns {Promise<void>}
 */
export function streamChatOverChannel({ body, onEvent, signal, stream, onLateRun }) {
  const channel = `chat:${body.conversation_id}`;
  const turnKey = body.client_turn_id;
  return new Promise((resolve, reject) => {
    let settled = false;
    let aborted = false;
    let lateTimer = null;
    let off = null;
    let release = null;

    const unsubscribe = () => {
      if (lateTimer) { clearTimeout(lateTimer); lateTimer = null; }
      off?.();
      release?.();
      off = null;
      release = null;
    };
    const settle = (err) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener('abort', onAbort);
      if (err) reject(err); else resolve();
    };
    function onAbort() {
      aborted = true;
      settle(abortError());
      if (signal?.reason === DETACHED) unsubscribe();
      else lateTimer = setTimeout(unsubscribe, LATE_RUN_WINDOW_MS);
    }

    // Subscribed before the POST so no early event is missed; the server
    // subscribes this client too, before it starts the turn.
    off = stream.on(channel, (event) => {
      if (!event || event.client_turn_id !== turnKey) return;
      if (event.type === 'chat_stream_end') {
        unsubscribe();
        settle();
        return;
      }
      if (aborted) {
        if (event.type === 'meta' && event.run_id) onLateRun?.(event.run_id);
        return;
      }
      if (event.type === 'turn_start') return;
      onEvent(event);
    });
    release = stream.acquireChannel(channel);

    if (signal?.aborted) { onAbort(); return; }
    signal?.addEventListener('abort', onAbort);

    startChatOverSSE(body).catch((err) => {
      unsubscribe();
      const detail = err?.response?.data?.detail;
      const message = (detail && (detail.message || (typeof detail === 'string' ? detail : ''))) || err?.message;
      settle(new Error(message || 'Failed to start the turn'));
    });
  });
}

export default streamChatOverChannel;
