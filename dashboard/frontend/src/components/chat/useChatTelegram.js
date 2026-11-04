import { genId } from './turnState';
import { getMessageInsights, getSessionMessages, getSessions } from '../../api';
import { loadConnectorBindings } from './connectorChats';
import { useEffect } from 'react';

/**
 * The connectors' side of the page (Telegram and the channels, connectorChats.js):
 * which of their chats are bound, and waking one of them up. A connector's
 * thread is a conversation the server has been keeping, so opening it means
 * resolving its session and reading its history back rather than starting a
 * new one.
 */
export function useChatTelegram(deps) {
  const {
    currentConnectorBinding, setConversations, setSessionId, setConnectorBindings,
  } = deps;

  // ---- load every connector's bindings ----
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const rows = await loadConnectorBindings();
        if (!cancelled) setConnectorBindings(rows);
      } catch {
        if (!cancelled) setConnectorBindings([]);
      }
    };
    refresh();
    return () => { cancelled = true; };
  }, [setConnectorBindings]);

  // ---- hydrate a connector's conversation: resolve session_id, load past messages ----
  // Re-runs whenever the binding *or its workspace* changes. The thread
  // is one conversation_id across workspaces, but each workspace shows only its
  // own slice of runs — so when the workspace context flips, we re-fetch and
  // replace the bubble list with the slice that belongs to the new workspace.
  // Past bubbles are reconstructed from server-side run logs via getMessageInsights.
  const bindingWorkspaceKey = currentConnectorBinding?.workspace || null;
  useEffect(() => {
    if (!currentConnectorBinding) return;
    const convId = currentConnectorBinding.conversation_id;
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
        const bindingWorkspace = currentConnectorBinding.workspace || null;
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
              origin: currentConnectorBinding.connector || 'telegram',
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
  }, [currentConnectorBinding, bindingWorkspaceKey, setConversations, setSessionId]);

}

export default useChatTelegram;
