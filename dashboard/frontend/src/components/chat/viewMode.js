/**
 * Chat or Build: how a conversation is drawn. The Chat view keeps each reply
 * to its answer (what the agent does on the way passes through while the turn
 * is live); the Build view keeps every step. One choice for every chat in the
 * hub, the Chat page's and the ones embedded in other pages, kept in this
 * browser, so switching it anywhere switches it everywhere.
 */
import { useCallback, useEffect, useState } from 'react';

export const VIEW_MODE_KEY = 'agent_hub_chat_view_mode';
// Fired on this window when the choice changes, so every mounted chat follows
// (the `storage` event only reaches other tabs).
const CHANGE_EVENT = 'agent-hub:chat-view-mode';

export function readViewMode() {
  try { return localStorage.getItem(VIEW_MODE_KEY) === 'build' ? 'build' : 'chat'; } catch { return 'chat'; }
}

export function writeViewMode(mode) {
  try { localStorage.setItem(VIEW_MODE_KEY, mode); } catch { /* storage unavailable */ }
  try { window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: mode })); } catch { /* no window */ }
}

/** [viewMode, setViewMode], shared with every other chat on the page. */
export function useChatViewMode() {
  const [mode, setMode] = useState(readViewMode);
  useEffect(() => {
    const onChange = () => setMode(readViewMode());
    window.addEventListener(CHANGE_EVENT, onChange);
    window.addEventListener('storage', onChange);
    return () => {
      window.removeEventListener(CHANGE_EVENT, onChange);
      window.removeEventListener('storage', onChange);
    };
  }, []);
  const set = useCallback((next) => {
    setMode(next);
    writeViewMode(next);
  }, []);
  return [mode, set];
}
