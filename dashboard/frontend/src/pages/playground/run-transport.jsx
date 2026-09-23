import React from 'react';
import { ChevronLeft, ChevronRight, History, Radio, X } from 'lucide-react';
import { statusLabel, useI18n } from '../../i18n';

const STATUS_STYLES = {
  starting: 'bg-blue-100 text-blue-700',
  running: 'bg-blue-100 text-blue-700',
  stopping: 'bg-amber-100 text-amber-700',
  completed: 'bg-green-100 text-green-700',
  stopped: 'bg-amber-100 text-amber-700',
  failed: 'bg-red-100 text-red-700',
};

/**
 * The estimate banner, the run's status and cost, the tick scrubber and the
 * run picker — everything above the two-column body that is read once and
 * left alone rather than watched, so it earns the strip above the columns
 * rather than a place inside either of them.
 */
export function RunTransport({
  estimate, onDismissEstimate, run, live, ticks, cursor, setCursor,
  following, setFollowing, currentTick, runs, onSelectRun, onOpenHistory,
}) {
  const { t } = useI18n();
  return (
    <div className="shrink-0 bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
      {estimate && (
        <div className="rounded-lg border border-indigo-100 bg-indigo-50 p-3 text-xs text-indigo-800 flex items-start gap-2">
          <div className="flex-1">
            <span className="font-bold">
              {t('playground.projectedCost', { cost: estimate.estimated_total_cost.toFixed(4) })}
            </span>
            {' — '}
            {t('playground.estimateBreakdown', {
              agents: estimate.agents,
              ticks: estimate.max_ticks,
              calls: estimate.llm_calls,
            })}{' '}
            <span className="text-indigo-600">
              {t(`playground.${estimate.note_key}`, { defaultValue: estimate.note })}
            </span>
          </div>
          <button
            onClick={onDismissEstimate}
            aria-label={t('common.dismiss')}
            title={t('common.dismiss')}
            className="shrink-0 text-indigo-400 hover:text-indigo-700"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {run && (
        <div className={`flex items-center gap-3 flex-wrap ${estimate ? 'mt-4 pt-3 border-t border-gray-100' : ''}`}>
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${STATUS_STYLES[run.status] || 'bg-gray-100 text-gray-600'}`}>
            {statusLabel(run.status, t)}
          </span>
          {live && (
            <span className="inline-flex items-center gap-1 text-xs text-blue-600">
              <Radio className="w-3 h-3 animate-pulse" /> {t('playground.live')}
            </span>
          )}
          <span className="text-xs text-gray-500">
            {t('playground.tickProgress', {
              tick: currentTick?.tick ?? 0,
              total: run.ticks_done || 0,
            })}
          </span>
          <span className="text-xs font-semibold text-gray-700">
            ${(run.total_cost || 0).toFixed(4)}
          </span>
          {run.error && <span className="text-xs text-amber-700">{run.error}</span>}

          {/* The right-hand group is one block pinned to the edge, not
              three items that happen to be last: the scrubber only
              exists once a tick has landed, and hanging `ml-auto` on it
              meant the run picker and the history button sat on the left
              for the first seconds of every run and then jumped right
              when the first tick arrived. */}
          <div className="flex items-center gap-3 flex-wrap ml-auto">
            {/* Tick scrubber — ticks are a cleaner axis than wall time. */}
            {ticks.length > 0 && (
              <div className="flex items-center gap-2">
                <button
                  onClick={() => { setFollowing(false); setCursor((c) => Math.max(0, c - 1)); }}
                  className="p-1 text-gray-400 hover:text-gray-700"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <input
                  type="range" min={0} max={ticks.length - 1} value={cursor}
                  onChange={(e) => { setFollowing(false); setCursor(parseInt(e.target.value, 10)); }}
                  className="w-48"
                />
                <button
                  onClick={() => {
                    const next = Math.min(ticks.length - 1, cursor + 1);
                    setCursor(next);
                    if (next === ticks.length - 1) setFollowing(true);
                  }}
                  className="p-1 text-gray-400 hover:text-gray-700"
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
                <button
                  onClick={() => { setFollowing(true); setCursor(ticks.length - 1); }}
                  className={`text-xs font-semibold px-2 py-1 rounded ${
                    following ? 'bg-indigo-100 text-indigo-700' : 'text-gray-500 hover:text-gray-800'
                  }`}
                >
                  {t('playground.follow')}
                </button>
              </div>
            )}

            {/* The picker is the quick hop between neighbouring runs; when
                the question is which run, the history tab is the list that
                can answer it, so it sits right next to it. */}
            {runs.length > 1 && (
              <select
                value={run.sim_run_id}
                onChange={(e) => onSelectRun(e.target.value)}
                className="text-xs border border-gray-300 rounded-md px-2 py-1"
              >
                {runs.map((r) => (
                  <option key={r.sim_run_id} value={r.sim_run_id}>
                    {new Date(r.started_at).toLocaleString()} ({r.status})
                  </option>
                ))}
              </select>
            )}
            <button
              onClick={onOpenHistory}
              title={t('playground.runHistory')}
              className="inline-flex items-center gap-1 text-xs font-semibold text-gray-500 hover:text-indigo-700 border border-gray-200 rounded-md px-2 py-1"
            >
              <History className="w-3.5 h-3.5" />
              {runs.length > 0 && runs.length}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default RunTransport;
