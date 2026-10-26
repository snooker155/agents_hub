/**
 * The steps of the member turns still running, read every few seconds.
 *
 * A member turn runs outside any chat stream (teams/runner.py `_run_member`),
 * so what it does reaches the page only through its run's trail (GET
 * /messages/{id}/trail). One poll feeds both the run log and the member's
 * reply being written in the run's chat, so the two show the same steps.
 * A finished turn's trail does not change and is read once, through
 * chat/runTrail.js.
 */
import { useEffect, useState } from 'react';
import { getRunTrail } from '../../api';

const POLL_MS = 3000;

// A tool call the turn has started but not finished has no output yet in
// the trail: marked running, so the reply being written says what it does.
function withRunningTool(trail) {
  const timeline = trail?.timeline || [];
  const last = timeline[timeline.length - 1];
  if (last?.type !== 'tool' || last.output != null) return trail;
  return { ...trail, timeline: [...timeline.slice(0, -1), { ...last, running: true }] };
}

/** `{run_id: trail}` for the `runIds` of turns in flight, refreshed while they run. */
export function useLiveTrails(runIds) {
  const [trails, setTrails] = useState({});
  const key = runIds.join('|');
  useEffect(() => {
    const ids = key ? key.split('|') : [];
    if (!ids.length) return undefined;
    let alive = true;
    const load = () => ids.forEach((id) => {
      getRunTrail(id)
        .then((r) => { if (alive && r.data) setTrails((prev) => ({ ...prev, [id]: withRunningTool(r.data) })); })
        .catch(() => { /* transient: the next poll tries again */ });
    });
    load();
    const timer = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(timer); };
  }, [key]);
  return trails;
}
