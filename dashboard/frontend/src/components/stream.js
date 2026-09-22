/**
 * Consumer side of the SSE stream: the context object and the hooks that read
 * it. Kept apart from `StreamContext.jsx` so that file exports nothing but the
 * provider component and Fast Refresh can hot-swap it (same split as
 * `i18n/core.js` / `I18nProvider.jsx`).
 *
 *   useStream()                         → { connected, on, acquireChannel, onRefetch }
 *   useStreamEvent(channel, type, fn)   → fire fn on matching events
 *   useLiveResource(fetchFn, opts)      → initial fetch + debounced refetch
 *   useLiveRefetch(fn, opts)            → refetch only, no initial call
 *   useChannel(channel, fn?)            → subscribe to a dynamic channel
 *
 * `onRefetch(fn)` (from useStream()) registers a callback the provider calls
 * after a gap the stream itself could not fill in (a reconnect that could not
 * resume, or events dropped because a connection fell behind). A page with its
 * own live data can register here as a last-resort full reload; this is
 * separate from useLiveResource/useLiveRefetch, which already refetch off
 * ordinary change events and need no extra wiring for this.
 */
import { createContext, useContext, useEffect, useLayoutEffect, useRef } from 'react';

export const StreamContext = createContext(null);

export function useStream() {
  const ctx = useContext(StreamContext);
  if (!ctx) throw new Error('useStream must be used within <StreamProvider>');
  return ctx;
}

/** Fire `handler` on events for `channel`, optionally filtered by `type`. */
export function useStreamEvent(channel, type, handler) {
  const { on } = useStream();
  const hRef = useRef(handler);
  // Keep the latest callback without re-subscribing. The write lives in a
  // layout effect, not in the render body: a render that never commits must
  // not leave its handler behind, and layout effects run before the
  // subscription effect below.
  useLayoutEffect(() => { hRef.current = handler; });
  useEffect(() => {
    if (!channel) return undefined;
    return on(channel, (ev) => {
      if (!type || ev.type === type) hRef.current?.(ev);
    });
  }, [channel, type, on]);
}

/**
 * Drop-in replacement for `setInterval(fetchFn, …)`.
 * Calls fetchFn once on mount, then again (debounced) whenever a matching
 * change event arrives. `type` is e.g. 'tasks.changed'; `channel` defaults to 'app'.
 */
export function useLiveResource(fetchFn, { type, deps = [], channel = 'app' } = {}) {
  const { on } = useStream();
  const fnRef = useRef(fetchFn);
  // Keep the latest callback without re-subscribing. The write lives in a
  // layout effect, not in the render body: a render that never commits must
  // not leave its handler behind, and layout effects run before the
  // subscription effect below.
  useLayoutEffect(() => { fnRef.current = fetchFn; });
  const timer = useRef(null);
  useEffect(() => {
    fnRef.current?.();
    const trigger = () => {
      if (timer.current) return;
      timer.current = setTimeout(() => { timer.current = null; fnRef.current?.(); }, 300);
    };
    const off = on(channel, (ev) => { if (!type || ev.type === type) trigger(); });
    return () => {
      off();
      if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

/**
 * Refetch-only companion to `useLiveResource`: does NOT fetch on mount (the
 * caller's own effect already loads initial data), it just calls `handler`
 * (debounced) on matching change events. `enabled` mirrors a page's
 * "live updates" toggle. Use when a page already owns its initial-load effect.
 *
 * A page that reloads on several different events passes them as `sources`
 * (`[{ type, channel }, ...]`) instead of calling this hook once per event:
 * one hook means ONE debounce timer, so two events landing together cost one
 * refetch, not one per subscription. `debounceMs` widens that window for a
 * page whose events arrive as a drawn-out burst rather than all at once.
 *
 * `fallbackMs` adds a slow safety-net interval on top of the subscription, for
 * a page whose *detail* has no event of its own: a team's board or a
 * simulation's ticks are published on their own run channel, and only the run
 * starting or finishing reaches the app channel. When that run channel drops,
 * the subscription alone would leave the page frozen. This is a floor, not a
 * poll: keep it at 30s or more, so it costs one request a minute rather than
 * twenty, and let the stream do the real work.
 */
export function useLiveRefetch(
  handler,
  { type, channel = 'app', sources, enabled = true, debounceMs = 300, fallbackMs = 0 } = {},
) {
  const { on } = useStream();
  const hRef = useRef(handler);
  // Keep the latest callback without re-subscribing. The write lives in a
  // layout effect, not in the render body: a render that never commits must
  // not leave its handler behind, and layout effects run before the
  // subscription effect below.
  useLayoutEffect(() => { hRef.current = handler; });
  const timer = useRef(null);
  // Subscriptions are re-read by value so a caller can pass an inline array
  // without re-subscribing on every render.
  const subs = sources?.length ? sources : [{ type, channel }];
  const subsKey = JSON.stringify(subs.map(s => [s.channel || 'app', s.type || '']));
  useEffect(() => {
    if (!enabled) return undefined;
    const trigger = () => {
      if (timer.current) return;
      timer.current = setTimeout(() => { timer.current = null; hRef.current?.(); }, debounceMs);
    };
    const offs = JSON.parse(subsKey).map(([ch, ty]) =>
      on(ch, (ev) => { if (!ty || ev.type === ty) trigger(); }));
    return () => {
      offs.forEach(off => off());
      if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    };
  }, [subsKey, enabled, debounceMs, on]);

  // The safety net, in its own effect so changing it never re-subscribes.
  useEffect(() => {
    if (!enabled || !fallbackMs) return undefined;
    const id = setInterval(() => hRef.current?.(), fallbackMs);
    return () => clearInterval(id);
  }, [enabled, fallbackMs]);
}

/**
 * Subscribe to a dynamic per-resource channel (e.g. `nodes`, `containers`,
 * `logs:node:<id>`, or a chat session id) while mounted. Optional `handler`
 * receives that channel's events.
 */
export function useChannel(channel, handler) {
  const { acquireChannel, on } = useStream();
  const hRef = useRef(handler);
  // Keep the latest callback without re-subscribing. The write lives in a
  // layout effect, not in the render body: a render that never commits must
  // not leave its handler behind, and layout effects run before the
  // subscription effect below.
  useLayoutEffect(() => { hRef.current = handler; });
  useEffect(() => {
    if (!channel) return undefined;
    const release = acquireChannel(channel);
    const off = on(channel, (ev) => hRef.current?.(ev));
    return () => { release(); off(); };
  }, [channel, acquireChannel, on]);
}
