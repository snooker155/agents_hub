/**
 * A view's build chat (the Visualizer), pinned to one view.
 *
 * The same entity chat every other builder page has: the transcript lives on
 * the server under `view:<id>`, so a reload resumes the session and the
 * floating page-chat panel can host the very conversation the Studio's column
 * shows rather than opening a second one about the same view. The Studio and
 * the view's own page (pages/ViewDetail.jsx) register it through usePageChat.
 *
 * `registerSend` is what makes the view's own buttons, a control the agent
 * authored, a `sendToAgent` bridge inside an html view, post into this chat;
 * `registerComposer` hands out a prefill of the composer, for a code view's
 * Discuss and Edit. The callbacks are memoised on the view id because
 * EntityChat loads its transcript in an effect keyed on them; fresh closures
 * each render would refetch the conversation continuously.
 */
import { useCallback, useMemo } from 'react';
import { clearViewChat, getViewChat, stopViewChat, viewChatUrl } from '../api';
import { useI18n } from '../i18n';

export default function useViewChatDescriptor(viewId, registerSend = null, registerComposer = null) {
  const { t } = useI18n();

  const loadChat = useCallback(() => getViewChat(viewId), [viewId]);
  const clearChat = useCallback(() => clearViewChat(viewId), [viewId]);
  const stopChat = useCallback(() => stopViewChat(viewId), [viewId]);

  return useMemo(() => (viewId ? {
    scope: `view:${viewId}`,
    path: viewChatUrl(viewId),
    loadChat, clearChat, stopChat, registerSend, registerComposer,
    title: t('studio.visualizer'),
    emptyHint: t('studio.chatEmptyHint'),
    suggestions: [
      t('studio.suggestBuild'),
      t('studio.suggestControls'),
      t('studio.suggestExplain'),
    ],
  } : null), [viewId, loadChat, clearChat, stopChat, registerSend, registerComposer, t]);
}
