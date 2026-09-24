/**
 * The shared job queue behind JobProgress.jsx: split out the same way
 * components/toast.js is split from ToastProvider.jsx, so the component file
 * exports nothing but a component and fast refresh keeps working.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { getLocalJobs } from '../../api/localModels';

// Jobs that still need another poll. Anything else (done, error) is final.
export const ACTIVE_JOB_STATUSES = new Set(['queued', 'running']);
const POLL_MS = 1500;

export function humanBytes(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = Number(n);
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

/**
 * Loads GET /models/local/jobs and, while any job is queued or running,
 * reschedules itself every 1.5s; stops on its own once none are. A single
 * timer is kept (never two overlapping polls), and it is cleared on unmount,
 * so this is safe to leave mounted for as long as the Local tab is.
 *
 * Shared by OllamaSection (pulls) and RuntimeSection (downloads): one job
 * queue, one poll, both sections watch the same list for their own kind.
 */
export function useLocalJobs() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const timerRef = useRef(null);
  const mountedRef = useRef(true);

  const load = useCallback(async () => {
    try {
      const { data } = await getLocalJobs();
      if (mountedRef.current) setJobs(data?.jobs || []);
    } catch {
      // Best-effort: keep showing the last known list rather than clearing it.
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    load();
    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [load]);

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (!jobs.some((j) => ACTIVE_JOB_STATUSES.has(j.status))) return undefined;
    timerRef.current = setTimeout(() => { load(); }, POLL_MS);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [jobs, load]);

  return { jobs, loading, reload: load };
}
