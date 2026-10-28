/**
 * The Guardrails page's event log (GET /api/guardrails/events): a filter by
 * guardrail, action and stage, the sort and batch size every long list has,
 * and "Load more" under the table (useListPaging). Refetched when the page's
 * Refresh bumps `refreshKey`.
 */
import { useCallback, useEffect, useState } from 'react';
import { getGuardrailEvents } from '../../api/guardrails';
import { useI18n } from '../../i18n';
import { ListLoadMore, ListPagingControls } from '../ListPaging';
import { useListPaging } from '../listPagingState';
import PageLoader from '../PageLoader';
import GuardrailEventsTable from './GuardrailEventsTable';

const SORT_KEYS = ['at', 'guardrail', 'stage', 'action'];
const ACTIONS = ['block', 'warn', 'ask'];
const STAGES = ['input', 'output', 'tool'];
const FIELD = 'px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none';

export default function GuardrailEventsPanel({ workspace, guardrails, refreshKey }) {
  const { t } = useI18n();
  const [filters, setFilters] = useState({ guardrail_id: '', action: '', stage: '' });
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const paging = useListPaging('guardrailEvents', {
    sorts: SORT_KEYS, defaultSort: 'at', resetOn: [filters, workspace],
  });
  const pageParams = paging.params;
  const queryKey = JSON.stringify([filters, pageParams, workspace, refreshKey]);
  const [loadedKey, setLoadedKey] = useState(null);
  const loading = loadedKey !== queryKey;

  // A promise chain, so the effect below calls it without a synchronous setState.
  const fetchEvents = useCallback(() => {
    const params = { ...pageParams, ...(workspace ? { workspace } : {}) };
    Object.entries(filters).forEach(([k, v]) => { if (v) params[k] = v; });
    return getGuardrailEvents(params)
      .then(({ data }) => {
        setRows(data?.items || []);
        setTotal(data?.total || 0);
      })
      .catch((err) => { console.error('Failed to load guardrail events', err); })
      .then(() => setLoadedKey(queryKey));
  }, [filters, pageParams, workspace, queryKey]);

  useEffect(() => { fetchEvents(); }, [fetchEvents]);

  const applyFilter = (patch) => setFilters((f) => ({ ...f, ...patch }));
  const firstLoad = loading && loadedKey === null;

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
      <h3 className="text-sm font-bold text-gray-900">{t('guardrails.events.title')}</h3>
      <div className="flex flex-wrap items-center gap-3">
        <select aria-label={t('guardrails.events.guardrail')} className={FIELD}
          value={filters.guardrail_id} onChange={(e) => applyFilter({ guardrail_id: e.target.value })}>
          <option value="">{t('guardrails.events.allGuardrails')}</option>
          {(guardrails || []).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </select>
        <select aria-label={t('guardrails.events.actionCol')} className={FIELD}
          value={filters.action} onChange={(e) => applyFilter({ action: e.target.value })}>
          <option value="">{t('guardrails.events.anyAction')}</option>
          {ACTIONS.map((a) => <option key={a} value={a}>{t(`guardrails.action.${a}`).split(':')[0]}</option>)}
        </select>
        <select aria-label={t('guardrails.events.stageCol')} className={FIELD}
          value={filters.stage} onChange={(e) => applyFilter({ stage: e.target.value })}>
          <option value="">{t('guardrails.events.anyStage')}</option>
          {STAGES.map((s) => <option key={s} value={s}>{t(`guardrails.stage.${s}`)}</option>)}
        </select>
        <ListPagingControls
          paging={paging}
          options={SORT_KEYS}
          labels={{
            at: t('guardrails.events.at'),
            guardrail: t('guardrails.events.guardrail'),
            stage: t('guardrails.events.stageCol'),
            action: t('guardrails.events.actionCol'),
          }}
          compact
        />
        <span className="text-xs text-gray-400">{t('guardrails.events.matching', { count: total })}</span>
      </div>
      {firstLoad ? <PageLoader /> : <GuardrailEventsTable events={rows} />}
      <ListLoadMore paging={paging} shown={rows.length} total={total} loading={loading} />
    </div>
  );
}
