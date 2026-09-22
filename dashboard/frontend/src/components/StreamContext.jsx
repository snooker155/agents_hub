import { useEffect, useRef, useState, useCallback } from 'react';
import { StreamContext } from './stream';
import { API_ORIGIN, getApiToken } from '../api';

/*
 * Single multiplexed SSE connection for the whole dashboard.
 *
 * The entire UI holds ONE EventSource to `/api/stream` instead of one connection
 * per stream plus dozens of polling loops. Every event carries a `channel`; this
 * provider fans events out to subscribers and lets detail pages add/remove
 * channel interest (logs, a specific node/container, an active chat session) on
 * the existing connection — no reconnect.
 *
 * Reconnecting: the browser's EventSource retries on its own and remembers the
 * last `id:` line it saw (sent back as Last-Event-ID). This provider also sends
 * back its previous client id (`?client=`), so the backend can resume the same
 * client and replay whatever it missed instead of starting over. When the
 * backend cannot resume (`ready` with `resumed: false`) or says events were
 * dropped for being too slow to keep up with (a `lagged` meta event), the
 * stream itself has a gap that replay cannot fill, so every page with live
 * data needs to refetch. `onRefetch` lets pages register for that.
 *
 * The hooks that read this provider live in ./stream.js.
 */

function postChannels(clientId, add, remove) {
  if (!clientId) return;
  const token = getApiToken();
  fetch(`${API_ORIGIN}/api/stream/${clientId}/channels`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ add, remove }),
  }).catch(() => {});
}

export function StreamProvider({ children }) {
  const [connected, setConnected] = useState(false);
  // Exposed so callers can deliver request-scoped work (e.g. a chat run) over
  // this one connection by passing the id to the server.
  const [clientId, setClientId] = useState(null);
  const clientIdRef = useRef(null);
  // The last numbered event id seen (from the SSE frame's `id:` line, surfaced
  // by the browser as MessageEvent.lastEventId). Carried into the next
  // reconnect's URL so the backend can replay what was missed — EventSource
  // cannot set a Last-Event-ID header itself, only the browser's own silent
  // retries do that, and this provider manages reconnects manually (see below).
  const lastEventIdRef = useRef(null);
  // Set right before a reconnect attempt, so the `ready` that follows can tell
  // "just opened" (nothing to refetch) from "was reconnecting" (refetch unless
  // the backend actually resumed and replayed what was missed).
  const reconnectingRef = useRef(false);
  // channel → Set<handler>
  const listenersRef = useRef(new Map());
  // dynamic channel → refcount (extra interest beyond server defaults)
  const channelsRef = useRef(new Map());
  // Callbacks pages register to reload their own data after a gap the stream
  // itself cannot fill in (a failed resume, or events dropped for lagging).
  const refetchersRef = useRef(new Set());

  const dispatch = useCallback((event) => {
    const handlers = listenersRef.current.get(event.channel);
    if (!handlers) return;
    handlers.forEach((fn) => {
      try {
        fn(event);
      } catch (e) {
        // One bad subscriber must not stop the rest, but a throwing handler is a
        // bug in that component — log it instead of losing it. Not a toast: the
        // user cannot act on it and a broken stream would fire it repeatedly.
        console.error(`Stream handler for "${event.channel}" threw:`, e);
      }
    });
  }, []);

  const refetchAll = useCallback(() => {
    refetchersRef.current.forEach((fn) => {
      try {
        fn();
      } catch (e) {
        console.error('Stream refetch callback threw:', e);
      }
    });
  }, []);

  const onRefetch = useCallback((fn) => {
    refetchersRef.current.add(fn);
    return () => refetchersRef.current.delete(fn);
  }, []);

  useEffect(() => {
    let closed = false;
    let retry = null;
    let es = null;

    const connect = () => {
      if (closed) return;
      // EventSource cannot set request headers, so an operator token (when
      // configured, see common/auth.py) has to travel as a query parameter —
      // the one form the backend's auth guard accepts besides a header. The
      // same limitation is why the last event id travels as `since` rather
      // than a real Last-Event-ID header: only the browser's own silent retry
      // can set that, and this provider replaces the connection itself instead.
      const token = getApiToken();
      const params = [];
      if (token) params.push(`token=${encodeURIComponent(token)}`);
      if (clientIdRef.current) params.push(`client=${encodeURIComponent(clientIdRef.current)}`);
      if (lastEventIdRef.current != null) params.push(`since=${encodeURIComponent(lastEventIdRef.current)}`);
      const url = `${API_ORIGIN}/api/stream${params.length ? `?${params.join('&')}` : ''}`;
      es = new EventSource(url);
      es.onmessage = (e) => {
        let ev;
        try { ev = JSON.parse(e.data); } catch { return; }
        if (e.lastEventId) {
          const id = Number(e.lastEventId);
          if (Number.isFinite(id)) lastEventIdRef.current = id;
        }
        if (ev.channel === '_meta') {
          if (ev.type === 'ready') {
            clientIdRef.current = ev.client_id;
            setClientId(ev.client_id);
            setConnected(true);
            // Re-register dynamic channels after a (re)connect. Harmless when
            // resumed: the backend already kept the same channel set.
            const chans = [...channelsRef.current.keys()];
            if (chans.length) postChannels(ev.client_id, chans, []);
            // A reconnect that could not be resumed, or one that starts fresh
            // with no prior history, has a gap the replay cannot fill.
            if (!ev.resumed) {
              lastEventIdRef.current = null;
              if (reconnectingRef.current) refetchAll();
            }
            reconnectingRef.current = false;
          } else if (ev.type === 'lagged') {
            // The backend had to drop events for this connection because it
            // fell behind; whatever a page built from the stream since may be
            // incomplete.
            refetchAll();
          }
          return; // heartbeats and meta are not dispatched further
        }
        dispatch(ev);
      };
      es.onerror = () => {
        setConnected(false);
        reconnectingRef.current = true;
        es?.close();
        retry = setTimeout(connect, 3000);
      };
    };
    connect();

    return () => {
      closed = true;
      es?.close();
      if (retry) clearTimeout(retry);
    };
  }, [dispatch, refetchAll]);

  const on = useCallback((channel, handler) => {
    let set = listenersRef.current.get(channel);
    if (!set) { set = new Set(); listenersRef.current.set(channel, set); }
    set.add(handler);
    return () => {
      set.delete(handler);
      if (!set.size) listenersRef.current.delete(channel);
    };
  }, []);

  const acquireChannel = useCallback((channel) => {
    const m = channelsRef.current;
    const next = (m.get(channel) || 0) + 1;
    m.set(channel, next);
    if (next === 1) postChannels(clientIdRef.current, [channel], []);
    return () => {
      const left = (m.get(channel) || 1) - 1;
      if (left <= 0) {
        m.delete(channel);
        postChannels(clientIdRef.current, [], [channel]);
      } else {
        m.set(channel, left);
      }
    };
  }, []);

  return (
    <StreamContext.Provider value={{ connected, clientId, on, acquireChannel, onRefetch }}>
      {children}
    </StreamContext.Provider>
  );
}
