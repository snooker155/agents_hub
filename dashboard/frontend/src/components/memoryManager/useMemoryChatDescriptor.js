import { useCallback, useMemo } from 'react';
import { getMemoryChat, clearMemoryChat, stopMemoryChat, memoryChatUrl } from '../../api';
import { useI18n } from '../../i18n';

/**
 * The Memory Agent's chat, beside the pool it is about.
 *
 * The pool open on the page is passed through to the turn, which binds the
 * agent's memory tools to it for that build — so a question asked while a pool
 * is open is answered from that pool, not from whatever the agent record
 * happens to be assigned. The transcript is keyed on the pool too: a
 * conversation about one pool should not come back under another.
 *
 * The callbacks are memoised on workspace and pool because EntityChat loads its
 * transcript in an effect keyed on them.
 */
function useMemoryChatDescriptor(workspace, memoryId, onChanged) {
  const { t } = useI18n();

  const loadChat = useCallback(() => getMemoryChat(workspace, memoryId), [workspace, memoryId]);
  const clearChat = useCallback(() => clearMemoryChat(workspace, memoryId), [workspace, memoryId]);
  const stopChat = useCallback(() => stopMemoryChat(workspace, memoryId), [workspace, memoryId]);

  const onEvent = useCallback((ev) => {
    if (ev.type === 'memory') onChanged();
  }, [onChanged]);

  return useMemo(() => ({
    scope: `memory:${workspace || ''}:${memoryId || ''}`,
    path: memoryChatUrl(workspace, memoryId),
    loadChat, clearChat, stopChat, onEvent,
    title: t('memoryManager.askChat'),
    emptyHint: memoryId ? t('memoryManager.askChatHint') : t('memoryManager.askChatPickPool'),
    suggestions: [
      t('memoryManager.chatSuggestWhatIsKnown'),
      t('memoryManager.chatSuggestSearch'),
      t('memoryManager.chatSuggestExtract'),
    ],
  }), [workspace, memoryId, loadChat, clearChat, stopChat, onEvent, t]);
}

export { useMemoryChatDescriptor };
