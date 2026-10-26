import React from 'react';
import { History, Plus, Trash2 } from 'lucide-react';
import { useI18n } from '../../i18n';
import { StatusBadge } from '../loops/loopUi';
import { isRunLive, stopReasonText } from './teamFormat';

/** The team's runs, newest first, in the left column of the team page. */
export default function TeamRunHistory({ runs, selectedId, onSelect, onNew, onDelete, newDisabled = false, className = '' }) {
  const { t } = useI18n();
  return (
    <div className={`flex-col overflow-hidden bg-white ${className}`}>
      {/* A new run: the chat empties and its box waits for the request. */}
      <div className="shrink-0 border-b border-slate-200 p-3">
        <button
          type="button" onClick={onNew} disabled={newDisabled}
          title={t('teamDetails.newChatHint')}
          className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm font-bold text-emerald-700 shadow-sm transition hover:bg-emerald-100 disabled:opacity-50"
          data-testid="team-new-run"
        >
          <Plus className="h-4 w-4" />
          {t('teamDetails.newRun')}
        </button>
      </div>
      {/* The column's title, over the list. */}
      <div className="flex shrink-0 items-center justify-center gap-1.5 px-3 py-3 text-xs font-bold uppercase tracking-wide text-gray-800">
        <History className="w-4 h-4 shrink-0 text-indigo-500" />
        <span className="truncate">{t('teamDetails.runHistory')}</span>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {runs.length === 0 ? (
          <p className="px-3 py-4 text-xs text-gray-500 italic">{t('teamDetails.thisTeamHasNotRun')}</p>
        ) : (
          <ul className="divide-y divide-gray-100" data-testid="team-run-history">
            {runs.map((r) => {
              const active = r.team_run_id === selectedId;
              return (
                <li key={r.team_run_id} className={`relative ${active ? 'bg-indigo-50' : 'hover:bg-gray-50'}`}>
                  <button type="button" onClick={() => onSelect(r.team_run_id)} className="w-full text-left px-3 py-2">
                    <div className="flex items-center gap-2">
                      <StatusBadge status={r.status} />
                      <span className={`ml-auto text-xs text-gray-500 ${onDelete && !isRunLive(r.status) ? 'mr-6' : ''}`}>
                        {t('teamDetails.roundsDone', { count: r.rounds_done || 0 })}
                      </span>
                    </div>
                    <div className={`mt-1 text-xs truncate ${active ? 'text-indigo-900 font-medium' : 'text-gray-700'}`} title={r.goal || ''}>
                      {r.goal || t('teamDetails.noGoal')}
                    </div>
                    <div className="mt-0.5 text-[11px] text-gray-400 break-words">
                      {r.started_at ? new Date(r.started_at).toLocaleString() : ''}
                      {r.total_cost ? ` · $${r.total_cost.toFixed(4)}` : ''}
                      {stopReasonText(r, t) ? ` · ${stopReasonText(r, t)}` : ''}
                    </div>
                  </button>
                  {/* A run still going cannot be deleted: its next turn would
                      write the board back. */}
                  {onDelete && !isRunLive(r.status) && (
                    <button
                      type="button"
                      onClick={() => onDelete(r)}
                      title={t('teamDetails.deleteRun')} aria-label={t('teamDetails.deleteRun')}
                      data-testid="team-history-delete"
                      className="absolute right-2 top-1.5 rounded-md p-1 text-slate-300 transition hover:bg-rose-50 hover:text-rose-600"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
