import React, { useCallback, useMemo, useState } from 'react';
import { MessageSquare, PanelRightClose } from 'lucide-react';
import EntityChat from '../EntityChat';
import { FILL_COLUMN } from '../ChatColumn';
import { getTeamChat, clearTeamChat, stopTeamChat, teamChatUrl } from '../../api';
import { useI18n } from '../../i18n';

/**
 * The team's build chat beside the team page, as the Loop Creator sits beside
 * a loop (loops/LoopCreatorChat.jsx): the Team Creator edits the roster in
 * place and the page picks the result up through `onTeamChanged` (the `team`
 * event the route sends at the end of a turn).
 *
 * The callbacks are memoised on the team id because EntityChat loads its
 * transcript in an effect keyed on them: fresh closures each render would
 * refetch the conversation continuously.
 */
export default function TeamCreatorChat({ teamId, onTeamChanged, onClose, className = '' }) {
  const { t } = useI18n();
  const [controls, setControls] = useState(null);

  const loadChat = useCallback(() => getTeamChat(teamId), [teamId]);
  const clearChat = useCallback(() => clearTeamChat(teamId), [teamId]);
  const stopChat = useCallback(() => stopTeamChat(teamId), [teamId]);
  const onEvent = useCallback((ev) => {
    if (ev.type === 'team' && ev.team) onTeamChanged(ev.team);
  }, [onTeamChanged]);
  const suggestions = useMemo(() => [
    t('teamDetails.chatSuggestManifest'),
    t('teamDetails.chatSuggestMember'),
    t('teamDetails.chatSuggestMode'),
    t('teamDetails.chatSuggestCharter'),
  ], [t]);

  return (
    <div className={`flex flex-col overflow-hidden bg-white ${className}`} data-testid="team-creator-chat">
      <div className="flex items-center justify-between h-11 shrink-0 px-3 border-b border-slate-200 gap-2">
        <div className="flex items-center gap-2 text-sm font-medium text-gray-700 min-w-0">
          <MessageSquare className="w-4 h-4 shrink-0 text-gray-400" />
          <span className="truncate">{t('teamDetails.buildChat')}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span ref={setControls} className="flex items-center gap-2" />
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" title={t('teamDetails.hideBuildChat')}
            aria-label={t('teamDetails.hideBuildChat')}>
            <PanelRightClose className="w-4 h-4" />
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0 flex flex-col px-3 py-2">
        {controls && (
          <EntityChat
            key={teamId}
            path={teamChatUrl(teamId)}
            loadChat={loadChat} clearChat={clearChat} stopChat={stopChat} onEvent={onEvent}
            header={false}
            clearTarget={controls}
            emptyHint={t('teamDetails.buildChatHint')}
            suggestions={suggestions}
            {...FILL_COLUMN}
          />
        )}
      </div>
    </div>
  );
}
