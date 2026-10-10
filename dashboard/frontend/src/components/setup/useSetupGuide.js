/**
 * The guided setup (docs/assistant.md "Guided setup", common/setup_guide.py):
 * the hook behind the onboarding modal, the Assistant page's Setup tab and
 * the header's own pill. Each holds its own copy; the event below keeps them
 * in step, and the slow polling keeps three copies cheap.
 *
 * Refreshes at once on the `ah:setup-guide` window event, which `act()` fires
 * after every change and which the Assistant page also fires once a
 * `setup_guide`, `setup_step` or `propose_connection` tool ends. Besides
 * that it polls: every 4s while a step's own work (a voice download) is
 * starting or submitted, else every 20s while the guide is active and the
 * tab is visible. Idle (no guide running, no work in progress) it does not
 * poll at all.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { getSetupGuide, setupGuideAction } from '../../api/setupGuide';
import { isTourDone } from '../docs/tourRunner';

const REFRESH_EVENT = 'ah:setup-guide';
const WORKING_POLL_MS = 4000;
const IDLE_POLL_MS = 20000;

/** Fired after anything that may have moved the guide along. */
export function dispatchSetupGuideRefresh() {
  try { window.dispatchEvent(new Event(REFRESH_EVENT)); } catch { /* no window (a test outside jsdom) */ }
}

/** A step's title or why, translated by id, falling back to the server's own English. */
export function setupStepText(t, step, field) {
  return t(`setupGuide.steps.${step.id}.${field}`, { defaultValue: step[field] });
}

export function useSetupGuide() {
  const [guide, setGuide] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const mountedRef = useRef(true);
  // Set again on every mount: StrictMode runs the cleanup once in between.
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const { data } = await getSetupGuide({ tourDone: isTourDone() });
      if (mountedRef.current) { setGuide(data); setError(''); }
    } catch (e) {
      if (mountedRef.current) setError(e?.response?.data?.detail || e.message || 'failed');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- refresh is shared with the poll timer and the refresh event; it sets state after its await
    refresh();
  }, [refresh]);

  useEffect(() => {
    window.addEventListener(REFRESH_EVENT, refresh);
    return () => window.removeEventListener(REFRESH_EVENT, refresh);
  }, [refresh]);

  // Idle once nothing is running: a finished or dismissed guide costs nothing.
  const working = guide?.work?.phase === 'starting' || guide?.work?.phase === 'submitted';
  const active = Boolean(guide?.active);
  useEffect(() => {
    if (!working && !active) return undefined;
    const ms = working ? WORKING_POLL_MS : IDLE_POLL_MS;
    const id = setInterval(() => {
      if (document.visibilityState === 'visible') refresh();
    }, ms);
    return () => clearInterval(id);
  }, [active, working, refresh]);

  const act = useCallback(async (action, opts = {}) => {
    const { data } = await setupGuideAction(action, { ...opts, tourDone: isTourDone() });
    if (mountedRef.current) setGuide(data);
    dispatchSetupGuideRefresh();
    return data;
  }, []);

  return { guide, loading, error, refresh, act };
}

export default useSetupGuide;
