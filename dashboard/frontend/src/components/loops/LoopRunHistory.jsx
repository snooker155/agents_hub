import React from 'react';
import { History, Plus, RotateCcw, Trash2 } from 'lucide-react';
import { useI18n } from '../../i18n';
import { StatusBadge } from './loopUi';
import { hasScore, isLive, isResumable, scoreColor, stopReasonLabel } from './loopFormat';

/** The loop's runs, newest first, in the left column of the loop page. */
export default function LoopRunHistory({ runs, selectedId, onSelect, onResume, resuming, onNew, onDelete, className = '' }) {
  const { t } = useI18n();
  return (
    <div className={`flex-col overflow-hidden bg-white ${className}`}>
      {/* A new run: the chat empties and its box waits for the request. */}
      {onNew && (
        <div className="shrink-0 border-b border-slate-200 p-3">
          <button
            type="button" onClick={onNew}
            className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm font-bold text-emerald-700 shadow-sm transition hover:bg-emerald-100"
            data-testid="loop-new-run"
          >
            <Plus className="h-4 w-4" />
            {t('loops.newRun')}
          </button>
        </div>
      )}
      {/* The column's title, over the list. */}
      <div className="flex shrink-0 items-center justify-center gap-1.5 px-3 py-3 text-xs font-bold uppercase tracking-wide text-gray-800">
        <History className="w-4 h-4 shrink-0 text-indigo-500" />
        <span className="truncate">{t('loops.runHistory')}</span>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {runs.length === 0 ? (
          <p className="px-3 py-4 text-xs text-gray-500 italic">{t('loops.noRunsYetPressRun')}</p>
        ) : (
          <ul className="divide-y divide-gray-100" data-testid="loop-run-history">
            {runs.map((r) => {
              const active = r.loop_run_id === selectedId;
              const score = hasScore(r.final_score) ? r.final_score : r.best_score;
              return (
                <li key={r.loop_run_id} className={`relative ${active ? 'bg-indigo-50' : 'hover:bg-gray-50'}`}>
                  <button type="button" onClick={() => onSelect(r.loop_run_id)} className="w-full text-left px-3 py-2">
                    <div className="flex items-center gap-2">
                      <StatusBadge status={r.status} />
                      {hasScore(score) && (
                        <span className="inline-flex items-center gap-1 text-xs font-semibold text-gray-700">
                          <span className={`w-2 h-2 rounded-full ${scoreColor(score)}`} />
                          {Math.round(score)}
                        </span>
                      )}
                      <span className={`ml-auto text-xs text-gray-500 ${onDelete && !isLive(r) ? 'mr-6' : ''}`}>{t('loops.iterationsShort', { count: r.iterations_done || 0 })}</span>
                    </div>
                    <div className={`mt-1 text-xs truncate ${active ? 'text-indigo-900 font-medium' : 'text-gray-700'}`} title={r.goal || ''}>
                      {r.goal || t('loops.noGoal')}
                    </div>
                    <div className="mt-0.5 text-[11px] text-gray-400 break-words">
                      {r.started_at ? new Date(r.started_at).toLocaleString() : ''}
                      {r.stop_reason ? ` · ${stopReasonLabel(r.stop_reason, t)}` : ''}
                    </div>
                  </button>
                  {isResumable(r) && (
                    <div className="px-3 pb-2 -mt-1">
                      <button
                        type="button"
                        onClick={() => onResume(r.loop_run_id)}
                        disabled={resuming === r.loop_run_id}
                        title={t('loops.resumeHint')}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold border border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100 disabled:opacity-50"
                      >
                        <RotateCcw className="w-3 h-3" />
                        {resuming === r.loop_run_id ? t('loops.resuming') : t('loops.resume')}
                      </button>
                    </div>
                  )}
                  {/* A run still going cannot be deleted: its next pass would
                      write the record back. */}
                  {onDelete && !isLive(r) && (
                    <button
                      type="button"
                      onClick={() => onDelete(r)}
                      title={t('loops.deleteRun')} aria-label={t('loops.deleteRun')}
                      data-testid="loop-history-delete"
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
