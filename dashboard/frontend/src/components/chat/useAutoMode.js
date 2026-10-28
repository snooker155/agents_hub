/**
 * Auto mode in a conversation (common/auto_mode.py on the backend): while it
 * is on, every confirmation of the conversation's turns is accepted without a
 * card. The flag lives on the conversation (`auto_approve`, saved with it);
 * before the first message there is no conversation yet, so a draft holds it
 * and the new conversation takes it over (useChatSend).
 *
 * `allowed` is the workspace's answer (`auto_approve_allowed` in its summary):
 * an admin may turn auto mode off, and then the toggle is off and disabled
 * and nothing asks the server for it.
 */
import { useCallback, useEffect, useState } from 'react';
import { loadWorkspaceSummary } from '../../api/workspaceSummary';

export default function useAutoMode({ currentConv, setConversations, workspace }) {
  const [draft, setDraft] = useState(false);
  const [allowed, setAllowed] = useState(true);

  useEffect(() => {
    let alive = true;
    loadWorkspaceSummary(workspace || 'default')
      .then((summary) => { if (alive) setAllowed(summary?.auto_approve_allowed !== false); })
      .catch(() => { if (alive) setAllowed(true); });
    return () => { alive = false; };
  }, [workspace]);

  const requested = currentConv ? Boolean(currentConv.auto_approve) : draft;
  const on = allowed && requested;

  const setOn = useCallback((next) => {
    if (!currentConv) {
      setDraft(Boolean(next));
      return;
    }
    const id = currentConv.id;
    setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, auto_approve: Boolean(next) } : c)));
  }, [currentConv, setConversations]);

  const toggle = useCallback(() => {
    if (!allowed) return;
    setOn(!requested);
  }, [allowed, requested, setOn]);

  // A draft belongs to the chat about to be created; once that chat exists
  // (or another one is opened) it is spent.
  const takeDraft = useCallback(() => {
    const value = allowed && draft;
    setDraft(false);
    return value;
  }, [allowed, draft]);

  return { on, allowed, toggle, setOn, takeDraft };
}
