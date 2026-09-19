/**
 * Context-window tracking for the chats that keep their own transcript, split
 * out so `ContextMeter.jsx` exports nothing but the component (same split as
 * `stream.js` / `StreamContext.jsx`).
 *
 * The figures come from the stream itself: every `usage` event carries the
 * per-call prompt size and the window of the model that actually ran (see
 * `ChatStreamCallback.bind_model`), and a `done` event repeats the turn's peak.
 * A window of 0 means the model's limit is unknown, and then the meter draws
 * nothing — an invented ceiling would be worse than none.
 */
import { useCallback, useState } from 'react';

//: Fractions of the window at which the meter stops being informational.
export const CONTEXT_WARN = 0.7;
export const CONTEXT_HIGH = 0.9;

const EMPTY = { used: 0, window: 0, overflow: false };

/** The context figures carried by one stream event, or null if it carries none. */
function contextFrom(ev) {
  if (!ev || typeof ev !== 'object') return null;
  // A streamed per-call usage event…
  if (ev.type === 'usage') {
    return { used: ev.prompt_tokens || 0, window: ev.context_window || 0 };
  }
  // …or the turn's summary, which repeats the peak for a client that missed it.
  const u = ev.usage;
  if (u && (u.context_window || u.context_used)) {
    return { used: u.context_used || 0, window: u.context_window || 0 };
  }
  return null;
}

/** True when this event says the conversation no longer fits the model. */
function isOverflow(ev) {
  return ev?.code === 'context_overflow' || ev?.error_code === 'context_overflow';
}

/**
 * Track context fill across a chat's stream.
 *
 * `observe` takes every event the surface already receives; `reset` goes with
 * whatever clears the conversation, since clearing it is what empties the
 * context. The peak prompt is kept rather than the last one: a turn's final
 * call can be smaller than its largest, and the largest is what has to fit.
 */
export function useContextUsage() {
  const [usage, setUsage] = useState(EMPTY);

  const observe = useCallback((ev) => {
    const ctx = contextFrom(ev);
    const overflow = isOverflow(ev);
    if (!ctx && !overflow) return;
    setUsage((prev) => ({
      used: Math.max(prev.used, ctx?.used || 0),
      window: ctx?.window || prev.window,
      // A call that came back with usage is a prompt the model accepted, so it
      // also clears a warning left over from before the user did something
      // about it (a bigger model, a shorter turn).
      overflow: overflow || (prev.overflow && ev.type !== 'usage'),
    }));
  }, []);

  const reset = useCallback(() => setUsage(EMPTY), []);

  return { usage, observe, reset };
}
