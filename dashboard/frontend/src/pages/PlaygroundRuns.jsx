import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { History, Loader, Search, AlertTriangle } from 'lucide-react';
import { getSimRuns, getScenarios } from '../api';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch } from '../components/stream';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import { RunHistory } from './playground/history';
import { isLiveRun } from './playground/status';

/**
 * Every simulation this install has run, newest first.
 *
 * The scenario page can only offer its own runs, and only once you have picked
 * the scenario — but "what did we run yesterday, and how did it go" is a
 * question about the runs, not about any one scenario. This page answers it:
 * one list across the catalogue, each row opening the run it names.
 */

const PAGE = 50;

// Which end a run came to. Not the same question as its status — a run that
// hit its tick cap and one that was stopped by hand are both finished — but
// status is what you filter a history by, so it is what the chips offer.
const STATUS_FILTERS = ['all', 'running', 'completed', 'stopped', 'failed'];

export default function PlaygroundRuns() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { selectedWorkspace, liveUpdates } = useWorkspace();
  const [runs, setRuns] = useState([]);
  const [maxTicks, setMaxTicks] = useState({});   // scenario_id -> its tick cap
  const [loading, setLoading] = useState(true);
  const [limit, setLimit] = useState(PAGE);
  const [status, setStatus] = useState('all');
  const [query, setQuery] = useState('');
  const [message, setMessage] = useState('');

  // `quiet` keeps the rows on screen while a live update refetches them — a
  // run finishing must not blank the list it is changing one row of.
  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const { data } = await getSimRuns(null, { workspace: selectedWorkspace, limit });
      setRuns(data.runs || []);
    } catch (e) {
      setMessage(e.response?.data?.detail || t('playground.loadRunFailed'));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [selectedWorkspace, limit, t]);

  useEffect(() => { load(); }, [load]);

  // "tick 7 / 20" needs the cap. A run carries its own in its launch snapshot
  // and the row prefers that; this catalogue fetch is the fallback for runs
  // recorded before the snapshot existed, and one fetch covers every row.
  useEffect(() => {
    (async () => {
      try {
        const { data } = await getScenarios(selectedWorkspace);
        setMaxTicks(Object.fromEntries(
          (data.scenarios || []).map((s) => [s.scenario_id, s.max_ticks]),
        ));
      } catch { /* the row falls back to a plain tick count */ }
    })();
  }, [selectedWorkspace]);

  useLiveRefetch(() => load(true), { type: 'sim_runs.changed', enabled: liveUpdates });

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return runs.filter((r) => {
      if (status !== 'all') {
        // The "running" chip means live, which includes a run whose first
        // tick has not landed yet.
        if (status === 'running' ? !isLiveRun(r) : r.status !== status) return false;
      }
      if (q && !(r.scenario_name || '').toLowerCase().includes(q)) return false;
      return true;
    });
  }, [runs, status, query]);

  return (
    <PageContainer>
      <PageHeader
        icon={History}
        title={t('playground.runHistory')}
        description={t('playground.runHistoryDescription')}
        backTo="/playground"
        backLabel={t('playground.scenarios')}
        actions={
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t('playground.filterByScenario')}
                className="text-xs border border-gray-300 rounded-lg pl-8 pr-3 py-2 w-52"
              />
            </div>
            <div className="inline-flex rounded-lg border border-gray-300 overflow-hidden">
              {STATUS_FILTERS.map((s) => (
                <button
                  key={s} onClick={() => setStatus(s)}
                  className={`px-2.5 py-1.5 text-xs font-semibold ${
                    status === s ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600'
                  }`}
                >
                  {t(`playground.runFilter.${s}`)}
                </button>
              ))}
            </div>
          </div>
        }
      />

      {message && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {message}
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-gray-500 py-10">
          <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
        </div>
      ) : (
        <>
          <RunHistory
            runs={shown}
            showScenario
            onSelect={(r) => navigate(`/playground/${r.scenario_id}?run=${r.sim_run_id}`)}
            maxTicks={(r) => maxTicks[r.scenario_id]}
            empty={runs.length ? t('playground.noRunsMatch') : t('playground.noRunsAnywhere')}
          />
          {/* Only offered when the window is full: a short list is the whole
              history, and a button that loads nothing is worse than no button. */}
          {runs.length >= limit && (
            <div className="flex justify-center pt-4">
              <button
                onClick={() => setLimit((n) => n + PAGE)}
                className="px-3 py-2 text-xs font-semibold text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-lg hover:bg-indigo-100"
              >
                {t('playground.loadMoreRuns')}
              </button>
            </div>
          )}
        </>
      )}
    </PageContainer>
  );
}
