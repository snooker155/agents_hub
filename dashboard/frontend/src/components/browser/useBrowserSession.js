import { useCallback, useEffect, useRef, useState } from 'react';
import { getBrowserFrame, sendBrowserInput, browserStreamUrl } from '../../api/browser';
import { errorDetail } from '../toast';

// After a failed frame the poll slows down to this, so a session that is gone
// or a service that is restarting is not asked five times a second. A frame
// stream that drops after it worked reconnects after the same pause.
const BACKOFF_MS = 3000;

const blankFor = (key) => ({ key, frame: null, error: null, status: null, inputError: null, live: null });

const hidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden';

const canStream = () => typeof WebSocket !== 'undefined' && typeof browserStreamUrl === 'function';

/**
 * Frames and input of one browser session.
 *
 *   const { frame, error, inputError, status, live, loading, send, navigate, refresh } =
 *     useBrowserSession(sessionId, { active, intervalMs, transport });
 *
 * While `active`, frames are pushed over the hub's WebSocket relay of the
 * service's screencast (`live` is 'ws'): a frame arrives when the page paints,
 * not on a timer. When the socket cannot be opened, or closes before it sent
 * a frame, the hook falls back to polling `/frame` every `intervalMs` (`live`
 * is 'poll') and stays there for that session; a socket that drops after it
 * worked reconnects after a pause. `transport: 'poll'` skips the socket. The
 * frame is kept while the next one loads, and everything pauses while the
 * tab is hidden. When not `active` the hook fetches one frame and stops: how
 * a finished run shows where its page was left.
 *
 * `send(input)` posts one input (viewport pixels); on the polling path it
 * fetches a fresh frame right after, on the stream the next paint shows it.
 */
export function useBrowserSession(sessionId, { active = true, intervalMs = 800, transport = 'auto' } = {}) {
  const [state, setState] = useState(() => blankFor(sessionId));
  const [loading, setLoading] = useState(false);
  const alive = useRef(true);
  // Sessions whose stream failed before a frame: polled from then on.
  const noStream = useRef(new Set());
  const liveRef = useRef(null);
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
          key: sessionId, frame: data, error: null, status: 200, live: liveRef.current,
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
          live: liveRef.current,
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
      liveRef.current = null;
      fetchFrame();
      return undefined;
    }
    let stopped = false;
    let timer = null;
    let socket = null;

    const stopAll = () => {
      if (timer) { clearTimeout(timer); timer = null; }
      if (socket) {
        const s = socket;
        socket = null;
        s.onmessage = null; s.onclose = null; s.onerror = null;
        try { s.close(); } catch { /* already closed */ }
      }
    };

    const poll = async () => {
      timer = null;
      if (stopped || hidden()) return;
      const ok = await fetchFrame();
      if (!stopped && !hidden()) timer = setTimeout(poll, ok ? intervalMs : BACKOFF_MS);
    };

    const startPolling = () => {
      liveRef.current = 'poll';
      setState((prev) => (prev.key === sessionId ? { ...prev, live: 'poll' } : { ...blankFor(sessionId), live: 'poll' }));
      poll();
    };

    const startStream = () => {
      let gotFrame = false;
      let ws;
      try {
        ws = new WebSocket(browserStreamUrl(sessionId));
      } catch {
        noStream.current.add(sessionId);
        startPolling();
        return;
      }
      socket = ws;
      liveRef.current = 'ws';
      ws.onmessage = (event) => {
        let msg;
        try { msg = JSON.parse(event.data); } catch { return; }
        if (!msg || typeof msg !== 'object') return;
        if (msg.type === 'frame') {
          gotFrame = true;
          setLoading(false);
          setState((prev) => ({
            key: sessionId, frame: msg, error: null, status: 200, live: 'ws',
            inputError: prev.key === sessionId ? prev.inputError : null,
          }));
        } else if (msg.type === 'keepalive') {
          setState((prev) => (prev.key === sessionId && prev.frame
            ? { ...prev, frame: { ...prev.frame, controlled_by: msg.controlled_by } }
            : prev));
        } else if (msg.type === 'error') {
          // The hub could not reach the service's stream: polling still can.
          gotFrame = false;
        }
      };
      ws.onerror = () => {};
      ws.onclose = () => {
        if (socket !== ws) return;
        socket = null;
        if (stopped) return;
        if (!gotFrame) {
          noStream.current.add(sessionId);
          startPolling();
        } else {
          timer = setTimeout(() => { timer = null; if (!stopped && !hidden()) start(); }, BACKOFF_MS);
        }
      };
      setLoading(true);
    };

    const start = () => {
      if (stopped || hidden()) return;
      if (transport !== 'poll' && canStream() && !noStream.current.has(sessionId)) startStream();
      else startPolling();
    };

    const onVisibility = () => {
      if (hidden()) stopAll();
      else if (!socket && timer == null && !stopped) start();
    };
    start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      stopped = true;
      stopAll();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [sessionId, active, intervalMs, transport, fetchFrame]);

  const send = useCallback(async (input) => {
    if (!sessionId) return null;
    try {
      const { data } = await sendBrowserInput(sessionId, input);
      if (alive.current) setState((prev) => (prev.key === sessionId ? { ...prev, inputError: null } : prev));
      if (liveRef.current !== 'ws') fetchFrame();
      return data;
    } catch (e) {
      // Kept apart from the frame's error: the next frame arriving fine must
      // not wipe out why the click or the address was refused.
      if (alive.current) {
        setState((prev) => ({ ...(prev.key === sessionId ? prev : blankFor(sessionId)), key: sessionId,
          inputError: errorDetail(e) || 'error' }));
      }
      if (liveRef.current !== 'ws') fetchFrame();
      return null;
    }
  }, [sessionId, fetchFrame]);

  const navigate = useCallback((url) => send({ kind: 'navigate', url }), [send]);

  return {
    frame: current.frame, error: current.error, inputError: current.inputError,
    status: current.status, live: current.live,
    loading, send, navigate, refresh: fetchFrame,
  };
}

export default useBrowserSession;
