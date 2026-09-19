import React from 'react';
import {
  ArrowLeft, History, Loader2, MessageSquare, Trash2,
} from 'lucide-react';
import { useFormatters, useI18n } from '../i18n';

/**
 * The way into a build chat's past threads, and the way back out.
 *
 * It is not a menu over the conversation: pressing History puts the list *in
 * place of* the conversation, filling the column the chat lives in. A thread is
 * a screen's worth of title, size and date, and the column is as wide as the
 * chat itself, so the list can say which conversation each thread was instead
 * of truncating all of them into a dropdown.
 *
 * Picking a thread swaps it in on the server and the column goes back to being
 * a chat, now showing that one. Picking the live thread is just the way back.
 */

/**
 * The toggle at the end of the chat's title row. Drawn only where there is a
 * history to open, which is the host's call: a chat being read from one of its
 * old threads has one left and a past all the same.
 */
export function ChatSessionsToggle({ open, count, disabled = false, onToggle }) {
  const { t } = useI18n();
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      aria-pressed={open}
      title={t('entityChat.historyHint')}
      className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs disabled:opacity-40
                  ${open
        ? 'text-indigo-700 bg-indigo-50 dark:bg-indigo-900/30'
        : 'text-gray-500 hover:text-indigo-600 hover:bg-gray-50 dark:hover:bg-gray-800'}`}
    >
      <History className="w-3.5 h-3.5" />
      <span className="hidden sm:inline">{t('entityChat.history')}</span>
      <span className="text-[10px] text-gray-400">{count}</span>
    </button>
  );
}

/**
 * The list itself, drawn where the feed would be.
 *
 * @param {object}   props
 * @param {object[]} props.sessions  summaries, live thread first.
 * @param {string}   [props.working] id of the thread a request is in flight for.
 * @param {string}   [props.error]   what went wrong with the last one.
 * @param {function} props.onPick    called with the session to open.
 * @param {function} props.onDelete  called with the session to drop.
 * @param {function} props.onClose   back to the conversation, unchanged.
 * @param {string}   [props.className] the feed's own classes, so the list
 *   occupies exactly the space the conversation did.
 */
export default function ChatSessionList({
  sessions, working = '', error = '', onPick, onDelete, onClose, className = '',
}) {
  const { t } = useI18n();
  const { formatDate } = useFormatters();

  return (
    <div className={className}>
      <div className="flex items-center justify-between gap-2 mb-2">
        <button
          type="button"
          onClick={onClose}
          className="inline-flex items-center gap-1 text-[11px] text-gray-500 hover:text-indigo-600"
        >
          <ArrowLeft className="w-3.5 h-3.5" /> {t('entityChat.backToChat')}
        </button>
        <span className="text-[10px] uppercase tracking-wide text-gray-400">
          {t('entityChat.pastChats')}
        </span>
      </div>
      {error && <div className="mb-2 text-[11px] text-red-600">{error}</div>}

      <div className="space-y-1">
        {sessions.map((s) => (
          <div
            key={s.id}
            className={`group flex items-start gap-1 rounded-lg border
                        ${s.active
              ? 'border-indigo-200 bg-indigo-50/60 dark:border-indigo-800 dark:bg-indigo-900/20'
              : 'border-gray-200 dark:border-gray-700 hover:border-indigo-200'}`}
          >
            <button
              type="button"
              onClick={() => onPick(s)}
              disabled={!!working}
              className="flex-1 min-w-0 text-left px-3 py-2.5 disabled:opacity-60"
            >
              <div className="flex items-center gap-1.5">
                <MessageSquare className={`w-3.5 h-3.5 shrink-0 ${s.active ? 'text-indigo-500' : 'text-gray-400'}`} />
                <span className={`text-xs truncate ${s.active
                  ? 'text-indigo-700 dark:text-indigo-300 font-medium'
                  : 'text-gray-700 dark:text-gray-200'}`}
                >
                  {s.title || t('entityChat.untitledChat')}
                </span>
              </div>
              <div className="mt-0.5 pl-5 text-[10px] text-gray-400 truncate">
                {s.active && <span className="text-indigo-500">{t('entityChat.currentChat')} · </span>}
                {t('entityChat.messageCount', { count: s.messages })}
                {s.updated_at ? ` · ${formatDate(s.updated_at, {
                  dateStyle: 'short', timeStyle: 'short',
                })}` : ''}
              </div>
            </button>
            {working === s.id ? (
              <Loader2 className="w-3.5 h-3.5 mt-3 mr-2 shrink-0 animate-spin text-gray-400" />
            ) : !s.active && (
              <button
                type="button"
                onClick={() => onDelete(s)}
                disabled={!!working}
                title={t('entityChat.deleteSession')}
                className="mt-2.5 mr-2 shrink-0 p-1 rounded text-gray-300 opacity-0
                           group-hover:opacity-100 focus:opacity-100 hover:text-red-600
                           disabled:opacity-0"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
