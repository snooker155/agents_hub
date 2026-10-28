/**
 * A finished run's steps, fetched from the server (`GET /messages/{id}/trail`,
 * routes/messages.py build_run_trail): the timeline a live turn builds with
 * turnEvents.js, for a turn that was not watched live (a flow chat reopened
 * from History, a node of a flow run in its log).
 *
 * A finished run does not change, so each one is fetched once per page load
 * and shared by every surface that shows it.
 */
import { useEffect, useMemo, useState } from 'react';
import { getRunTrail } from '../../api';

const cache = new Map();

/** The trail of `runId`: `{timeline, files, stats}`, or null when it has none. */
export function loadRunTrail(runId) {
  const key = String(runId || '');
  if (!key) return Promise.resolve(null);
  if (!cache.has(key)) {
    const pending = getRunTrail(key)
      .then((r) => r.data || null)
      .catch(() => {
        // A failed fetch is not remembered: the next look tries again.
        cache.delete(key);
        return null;
      });
    cache.set(key, pending);
  }
  return cache.get(key);
}

/** The trail of `runId` once loaded (null until then), fetched only when `enabled`. */
export function useRunTrail(runId, enabled = true) {
  const [loaded, setLoaded] = useState({ runId: null, trail: null });
  useEffect(() => {
    if (!enabled || !runId) return undefined;
    let live = true;
    loadRunTrail(runId).then((trail) => { if (live) setLoaded({ runId, trail }); });
    return () => { live = false; };
  }, [runId, enabled]);
  return loaded.runId === runId ? loaded.trail : null;
}

/**
 * `messages` with the steps of every finished agent message that has a run
 * but no timeline of its own filled in from its trail. Only fetched while
 * `enabled` (the Build view, where the steps are drawn).
 */
export function useMessagesWithTrails(messages, enabled) {
  const [trails, setTrails] = useState({});
  const wanted = useMemo(
    () => (enabled ? messages : [])
      .filter((m) => m.role !== 'user' && m.run_id && !(m.timeline && m.timeline.length))
      .map((m) => String(m.run_id)),
    [messages, enabled],
  );
  useEffect(() => {
    let live = true;
    for (const runId of wanted) {
      if (trails[runId] !== undefined) continue;
      loadRunTrail(runId).then((trail) => {
        if (live) setTrails((prev) => ({ ...prev, [runId]: trail }));
      });
    }
    return () => { live = false; };
  }, [wanted, trails]);
  return useMemo(() => messages.map((m) => {
    const trail = m.run_id ? trails[String(m.run_id)] : null;
    if (!trail || (m.timeline && m.timeline.length)) return m;
    return {
      ...m,
      timeline: trail.timeline || [],
      files: m.files || trail.files || [],
      ...(!m.entities && trail.entities ? { entities: trail.entities } : {}),
    };
  }), [messages, trails]);
}
