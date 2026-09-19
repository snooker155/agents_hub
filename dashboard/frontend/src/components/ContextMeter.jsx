import React from 'react';
import { AlertTriangle, Trash2 } from 'lucide-react';
import { useI18n } from '../i18n';
import { CONTEXT_HIGH, CONTEXT_WARN } from './contextUsage';

/**
 * How full the model's context window is, for the chats that keep their own
 * transcript: the Studio, the architect and planner chats, and every entity
 * build chat.
 *
 * These surfaces send the whole conversation back with every turn, so they grow
 * until the model refuses them. Until now that arrived as a red provider string
 * at the moment the chat stopped working, with no warning on the way there and
 * no obvious way out. The meter makes the approach visible while there is still
 * room to act, and the overflow state says what to do about it.
 *
 * Feed it `usage` from `useContextUsage` (see `contextUsage.js`).
 */

function formatTokens(n) {
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n || 0);
}

/**
 * The meter itself.
 *
 * @param {object}   props.usage    from `useContextUsage`.
 * @param {function} [props.onClear] clear-the-chat action; offered only where
 *   the context is actually tight, so the control appears when it is the answer.
 * @param {string}   [props.className]
 */
export default function ContextMeter({ usage, onClear = null, className = '' }) {
  const { t } = useI18n();
  const { used = 0, window: limit = 0, overflow = false } = usage || {};

  // Nothing measured and nothing failed: the chat has not run yet, or the
  // model's window is unknown.
  if (!overflow && (!limit || !used)) return null;

  const clearButton = onClear ? (
    <button
      onClick={onClear}
      className="inline-flex items-center gap-1 shrink-0 text-[11px] font-medium underline decoration-dotted hover:no-underline"
    >
      <Trash2 className="w-3 h-3" /> {t('contextMeter.clearChat')}
    </button>
  ) : null;

  if (overflow) {
    return (
      <div
        className={`flex items-start gap-1.5 rounded-md border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/50 px-2 py-1.5 text-[11px] text-red-700 dark:text-red-300 ${className}`}
      >
        <AlertTriangle className="w-3.5 h-3.5 mt-px shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="font-medium">{t('contextMeter.overflowTitle')}</div>
          <div className="opacity-90">{t('contextMeter.overflowHint')}</div>
        </div>
        {clearButton}
      </div>
    );
  }

  const ratio = used / limit;
  const pct = Math.min(100, Math.round(ratio * 100));
  const high = ratio >= CONTEXT_HIGH;
  const warn = ratio >= CONTEXT_WARN;
  const tone = high
    ? 'text-red-600 dark:text-red-400'
    : warn ? 'text-amber-600 dark:text-amber-400' : 'text-gray-400 dark:text-gray-500';
  const bar = high ? 'bg-red-500' : warn ? 'bg-amber-500' : 'bg-gray-300 dark:bg-gray-600';

  return (
    <div className={`flex items-center gap-2 text-[11px] ${tone} ${className}`}>
      <span className="shrink-0">{t('contextMeter.label')}</span>
      <div
        className="flex-1 min-w-[2rem] h-1 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden"
        title={t('contextMeter.tooltip', { used, limit })}
      >
        <div className={`h-full rounded-full ${bar}`} style={{ width: `${Math.max(2, pct)}%` }} />
      </div>
      <span className="shrink-0 tabular-nums">
        {formatTokens(used)}/{formatTokens(limit)} · {pct}%
      </span>
      {/* Offered only once it is the useful move: below that it would read as a
          suggestion to throw the conversation away for no reason. */}
      {warn && clearButton}
    </div>
  );
}
