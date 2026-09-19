import { useCallback, useEffect, useState } from 'react';
import {
  activateEntityChatSession, deleteEntityChatSession, getEntityChatSessions,
} from '../api';
import { useI18n } from '../i18n';

/**
 * The threads one entity chat has been, and the two things you can do to them.
 *
 * Split out of the component that shows them because two surfaces need the same
 * state: the button beside the chat's name (which only exists when there is
 * history behind it) and the list that replaces the conversation when it is
 * pressed.
 *
 * Reopening is a swap on the server, not a read: the chosen thread becomes the
 * live thread, epoch included, so the next message carries on the same
 * conversation instead of opening a copy of it beside the original. The caller
 * is handed the restored transcript so the feed can change with the click,
 * without a second round trip.
 *
 * @param {{kind: string, id: string}|null} chatRef the chat's storage key, which
 *   the server hands the browser as `chat_ref` on the chat's own GET.
 * @param {number} [refreshKey] bumped by the caller when the list may have gone
 *   stale: a turn just landed, or a new thread was started.
 */
export function useChatSessions(chatRef, refreshKey = 0) {
  const { t } = useI18n();
  const kind = chatRef?.kind;
  const entityId = chatRef?.id;
  const [sessions, setSessions] = useState([]);
  // Whether this chat has ever been more than one conversation. Not the same as
  // `sessions.length > 1`: reading an old thread whose only sibling was an
  // empty new chat leaves one thread and a history that still has to be
  // reachable, which is the server's call to make (see `_has_history`).
  const [hasHistory, setHasHistory] = useState(false);
  // The id of the thread a request is in flight for, so one row can show it.
  const [working, setWorking] = useState('');
  const [error, setError] = useState('');

  // Every response that carries threads carries the flag with them, so the
  // control never has to guess from the list's length.
  const take = useCallback((data) => {
    setSessions(data?.sessions || []);
    setHasHistory(Boolean(data?.has_history));
  }, []);

  const reload = useCallback(async () => {
    if (!kind || !entityId) { take(null); return; }
    try {
      const { data } = await getEntityChatSessions({ kind, id: entityId });
      take(data);
    } catch {
      // The history is an affordance, not the chat: a failure here leaves the
      // conversation working and simply offers no way back into the archive.
      take(null);
    }
  }, [kind, entityId, take]);

  useEffect(() => { reload(); }, [reload, refreshKey]);

  const activate = useCallback(async (sessionId) => {
    if (!kind || !entityId || working) return null;
    setWorking(sessionId);
    setError('');
    try {
      const { data } = await activateEntityChatSession({ kind, id: entityId }, sessionId);
      take(data);
      return data;
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('entityChat.switchFailed'));
      return null;
    } finally {
      setWorking('');
    }
  }, [kind, entityId, working, take, t]);

  const remove = useCallback(async (sessionId) => {
    if (!kind || !entityId || working) return false;
    setWorking(sessionId);
    setError('');
    try {
      const { data } = await deleteEntityChatSession({ kind, id: entityId }, sessionId);
      take(data);
      return true;
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('entityChat.deleteSessionFailed'));
      return false;
    } finally {
      setWorking('');
    }
  }, [kind, entityId, working, take, t]);

  return {
    sessions,
    // Whether the control exists at all, and what it counts: every thread,
    // the one being read included, because that is what the list shows.
    hasHistory,
    count: sessions.length,
    working,
    error,
    activate,
    remove,
    reload,
  };
}

export default useChatSessions;
