import React, { useCallback, useMemo, useState } from 'react';
import { MessageSquare, PanelRightClose } from 'lucide-react';
import EntityChat from '../EntityChat';
import { FILL_COLUMN } from '../ChatColumn';
import { getLoopChat, clearLoopChat, stopLoopChat, loopChatUrl } from '../../api';
import { useI18n } from '../../i18n';

/**
 * The Loop Creator beside the loop page, the way the Planner sits beside the
 * Tasks board and the Architect beside a project's graph: a column the page
 * opens from its header and folds away from here.
 *
 * A loop is three decisions (which flow, what "good enough" means, when to
 * give up) and two of them are prose, so they get a conversation pinned to
 * this loop: the Loop Creator edits the record in place and the page picks the
 * result up through `onLoopChanged` (the `loop` event the route sends at the
 * end of a turn).
 *
 * The callbacks are memoised on the loop id because EntityChat loads its
 * transcript in an effect keyed on them: fresh closures each render would
 * refetch the conversation continuously.
 */
export default function LoopCreatorChat({ loopId, onLoopChanged, onClose, className = '' }) {
  const { t } = useI18n();
  // Where EntityChat draws New chat, Chat/Build and History: the panel's own
  // title row, so the controls sit where the Planner has them.
  const [controls, setControls] = useState(null);

  const loadChat = useCallback(() => getLoopChat(loopId), [loopId]);
  const clearChat = useCallback(() => clearLoopChat(loopId), [loopId]);
  const stopChat = useCallback(() => stopLoopChat(loopId), [loopId]);
  const onEvent = useCallback((ev) => {
    if (ev.type === 'loop' && ev.loop) onLoopChanged(ev.loop);
  }, [onLoopChanged]);
  const suggestions = useMemo(() => [
    t('loops.chatSuggestCriterion'),
    t('loops.chatSuggestReviewer'),
    t('loops.chatSuggestCeilings'),
    // The chat can start the loop too, behind an approval step.
    t('loops.chatSuggestRun'),
  ], [t]);

  return (
    <div className={`flex flex-col overflow-hidden bg-white ${className}`}>
      {/* A fixed height: the controls come and go with the conversation and
          the history list, and the row must not move when they do. */}
      <div className="flex items-center justify-between h-11 shrink-0 px-3 border-b border-slate-200 gap-2">
        <div className="flex items-center gap-2 text-sm font-medium text-gray-700 min-w-0">
          <MessageSquare className="w-4 h-4 shrink-0 text-gray-400" />
          <span className="truncate">{t('loops.loopCreator')}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span ref={setControls} className="flex items-center gap-2" />
          {onClose && (
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600" title={t('loops.hideLoopCreator')}
              aria-label={t('loops.hideLoopCreator')}>
              <PanelRightClose className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>
      <div className="flex-1 min-h-0 flex flex-col px-3 py-2">
        {controls && (
          <EntityChat
            key={loopId}
            path={loopChatUrl(loopId)}
            loadChat={loadChat} clearChat={clearChat} stopChat={stopChat} onEvent={onEvent}
            header={false}
            clearTarget={controls}
            emptyHint={t('loops.buildChatHint')}
            suggestions={suggestions}
            {...FILL_COLUMN}
            /* The rule over the composer across the panel's `px-3`. */
            composerClassName="mt-auto -mx-3 px-3"
          />
        )}
      </div>
    </div>
  );
}
