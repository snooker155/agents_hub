import { useCallback, useEffect, useState } from 'react';
import { getWorkspaceIsolation } from '../../api/isolation';

/**
 * A workspace's isolation state (common/isolation.py `state()`), shared by
 * three consumers that may be mounted at once: the Isolation settings
 * section, the "Isolated" badge next to the workspace name, and the agent
 * editor's tool picker. A small module level cache with a subscriber list
 * keeps them in sync: the section calls `setWorkspaceIsolationCache` after a
 * successful PUT, and every other mounted hook for that workspace re-renders
 * with the fresh value instead of waiting for a refetch.
 *
 * A failed request leaves `data` at `null` (so the badge shows nothing,
 * per contract, and the tool picker gates nothing) rather than throwing.
 */
const cache = new Map(); // workspace -> state body
const listeners = new Map(); // workspace -> Set<() => void>

function notify(workspace) {
  (listeners.get(workspace) || new Set()).forEach((fn) => fn());
}

/** Called after a PUT so every other mounted hook for this workspace updates
 * without a round trip. */
export function setWorkspaceIsolationCache(workspace, data) {
  if (!workspace) return;
  cache.set(workspace, data);
  notify(workspace);
}

export default function useWorkspaceIsolation(workspace) {
  const [data, setData] = useState(() => (workspace ? cache.get(workspace) ?? null : null));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    if (!workspace) { setData(null); return; }
    setLoading(true);
    setError('');
    try {
      const { data: body } = await getWorkspaceIsolation(workspace);
      setWorkspaceIsolationCache(workspace, body);
    } catch (e) {
      // Not the owner, the workspace is gone, or the network is down: the
      // badge shows nothing and the tool picker gates nothing rather than
      // blocking on an error the user did not ask to see here.
      setError(e?.response?.data?.detail || e?.message || '');
    } finally {
      setLoading(false);
    }
  }, [workspace]);

  useEffect(() => {
    if (!workspace) { setData(null); return undefined; }
    setData(cache.get(workspace) ?? null);
    const set = listeners.get(workspace) || new Set();
    const onChange = () => setData(cache.get(workspace) ?? null);
    set.add(onChange);
    listeners.set(workspace, set);
    if (!cache.has(workspace)) load();
    return () => { set.delete(onChange); };
  }, [workspace, load]);

  return { data, loading, error, reload: load };
}
