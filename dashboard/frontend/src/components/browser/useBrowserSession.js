import { useCallback, useEffect, useRef, useState } from 'react';
import { getBrowserFrame, sendBrowserInput } from '../../api/browser';
import { errorDetail } from '../toast';

// After a failed frame the poll slows down to this, so a session that is gone
// or a service that is restarting is not asked five times a second.
const BACKOFF_MS = 3000;

const blankFor = (key) => ({ key, frame: null, error: null, status: null, inputError: null });

const hidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden';

/**
 * Frames and input of one browser session.
 *
 *   const { frame, error, inputError, status, loading, send, navigate, refresh } =
 *     useBrowserSession(sessionId, { active, intervalMs });
 *
 * While `active`, the viewport is polled every `intervalMs` (the service takes
 * each frame under the session lock, so a frame asked for during an agent's
 * action simply arrives when the action is done). Polling pauses while the tab
 * is hidden and backs off to 3 s after an error. When not `active` the hook
 * fetches one frame and stops: that is how a finished run shows where its page
 * was left. `frame` is kept across polls, so the last picture stays on screen
 * while the next loads.
 *
 * `send(input)` posts one input (viewport pixels) and fetches a fresh frame
 * right after, so the effect of a click shows without waiting for the poll.
 */
export function useBrowserSession(sessionId, { active = true, intervalMs = 800 } = {}) {
  const [state, setState] = useState(() => blankFor(sessionId));
  const [loading, setLoading] = useState(false);
  const alive = useRef(true);
  const current = state.key === sessionId ? state : blankFor(sessionId);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const fetchFrame = useCallback(async () => {
    if (!sessionId) return false;
    setLoading(true);
    try {
      const { data } = await getBrowserFrame(sessionId);
      if (alive.current) {
        setState((prev) => ({
          key: sessionId, frame: data, error: null, status: 200,
          inputError: prev.key === sessionId ? prev.inputError : null,
        }));
      }
      return true;
    } catch (e) {
      if (alive.current) {
        setState((prev) => ({
          key: sessionId,
          frame: prev.key === sessionId ? prev.frame : null,
          inputError: prev.key === sessionId ? prev.inputError : null,
          error: errorDetail(e) || 'error',
          status: e?.response?.status ?? null,
        }));
      }
      return false;
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return undefined;
    if (!active) {
      fetchFrame();
      return undefined;
    }
    let stopped = false;
    let timer = null;
    const tick = async () => {
      timer = null;
      if (stopped) return;
      if (hidden()) return; // visibilitychange restarts the loop
      const ok = await fetchFrame();
      if (!stopped && !hidden()) timer = setTimeout(tick, ok ? intervalMs : BACKOFF_MS);
    };
    const onVisibility = () => {
      if (!hidden() && timer == null && !stopped) tick();
    };
    tick();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [sessionId, active, intervalMs, fetchFrame]);

  const send = useCallback(async (input) => {
    if (!sessionId) return null;
    try {
      const { data } = await sendBrowserInput(sessionId, input);
      if (alive.current) setState((prev) => (prev.key === sessionId ? { ...prev, inputError: null } : prev));
      fetchFrame();
      return data;
    } catch (e) {
      // Kept apart from the frame's error: the next frame arriving fine must
      // not wipe out why the click or the address was refused.
      if (alive.current) {
        setState((prev) => ({ ...(prev.key === sessionId ? prev : blankFor(sessionId)), key: sessionId,
          inputError: errorDetail(e) || 'error' }));
      }
      fetchFrame();
      return null;
    }
  }, [sessionId, fetchFrame]);

  const navigate = useCallback((url) => send({ kind: 'navigate', url }), [send]);

  return {
    frame: current.frame, error: current.error, inputError: current.inputError,
    status: current.status,
    loading, send, navigate, refresh: fetchFrame,
  };
}

export default useBrowserSession;
