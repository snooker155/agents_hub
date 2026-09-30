import { useCallback, useMemo } from 'react';
import {
  agentDefinitionChatUrl, clearAgentDefinitionChat, getAgentDefinitionChat,
  stopAgentDefinitionChat,
} from '../../api';
import { useI18n } from '../../i18n';

/**
 * The agent's own definition chat, pinned to this agent: the Agent Creator
 * edits instructions.md, capabilities.md, usage.md and the tool list in place,
 * and the editors below pick up the result.
 *
 * The callbacks are memoised on the agent id because EntityChat loads its
 * transcript in an effect keyed on them — fresh closures each render would
 * refetch the conversation continuously.
 */
function useDefinitionChatDescriptor(agentId, workspace, onChanged) {
  const { t } = useI18n();

  const loadChat = useCallback(() => getAgentDefinitionChat(agentId), [agentId]);
  const clearChat = useCallback(() => clearAgentDefinitionChat(agentId), [agentId]);
  const stopChat = useCallback(() => stopAgentDefinitionChat(agentId), [agentId]);

  const onEvent = useCallback((ev) => {
    if (ev.type === 'agentdef') onChanged();
  }, [onChanged]);

  return useMemo(() => (agentId ? {
    scope: `agent:${agentId}`,
    path: agentDefinitionChatUrl(agentId, workspace),
    loadChat, clearChat, stopChat, onEvent,
    title: t('agentDetails.definitionChat'),
    emptyHint: t('agentDetails.definitionChatHint'),
    suggestions: [
      t('agentDetails.chatSuggestSharpen'),
      t('agentDetails.chatSuggestCapabilities'),
      t('agentDetails.chatSuggestTools'),
      t('agentDetails.chatSuggestUsage'),
    ],
  } : null), [agentId, workspace, loadChat, clearChat, stopChat, onEvent, t]);
}


export default useDefinitionChatDescriptor;
