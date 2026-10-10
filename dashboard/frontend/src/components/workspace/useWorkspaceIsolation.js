import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { getWorkspaceIsolation } from '../../api/isolation';
import { patchWorkspaceSummary } from '../../api/workspaceSummary';

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
  // The header badge reads the switch from the workspace summary.
  if (data) patchWorkspaceSummary(workspace, { isolated: Boolean(data.isolated) });
}

function subscribeTo(workspace, onChange) {
  if (!workspace) return () => {};
  const set = listeners.get(workspace) || new Set();
  set.add(onChange);
  listeners.set(workspace, set);
  return () => { set.delete(onChange); };
}

export default function useWorkspaceIsolation(workspace) {
  // The cache is an external store: read it with useSyncExternalStore rather
  // than mirroring it into state from an effect.
  const subscribe = useCallback((onChange) => subscribeTo(workspace, onChange), [workspace]);
  const data = useSyncExternalStore(
    subscribe,
    () => (workspace ? cache.get(workspace) ?? null : null),
  );
  // `reloading` is an explicit reload(); a first fetch shows as `settled`
  // lagging behind the workspace while nothing is cached for it.
  const [reloading, setReloading] = useState(false);
  const [settled, setSettled] = useState(null);
  const [error, setError] = useState('');
  const loading = reloading || Boolean(workspace && !cache.has(workspace) && settled !== workspace);

  // Promise chain rather than try/await: the lint rule cannot tell that no
  // state is set before the first await of an async function with a catch.
  const fetchState = useCallback(() => {
    if (!workspace) return;
    getWorkspaceIsolation(workspace)
      .then(({ data: body }) => {
        setError('');
        setWorkspaceIsolationCache(workspace, body);
      })
      .catch((e) => {
        // Not the owner, the workspace is gone, or the network is down: the
        // badge shows nothing and the tool picker gates nothing rather than
        // blocking on an error the user did not ask to see here.
        setError(e?.response?.data?.detail || e?.message || '');
      })
      .finally(() => {
        setReloading(false);
        setSettled(workspace);
      });
  }, [workspace]);

  const load = useCallback(() => {
    if (!workspace) return;
    setReloading(true);
    setError('');
    fetchState();
  }, [workspace, fetchState]);

  useEffect(() => {
    if (workspace && !cache.has(workspace)) fetchState();
  }, [workspace, fetchState]);

  return { data, loading, error, reload: load };
}
