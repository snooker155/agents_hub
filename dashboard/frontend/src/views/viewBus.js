// Tiny in-page pub/sub bus for linked views (§14): views sharing a `link`
// declaration exchange the timebase (t + frame aggregates) and the selection
// without any coupling between renderers. Channels are plain names —
// `timebase:<name>` carries {t, aggregates}; `selection` carries the selected
// id — and late subscribers get the last value immediately.

const channels = new Map();

function channel(name) {
  let ch = channels.get(name);
  if (!ch) { ch = { last: undefined, subs: new Set() }; channels.set(name, ch); }
  return ch;
}

export function publish(name, payload) {
  const ch = channel(name);
  ch.last = payload;
  for (const cb of ch.subs) { try { cb(payload); } catch { /* subscriber's problem */ } }
}

export function subscribe(name, cb) {
  const ch = channel(name);
  ch.subs.add(cb);
  if (ch.last !== undefined) { try { cb(ch.last); } catch { /* noop */ } }
  return () => ch.subs.delete(cb);
}
