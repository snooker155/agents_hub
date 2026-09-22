import React, { useEffect, useMemo, useState } from 'react';
import { getHealth } from '../api';
import { DEFAULT_FEATURES, FeaturesContext } from './features';

/**
 * Reads the optional-feature flags once, at app start.
 *
 * One call, no refetch: turning the playground off is a restart of the backend,
 * so a tab that was open across that restart is already stale in other ways.
 * Until the answer lands, and forever if it never does, every feature counts as
 * on, so a slow or old backend shows the full app rather than a trimmed one.
 */
export function FeaturesProvider({ children }) {
  const [features, setFeatures] = useState(DEFAULT_FEATURES);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await getHealth();
        if (cancelled || !data?.features) return;
        setFeatures({ ...DEFAULT_FEATURES, ...data.features });
      } catch {
        /* keep the defaults: an unreachable backend hides nothing */
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const value = useMemo(() => features, [features]);
  return <FeaturesContext.Provider value={value}>{children}</FeaturesContext.Provider>;
}

export default FeaturesProvider;
