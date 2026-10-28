/**
 * What a build chat's turn changed in the thing the chat is pinned to (a loop's
 * step added, a team member removed, a project node renamed): a line per change
 * in the Build view, where it happened, and the whole list folded under the
 * reply in the Chat view, the way the files a turn touched are listed.
 *
 * Fed by the `entity_changed` stream event (chat/entity_chat.py), which
 * turnEvents.js folds into the message as `{type: 'change', action, kind,
 * label}` timeline entries and a `changes` list.
 */
import { useState } from 'react';
import { ChevronDown, ChevronUp, MinusCircle, MoreHorizontal, Pencil, PlusCircle, Shapes } from 'lucide-react';
import { useI18n } from '../../i18n';

// The verb decides the icon and the colour, so a run of changes can be skimmed
// for what was removed without reading a word.
const CHANGE_STYLES = {
  added: { icon: PlusCircle, className: 'text-green-600' },
  removed: { icon: MinusCircle, className: 'text-red-500' },
  updated: { icon: Pencil, className: 'text-sky-600' },
  set: { icon: Pencil, className: 'text-sky-600' },
  more: { icon: MoreHorizontal, className: 'text-gray-400' },
};

/** One change, as one line. */
export function ChangeStep({ entry }) {
  const { t } = useI18n();
  const style = CHANGE_STYLES[entry.action] || CHANGE_STYLES.updated;
  const Icon = style.icon;
  if (entry.action === 'more') {
    return (
      <div className="flex items-center gap-1.5 text-xs text-gray-400">
        <Icon className="w-3.5 h-3.5 shrink-0" /> {t('flowChatFeed.moreChanges', { count: Number(entry.label) || 0 })}
      </div>
    );
  }
  const kind = entry.kind ? t(`flowChatFeed.kinds.${entry.kind}`, { defaultValue: entry.kind }) : '';
  return (
    <div className={`flex items-start gap-1.5 text-xs ${style.className}`} data-testid="change-step">
      <Icon className="w-3.5 h-3.5 mt-0.5 shrink-0" />
      <div className="min-w-0">
        {t(`flowChatFeed.change.${entry.action}`, { defaultValue: entry.action })}
        {kind ? <span className="text-gray-400"> {kind}</span> : null}
        {' '}
        <span className="font-medium break-words">{entry.label}</span>
      </div>
    </div>
  );
}

/** The turn's changes under its reply, folded once there are more than a few. */
export function MessageChanges({ changes }) {
  const { t } = useI18n();
  const items = changes || [];
  const [userOpen, setUserOpen] = useState(null);
  const open = userOpen === null ? items.length <= 3 : userOpen;
  if (!items.length) return null;
  return (
    <div className="mt-2 rounded-lg border border-gray-200 bg-gray-50/70 overflow-hidden" data-testid="message-changes">
      <button
        type="button"
        onClick={() => setUserOpen(!open)}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-left hover:bg-gray-100/70"
      >
        <Shapes className="w-3.5 h-3.5 text-gray-500 flex-shrink-0" />
        <span className="text-xs font-semibold text-gray-700">{t('flowChatFeed.changesCount', { count: items.length })}</span>
        {open
          ? <ChevronUp className="w-3 h-3 text-gray-400 ml-auto flex-shrink-0" />
          : <ChevronDown className="w-3 h-3 text-gray-400 ml-auto flex-shrink-0" />}
      </button>
      {open && (
        <div className="border-t border-gray-100 px-3 py-2 space-y-1">
          {items.map((c, i) => <ChangeStep key={i} entry={c} />)}
        </div>
      )}
    </div>
  );
}
