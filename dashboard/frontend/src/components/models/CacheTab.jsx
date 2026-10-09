import { useCallback, useEffect, useState } from 'react';
import RuntimeCache from './RuntimeCache';
import { getRuntimeStatus } from '../../api/localModels';

const POLL_MS = 10000;

/**
 * The Models page's Prompt cache tab: the runtime's prompt cache on a page
 * of its own (RuntimeCache, open from the start). Its figures come with the
 * runtime status, polled here while the tab is shown; what the cache holds
 * and its settings the card reads itself.
 */
export default function CacheTab() {
  // The last status, or {} once the runtime failed to answer; null until
  // the first answer, so the card waits for it.
  const [status, setStatus] = useState(null);

  const load = useCallback(async () => {
    let next;
    try {
      const { data } = await getRuntimeStatus();
      next = data || {};
    } catch {
      next = {};
    }
    setStatus(next);
  }, []);

  useEffect(() => {
    load(); // eslint-disable-line react-hooks/set-state-in-effect
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  return <RuntimeCache usage={status?.usage || null} standalone ready={status !== null} />;
}
