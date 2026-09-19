import { useEffect, useRef, useState, useCallback } from 'react';
import { StreamContext } from './stream';
import { API_ORIGIN } from '../api';

/*
 * Single multiplexed SSE connection for the whole dashboard.
 *
 * The entire UI holds ONE EventSource to `/api/stream` instead of one connection
 * per stream plus dozens of polling loops. Every event carries a `channel`; this
 * provider fans events out to subscribers and lets detail pages add/remove
 * channel interest (logs, a specific node/container, an active chat session) on
 * the existing connection — no reconnect.
 *
 * The hooks that read this provider live in ./stream.js.
 */

function postChannels(clientId, add, remove) {
  if (!clientId) return;
  fetch(`${API_ORIGIN}/api/stream/${clientId}/channels`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ add, remove }),
  }).catch(() => {});
}

export function StreamProvider({ children }) {
  const [connected, setConnected] = useState(false);
  // Exposed so callers can deliver request-scoped work (e.g. a chat run) over
  // this one connection by passing the id to the server.
  const [clientId, setClientId] = useState(null);
  const clientIdRef = useRef(null);
  // channel → Set<handler>
  const listenersRef = useRef(new Map());
  // dynamic channel → refcount (extra interest beyond server defaults)
  const channelsRef = useRef(new Map());

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

  useEffect(() => {
    let closed = false;
    let retry = null;
    let es = null;

    const connect = () => {
      if (closed) return;
      es = new EventSource(`${API_ORIGIN}/api/stream`);
      es.onmessage = (e) => {
        let ev;
        try { ev = JSON.parse(e.data); } catch { return; }
        if (ev.channel === '_meta') {
          if (ev.type === 'ready') {
            clientIdRef.current = ev.client_id;
            setClientId(ev.client_id);
            setConnected(true);
            // Re-register dynamic channels after a (re)connect.
            const chans = [...channelsRef.current.keys()];
            if (chans.length) postChannels(ev.client_id, chans, []);
          }
          return; // heartbeats and meta are not dispatched
        }
        dispatch(ev);
      };
      es.onerror = () => {
        setConnected(false);
        clientIdRef.current = null;
        setClientId(null);
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
  }, [dispatch]);

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
    <StreamContext.Provider value={{ connected, clientId, on, acquireChannel }}>
      {children}
    </StreamContext.Provider>
  );
}
