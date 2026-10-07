import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * The turns this tab is sending, at most one per conversation.
 *
 * The page used to have one `loading` flag and one AbortController: while a
 * conversation was being answered every other one was "busy" too, so a message
 * typed into a second chat waited in the queue for the first chat's turn to
 * end. Keyed by conversation, several conversations answer at once, each with
 * its own stop and its own steering, and leaving a chat does not touch its
 * turn.
 *
 * `turns` (state, for rendering) maps a conversation id to `{runId,
 * sessionId}`; the controllers live in a ref beside it, read by `get` and
 * `isRunning`, which stay current between renders.
 *
 * A turn can carry a `detach` callback (`begin`'s third argument): when the
 * page goes away every turn is detached, which lets go of it without stopping
 * it.
 */
export function useChatTurns() {
  const [turns, setTurns] = useState({});
  const ref = useRef({});

  const begin = useCallback((convId, ctrl, { detach = null } = {}) => {
    ref.current[convId] = { ctrl, detach, runId: null, sessionId: null };
    setTurns((prev) => ({ ...prev, [convId]: { runId: null, sessionId: null } }));
  }, []);

  /** Record what the turn has said about itself (its run, its session). */
  const note = useCallback((convId, patch) => {
    const turn = ref.current[convId];
    if (!turn) return;
    Object.assign(turn, patch);
    setTurns((prev) => (prev[convId] ? { ...prev, [convId]: { ...prev[convId], ...patch } } : prev));
  }, []);

  /** The turn is over. With `ctrl`, only if it is still that send's turn: a
   *  stopped turn's send finishing late must not end the next one. */
  const end = useCallback((convId, ctrl = null) => {
    const turn = ref.current[convId];
    if (!turn || (ctrl && turn.ctrl !== ctrl)) return;
    delete ref.current[convId];
    setTurns((prev) => {
      if (!prev[convId]) return prev;
      const next = { ...prev };
      delete next[convId];
      return next;
    });
  }, []);

  useEffect(() => {
    const live = ref.current;
    return () => {
      for (const turn of Object.values(live)) turn.detach?.();
    };
  }, []);

  const get = useCallback((convId) => (convId ? ref.current[convId] || null : null), []);
  const isRunning = useCallback((convId) => Boolean(convId && ref.current[convId]), []);

  return { turns, begin, note, end, get, isRunning };
}

export default useChatTurns;
