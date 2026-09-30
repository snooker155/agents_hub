import { genId } from './turnState';
import { getMessageInsights, getSessionMessages, getSessions, getTelegramBindings } from '../../api';
import { useEffect } from 'react';

/**
 * The Telegram side of the page: which chats are bound to this workspace, and
 * waking one of them up — a Telegram thread is a conversation the server has
 * been keeping, so opening it means resolving its session and reading its
 * history back rather than starting a new one.
 */
export function useChatTelegram(deps) {
  const {
    currentTelegramBinding, setConversations, setSessionId, setTelegramBindings,
  } = deps;

  // ---- load Telegram bindings (refresh on workspace switch + interval) ----
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const { data } = await getTelegramBindings();
        if (!cancelled) setTelegramBindings(data || []);
      } catch {
        if (!cancelled) setTelegramBindings([]);
      }
    };
    refresh();
    return () => { cancelled = true; };
  }, [setTelegramBindings]);

  // ---- hydrate Telegram conversation: resolve session_id, load past messages ----
  // Re-runs whenever the binding *or its workspace* changes. The Telegram thread
  // is one conversation_id across workspaces, but each workspace shows only its
  // own slice of runs — so when the workspace context flips, we re-fetch and
  // replace the bubble list with the slice that belongs to the new workspace.
  // Past bubbles are reconstructed from server-side run logs via getMessageInsights.
  const bindingWorkspaceKey = currentTelegramBinding?.workspace || null;
  useEffect(() => {
    if (!currentTelegramBinding) return;
    const convId = currentTelegramBinding.conversation_id;
    if (!convId) return;
    let cancelled = false;

    (async () => {
      try {
        const { data: sessions } = await getSessions({ conversation_id: convId, limit: 1 });
        const sess = (sessions?.items || [])[0];
        if (!sess?.session_id || cancelled) return;
        setSessionId(sess.session_id);

        const { data: runs } = await getSessionMessages(sess.session_id);
        // Filter runs to only those that executed against the binding's workspace.
        const bindingWorkspace = currentTelegramBinding.workspace || null;
        const scopedRuns = (runs || []).filter((r) => (r.workspace || null) === bindingWorkspace);
        const insightsList = await Promise.all(
          scopedRuns.map((r) =>
            getMessageInsights(r.run_id).then((res) => ({ run: r, insights: res.data }))
              .catch(() => null)
          )
        );
        if (cancelled) return;

        const bubbles = [];
        for (const entry of insightsList) {
          if (!entry) continue;
          const runId = entry.run.run_id;
          const agentId = entry.run.agent_id;
          const msgs = entry.insights?.messages || [];
          for (const m of msgs) {
            bubbles.push({
              id: genId(),
              role: m.role === 'assistant' ? 'agent' : 'user',
              content: m.content || '',
              agent_id: m.role === 'assistant' ? agentId : undefined,
              run_id: m.role === 'assistant' ? runId : undefined,
              origin: 'telegram',
            });
          }
        }

        setConversations((prev) =>
          prev.map((c) => (c.id !== convId ? c : { ...c, messages: bubbles }))
        );
      } catch {
        // Best-effort hydration; failures are silent.
      }
    })();

    return () => { cancelled = true; };
  }, [currentTelegramBinding, bindingWorkspaceKey, setConversations, setSessionId]);

}

export default useChatTelegram;
