// A block of the Settings page with its own form state (web search, fetch
// limits, domain lists, temperature, the tool policy, loop settings) hands
// its unsaved edits to the page's one Save button through this hook instead
// of carrying a Save button of its own (useWorkspaceSettings ``setDraft``).
import { useEffect, useRef } from 'react';

/**
 * Register ``save`` under ``key`` while ``dirty``. ``save`` resolves to true
 * when it stored the edits and false when it did not (it shows its own
 * error). Without ``s.setDraft`` (a block used outside the Settings page)
 * nothing is registered and the block keeps its own button.
 */
export function useSettingsDraft(s, key, dirty, save) {
  const saveRef = useRef(save);
  useEffect(() => { saveRef.current = save; });
  const register = s?.setDraft;
  useEffect(() => {
    if (!register) return undefined;
    register(key, dirty ? () => saveRef.current() : null);
    return () => register(key, null);
  }, [register, key, dirty]);
}
