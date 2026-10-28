/**
 * Chat or Build: how a conversation is drawn. The Chat view keeps each reply
 * to its answer (what the agent does on the way passes through while the turn
 * is live); the Build view keeps every step. Kept in this browser, one choice
 * per kind of chat: the page chats (the Chat page, a flow's, a team's, an
 * instance's) share one, and the side chats embedded in other pages (entity
 * build chats, the page chat, Help, the planner and the architect) share
 * another, so switching a side chat to Build leaves the Chat page as it was.
 */
import { useCallback, useEffect, useState } from 'react';

export const VIEW_MODE_KEY = 'agent_hub_chat_view_mode';
export const SIDE_VIEW_MODE_KEY = 'agent_hub_side_chat_view_mode';
const KEYS = { main: VIEW_MODE_KEY, side: SIDE_VIEW_MODE_KEY };
// Fired on this window when a choice changes, so every mounted chat of that
// kind follows (the `storage` event only reaches other tabs).
const CHANGE_EVENT = 'agent-hub:chat-view-mode';

export function readViewMode(key = VIEW_MODE_KEY) {
  try { return localStorage.getItem(key) === 'build' ? 'build' : 'chat'; } catch { return 'chat'; }
}

export function writeViewMode(mode, key = VIEW_MODE_KEY) {
  try { localStorage.setItem(key, mode); } catch { /* storage unavailable */ }
  try { window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: { key, mode } })); } catch { /* no window */ }
}

/** [viewMode, setViewMode], shared with the other chats of the same kind:
 *  `scope` is 'main' (page chats) or 'side' (chats embedded in a page). */
export function useChatViewMode(scope = 'main') {
  const key = KEYS[scope] || VIEW_MODE_KEY;
  const [mode, setMode] = useState(() => readViewMode(key));
  useEffect(() => {
    const onChange = (e) => {
      const changed = e.type === 'storage' ? e.key : e.detail?.key;
      if (changed && changed !== key) return;
      setMode(readViewMode(key));
    };
    window.addEventListener(CHANGE_EVENT, onChange);
    window.addEventListener('storage', onChange);
    return () => {
      window.removeEventListener(CHANGE_EVENT, onChange);
      window.removeEventListener('storage', onChange);
    };
  }, [key]);
  const set = useCallback((next) => {
    setMode(next);
    writeViewMode(next, key);
  }, [key]);
  return [mode, set];
}
