import { useEffect, useState } from 'react';

// A pane laid over the whole screen (not the browser's Fullscreen API, which
// an iPhone does not offer for a part of the page): over the top bar and the
// menu column (z-40), under the page chat's button and panel (z-[46]), so the
// chat still opens over it.
export const FULL_SCREEN_CLS = 'fixed inset-0 z-[45] bg-white flex flex-col pt-safe pb-safe px-safe';

/** [on, toggle, setOn]: a pane's full-screen state; Escape leaves it. */
export function useFullScreen() {
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (!on) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') setOn(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [on]);
  return [on, () => setOn((v) => !v), setOn];
}
