import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Clock, X } from 'lucide-react';

import { getAgents, getCostRuns } from '../../api';
import { statusLabel, useI18n } from '../../i18n';
import DateInput from '../DateInput';
import { ListLoadMore, ListPagingControls } from '../ListPaging';
import { useListPaging } from '../listPagingState';
import PageLoader from '../PageLoader';
import RunStatusBadge from '../RunStatusBadge';
import RunModelCell from '../run/RunModelCell';
import { fmtRunUsd } from '../dashboard/format';

/*
 * The Runs tab of the Costs page: every run the breakdowns count, one row each
 * with its tokens and estimated cost (GET /api/costs/runs, common/costs_report.py
 * costs_runs). Same filters as the page, so the rows add up to its totals.
 * Drawn the way the Runs page (pages/Messages.jsx) draws its list: a filter
 * card, then a card of grid rows that open the run. The workspace scope and
 * the dates are the page's own (shared with the Overview tab); the agent and
 * status filters are this list's.
 */

// Sort keys the backend orders by (common/costs_report.py RUN_SORTS).
const SORTS = ['started', 'cost', 'tokens', 'agent', 'model'];

// The Workspace column only shows across all workspaces. Written out in full
// so Tailwind's JIT can detect them.
const GRID_WITH_WORKSPACE = 'md:grid-cols-[minmax(0,2.3fr)_1fr_1.1fr_minmax(0,1.3fr)_1fr_0.9fr_0.8fr_0.9fr]';
const GRID = 'md:grid-cols-[minmax(0,2.3fr)_1fr_1.1fr_minmax(0,1.3fr)_0.9fr_0.8fr_0.9fr]';
const STATUSES = ['running', 'completed', 'failed', 'error', 'stopped', 'stop'];
const FIELD = 'w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none';
const LABEL = 'block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider';
const MOBILE_LABEL = 'md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1';

const fmtInt = (n) => (n || 0).toLocaleString();

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
}

export default function CostRunsTab({
  workspace, since, until, setSince, setUntil,
  scope, setScope, canPickScope, selectedWorkspace, refreshTick,
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [agentId, setAgentId] = useState('');
  const [status, setStatus] = useState('');
  const [agents, setAgents] = useState([]);
  const showWorkspace = scope === 'all';

  // The agent filter belongs to a scope: another one starts over.
  const [seenWorkspace, setSeenWorkspace] = useState(workspace);
  if (seenWorkspace !== workspace) {
    setSeenWorkspace(workspace);
    setAgentId('');
  }
  useEffect(() => {
    let ignore = false;
    getAgents(workspace || undefined)
      .then(({ data }) => { if (!ignore) setAgents(Array.isArray(data) ? data : []); })
      .catch(() => {});
    return () => { ignore = true; };
  }, [workspace]);

  const paging = useListPaging('costRuns', {
    sorts: SORTS,
    defaultSort: 'started',
    resetOn: [workspace, since, until, agentId, status],
  });
  const pageParams = paging.params;
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState('');
  // Loading is derived from the query the rows on screen were fetched for.
  const queryKey = JSON.stringify([workspace, since, until, agentId, status, pageParams, refreshTick]);
  const [loadedKey, setLoadedKey] = useState(null);
  const loading = loadedKey !== queryKey;

  useEffect(() => {
    let ignore = false;
    getCostRuns({ ...pageParams, workspace: workspace || undefined,
      since: since || undefined, until: until || undefined,
      agent_id: agentId || undefined, status: status || undefined })
      .then(({ data }) => {
        if (ignore) return;
        setRows(data?.items || []);
        setTotal(data?.total || 0);
        setError('');
      })
      .catch((e) => { if (!ignore) setError(e?.response?.data?.detail || t('costs.runsTab.loadFailed')); })
      .finally(() => { if (!ignore) setLoadedKey(queryKey); });
    return () => { ignore = true; };
  }, [workspace, since, until, agentId, status, pageParams, queryKey, t]);

  const grid = showWorkspace ? GRID_WITH_WORKSPACE : GRID;
  const hasFilters = agentId || status || since || until || (canPickScope && scope === 'all');
  const clearFilters = () => {
    setAgentId('');
    setStatus('');
    setSince('');
    setUntil('');
    setScope('workspace');
  };
  const open = (runId) => navigate(`/messages/${encodeURIComponent(runId)}`);

  return (
    <div className="space-y-4">
      {/* Filter bar, laid out like the Runs page's. */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="flex flex-wrap gap-4 items-end">
          {canPickScope && (
            <div className="flex-1 min-w-[160px]">
              <label className={LABEL}>{t('costs.runsTab.workspace')}</label>
              <select className={FIELD} value={scope} onChange={(e) => setScope(e.target.value)}>
                <option value="workspace">{t('costs.thisWorkspace')}{selectedWorkspace ? ` (${selectedWorkspace})` : ''}</option>
                <option value="all">{t('costs.allWorkspaces')}</option>
              </select>
            </div>
          )}

          <div className="flex-1 min-w-[160px]">
            <label className={LABEL}>{t('costs.runsTab.agent')}</label>
            <select className={FIELD} value={agentId} onChange={(e) => setAgentId(e.target.value)}>
              <option value="">{t('messages.allAgents')}</option>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
            </select>
          </div>

          <div className="flex-1 min-w-[140px]">
            <label className={LABEL}>{t('costs.runsTab.status')}</label>
            <select className={FIELD} value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">{t('messages.allStatuses')}</option>
              {STATUSES.map((s) => <option key={s} value={s}>{statusLabel(s, t)}</option>)}
            </select>
          </div>

          <div className="flex-1 min-w-[160px]">
            <label className={LABEL}>{t('costs.from')}</label>
            <DateInput className={FIELD} value={since} onChange={setSince} />
          </div>

          <div className="flex-1 min-w-[160px]">
            <label className={LABEL}>{t('costs.to')}</label>
            <DateInput className={FIELD} value={until} onChange={setUntil} />
          </div>

          <ListPagingControls paging={paging} options={SORTS} />

          {hasFilters && (
            <button
              onClick={clearFilters}
              className="flex items-center gap-1 px-3 py-2 text-sm text-gray-500 hover:text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50"
            >
              <X className="w-4 h-4" />
              {t('messages.clear')}
            </button>
          )}
        </div>
      </div>

      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>
      )}

      {loading && !rows.length ? (
        <div className="bg-white rounded-xl border border-gray-200"><PageLoader label={t('costs.loadingCosts')} /></div>
      ) : rows.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 text-center py-16">
          <Clock className="w-10 h-10 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500 text-sm">{t('costs.noRecordedSpend')}</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className={`hidden md:grid ${grid} gap-3 px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-gray-500 bg-gray-50 border-b border-gray-200`}>
            <div>{t('costs.runsTab.run')}</div>
            <div className="text-center">{t('costs.runsTab.status')}</div>
            <div className="text-center">{t('costs.runsTab.agent')}</div>
            <div className="text-center">{t('costs.runsTab.model')}</div>
            {showWorkspace && <div className="text-center">{t('costs.runsTab.workspace')}</div>}
            <div className="text-right">{t('costs.tokens')}</div>
            <div className="text-right" title={t('costs.cachedHint')}>{t('costs.cached')}</div>
            <div className="text-right">{t('costs.cost')}</div>
          </div>
          <div className="divide-y divide-gray-100">
            {rows.map((r) => (
              <div
                key={r.run_id}
                role="button"
                tabIndex={0}
                onClick={() => open(r.run_id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(r.run_id); }
                }}
                className={`grid grid-cols-1 ${grid} gap-3 px-4 py-3 hover:bg-indigo-50/40 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-inset`}
              >
                <div className="min-w-0">
                  <div className="font-medium text-gray-900 truncate">
                    {r.title || <span className="text-gray-400 italic">{t('messages.noTitle')}</span>}
                  </div>
                  <div className="text-xs text-gray-500 mt-1 truncate">{fmtDate(r.started_at)}</div>
                  <div className="text-xs text-gray-400 mt-0.5 truncate">{r.run_id}</div>
                </div>

                <div className="md:self-center md:flex md:justify-center">
                  <div className={MOBILE_LABEL}>{t('costs.runsTab.status')}</div>
                  <RunStatusBadge status={r.status} />
                </div>

                <div className="text-sm text-gray-700 md:self-center md:text-center min-w-0">
                  <div className={MOBILE_LABEL}>{t('costs.runsTab.agent')}</div>
                  <span className="text-xs truncate" title={r.agent_id}>{r.agent_name || r.agent_id || '—'}</span>
                </div>

                <div className="text-sm text-gray-700 md:self-center md:text-center min-w-0">
                  <div className={MOBILE_LABEL}>{t('costs.runsTab.model')}</div>
                  <RunModelCell run={r} />
                </div>

                {showWorkspace && (
                  <div className="text-sm text-gray-700 md:self-center md:text-center">
                    <div className={MOBILE_LABEL}>{t('costs.runsTab.workspace')}</div>
                    {r.workspace || '—'}
                  </div>
                )}

                <div className="text-sm text-gray-700 md:self-center md:text-right"
                  title={t('costs.runsTab.inOut', { input: fmtInt(r.inbound_tokens), output: fmtInt(r.outbound_tokens) })}>
                  <div className={MOBILE_LABEL}>{t('costs.tokens')}</div>
                  {fmtInt(r.total_tokens)}
                </div>

                <div className="text-sm text-gray-400 md:self-center md:text-right" title={t('costs.cachedHint')}>
                  <div className={MOBILE_LABEL}>{t('costs.cached')}</div>
                  {r.cached_tokens > 0 ? fmtInt(r.cached_tokens) : '—'}
                </div>

                <div className="text-sm md:self-center md:text-right">
                  <div className={MOBILE_LABEL}>{t('costs.cost')}</div>
                  {r.priced ? (
                    <span className="font-medium text-gray-900">{fmtRunUsd(r.cost)}</span>
                  ) : (
                    <span className="text-gray-400" title={t('messageDetails.costNoPriceTooltip')}>
                      {t('messageDetails.costNoPrice')}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <ListLoadMore paging={paging} shown={rows.length} total={total} loading={loading} />
    </div>
  );
}
