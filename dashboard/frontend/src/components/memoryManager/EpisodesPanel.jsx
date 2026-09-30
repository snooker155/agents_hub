/**
 * Episodes panel — discrete event log scoped to a pool.
 */
import { useState, useEffect, useCallback } from 'react';
import { Trash2, RefreshCw } from 'lucide-react';
import { listMemoryEpisodes, deleteMemoryEpisode } from '../../api';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';
import { EPISODE_KIND_COLOR, EPISODE_OUTCOME_COLOR, fmt } from './helpers';

function EpisodesPanel({ poolId, stats, onChange }) {
  const { t } = useI18n();
  const toast = useToast();
  const [episodes, setEpisodes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [kindFilter, setKindFilter] = useState('');
  const [outcomeFilter, setOutcomeFilter] = useState('');
  const [query, setQuery] = useState('');

  const load = useCallback(async () => {
    if (!poolId) return;
    setLoading(true);
    try {
      const params = { limit: 100 };
      if (kindFilter) params.kind = kindFilter;
      if (outcomeFilter) params.outcome = outcomeFilter;
      if (query.trim()) params.query = query.trim();
      const r = await listMemoryEpisodes(poolId, params);
      setEpisodes(r.data?.episodes || []);
    } catch {
      setEpisodes([]);
    } finally {
      setLoading(false);
    }
  }, [poolId, kindFilter, outcomeFilter, query]);

  useEffect(() => { load(); }, [load]);

  const handleDelete = async (epId) => {
    if (!window.confirm(t('memoryManager.confirmDeleteEpisode'))) return;
    try {
      await deleteMemoryEpisode(poolId, epId);
      await load();
      onChange?.();
    } catch (e) {
      toast.error(t('memoryManager.errors.deleteEpisode'), errorDetail(e));
    }
  };

  const cap = stats?.cap;
  const total = stats?.total ?? 0;

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="text-xs text-gray-500">
          {t('memoryManager.episodesStored', { count: total })}{cap ? ` · ${t('memoryManager.cap', { cap })}` : ''}
          {stats?.by_kind && Object.keys(stats.by_kind).length > 0 && (
            <span className="ml-2">
              ({Object.entries(stats.by_kind).map(([k, v]) => `${k}: ${v}`).join(', ')})
            </span>
          )}
        </div>
        <button onClick={load} className="text-xs text-indigo-600 hover:text-indigo-800 flex items-center gap-1">
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> {t('memoryManager.refresh')}
        </button>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <select value={kindFilter} onChange={e => setKindFilter(e.target.value)}
          className="text-xs border border-gray-200 rounded px-2 py-1 bg-white">
          <option value="">{t('memoryManager.allKinds')}</option>
          <option value="interaction">{t('memoryManager.interaction')}</option>
          <option value="task">{t('memoryManager.task')}</option>
          <option value="decision">{t('memoryManager.decision')}</option>
          <option value="error">{t('memoryManager.error')}</option>
          <option value="observation">{t('memoryManager.observation')}</option>
        </select>
        <select value={outcomeFilter} onChange={e => setOutcomeFilter(e.target.value)}
          className="text-xs border border-gray-200 rounded px-2 py-1 bg-white">
          <option value="">{t('memoryManager.allOutcomes')}</option>
          <option value="success">{t('memoryManager.success')}</option>
          <option value="failure">{t('memoryManager.failure')}</option>
          <option value="partial">{t('memoryManager.partial')}</option>
          <option value="n/a">N/A</option>
        </select>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder={t('memoryManager.keywordSearch')}
          className="text-xs border border-gray-200 rounded px-2 py-1 bg-white flex-1 min-w-[140px]"
        />
      </div>

      {episodes.length === 0 ? (
        <p className="p-6 text-center text-gray-400 text-sm italic">
          {loading ? t('common.loading') : t('memoryManager.noEpisodesMatch')}
        </p>
      ) : (
        <div className="space-y-2">
          {episodes.map((e) => {
            const kindCls = EPISODE_KIND_COLOR[e.kind] || 'bg-gray-100 text-gray-700';
            const outcomeCls = e.outcome ? (EPISODE_OUTCOME_COLOR[e.outcome] || 'bg-gray-100 text-gray-500') : null;
            return (
              <div key={e.id} className="border border-gray-200 rounded-lg p-3 bg-white">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded ${kindCls}`}>{e.kind}</span>
                    {outcomeCls && (
                      <span className={`text-[10px] font-medium px-2 py-0.5 rounded ${outcomeCls}`}>{e.outcome}</span>
                    )}
                    {e.actor && <span className="text-xs text-gray-500">{t('memoryManager.actor')} <span className="font-mono">{e.actor}</span></span>}
                    {e.subject && <span className="text-xs text-gray-500">{t('memoryManager.subject')} <span className="font-mono">{e.subject}</span></span>}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-[11px] text-gray-400">{fmt(e.occurred_at)}</span>
                    <button onClick={() => handleDelete(e.id)} className="text-gray-300 hover:text-red-500 p-1">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
                <p className="text-sm text-gray-800 mt-2 whitespace-pre-wrap break-words">{e.summary}</p>
                {e.tags && e.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {e.tags.map(t => (
                      <span key={t} className="text-[10px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">{t}</span>
                    ))}
                  </div>
                )}
                {e.details && Object.keys(e.details).length > 0 && (
                  <details className="mt-2">
                    <summary className="text-[11px] text-gray-400 cursor-pointer hover:text-gray-600">{t('memoryManager.details')}</summary>
                    <pre className="text-[11px] bg-gray-50 rounded p-2 mt-1 overflow-x-auto">{JSON.stringify(e.details, null, 2)}</pre>
                  </details>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export { EpisodesPanel };
