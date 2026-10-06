import { useCallback, useEffect, useSyncExternalStore } from 'react';
import {
  cachedWorkspaceSummary, loadWorkspaceSummary, subscribeWorkspaceSummary,
} from '../../api/workspaceSummary';

/**
 * The selected workspace's summary (api/workspaceSummary.js), shared by
 * every mounted consumer: they all ride the same request and re-render when a
 * write patches it. Null until it arrives and when the request fails, so a
 * consumer shows nothing rather than an error it was not asked about.
 */
export default function useWorkspaceSummary(workspace) {
  const subscribe = useCallback(
    (onChange) => (workspace ? subscribeWorkspaceSummary(workspace, onChange) : () => {}),
    [workspace],
  );
  const data = useSyncExternalStore(subscribe, () => cachedWorkspaceSummary(workspace));

  useEffect(() => {
    if (workspace) loadWorkspaceSummary(workspace).catch(() => {});
  }, [workspace]);

  return data;
}
