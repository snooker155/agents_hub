import { MessageSquare, ScrollText, Terminal } from 'lucide-react';
import { useI18n } from '../../i18n';

/**
 * The Chat / Build switch of the Chat page's top bar, for a chat embedded in
 * another page. `small` drops the labels where the row has no room for them.
 * `summary` adds a third choice, the flow chat's Summary view: one reply per
 * request (flow/summaryView.js).
 */
export default function ViewModeToggle({ viewMode, onChange, small = false, summary = false }) {
  const { t } = useI18n();
  const options = [
    { id: 'chat', icon: MessageSquare, label: t('chat.chat'), title: t('chat.cleanChatMessagesOnly') },
    { id: 'build', icon: Terminal, label: t('chat.build'), title: t('chat.buildFullTranscriptToolsThinking') },
    ...(summary ? [{ id: 'summary', icon: ScrollText, label: t('chat.summaryView'), title: t('chat.summaryViewTitle') }] : []),
  ];
  return (
    <div className="inline-flex rounded-lg border border-gray-200 bg-white overflow-hidden shrink-0" role="group" data-testid="view-mode-toggle">
      {options.map(({ id, icon: Icon, label, title }, i) => (
        <button
          key={id}
          type="button"
          onClick={() => onChange(id)}
          aria-pressed={viewMode === id}
          title={title}
          className={`flex items-center gap-1.5 text-xs font-medium transition-colors ${small ? 'h-7 px-2' : 'h-[30px] px-2.5'} ${
            i ? 'border-l border-gray-200' : ''
          } ${viewMode === id ? 'bg-indigo-50 text-indigo-700' : 'text-gray-500 hover:bg-gray-50'}`}
        >
          <Icon className="w-3.5 h-3.5" />
          {!small && <span>{label}</span>}
        </button>
      ))}
    </div>
  );
}
