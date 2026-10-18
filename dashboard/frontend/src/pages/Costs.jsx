import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { DollarSign, RefreshCw, PieChart, ListOrdered } from 'lucide-react';
import { useWorkspace } from '../components/workspace';
import { getCosts } from '../api';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import DateInput from '../components/DateInput';
import { usePageChatSubject } from '../components/pageChat/pageChat';
import PageLoader from '../components/PageLoader';
import CostRunsTab from '../components/costs/CostRunsTab';
const card = 'bg-white p-6 rounded-xl shadow-sm border border-gray-100';
const inputCls = 'border border-gray-300 rounded-lg px-2 py-1 text-sm focus:outline-none';

const fmtInt = (n) => (n || 0).toLocaleString();
const fmtUsd = (n) => `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

// One breakdown table (by workspace / agent / model / project).
function Breakdown({ title, rows }) {
  const { t } = useI18n();
  return (
    <div className={card}>
      <h3 className="text-sm font-semibold text-gray-700 mb-3">{title}</h3>
      {(!rows || rows.length === 0) ? (
        <p className="text-sm text-gray-400">{t('costs.noRecordedSpend')}</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b border-gray-100">
              <th className="py-1.5 font-medium">{t('costs.name')}</th>
              <th className="py-1.5 font-medium text-right">{t('costs.runs')}</th>
              <th className="py-1.5 font-medium text-right">{t('costs.tokens')}</th>
              <th className="py-1.5 font-medium text-right" title={t('costs.cachedHint')}>{t('costs.cached')}</th>
              <th className="py-1.5 font-medium text-right">{t('costs.cost')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-b border-gray-50 last:border-0">
                <td className="py-1.5 text-gray-800 truncate max-w-[16rem]" title={r.label}>{r.label}</td>
                <td className="py-1.5 text-right text-gray-600">{fmtInt(r.runs)}</td>
                <td className="py-1.5 text-right text-gray-600">{fmtInt(r.total_tokens)}</td>
                {/* Cache reads are a slice of the input tokens already counted
                    in the column to the left, billed at the cheaper rate. */}
                <td className="py-1.5 text-right text-gray-400" title={t('costs.cachedHint')}>
                  {r.cached_tokens > 0 ? fmtInt(r.cached_tokens) : '—'}
                </td>
                <td className="py-1.5 text-right font-medium text-gray-800">{fmtUsd(r.cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function Costs() {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const [scopeChoice, setScope] = useState('workspace'); // 'workspace' | 'all'
  // Only the default workspace may look across all workspaces; anywhere else
  // the page shows the current workspace and the picker is hidden.
  const isDefaultWorkspace = selectedWorkspace === 'default';
  const scope = isDefaultWorkspace ? scopeChoice : 'workspace';
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  // The tab lives in the address (?tab=runs), so a link can open the run list.
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') === 'runs' ? 'runs' : 'overview';
  const setTab = (id) => setParams(id === 'overview' ? {} : { tab: id }, { replace: true });
  // Bumped by Refresh, so the run list refetches along with the breakdowns.
  const [refreshTick, setRefreshTick] = useState(0);

  const wsParam = scope === 'workspace' ? (selectedWorkspace || undefined) : undefined;

  const loadCosts = useCallback(() => {
    setLoading(true);
    getCosts({ workspace: wsParam, since: since || undefined, until: until || undefined })
      .then(({ data }) => setData(data))
      .catch((e) => console.error('Error fetching costs:', e))
      .finally(() => setLoading(false));
  }, [wsParam, since, until]);

  useEffect(() => {
    // loadCosts is shared with the Refresh button and owns the loading flag.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- shared loader
    loadCosts();
  }, [loadCosts]);
  const totals = data?.totals || { runs: 0, total_tokens: 0, cached_tokens: 0, cost: 0 };

  // What the page chat is looking at. The costs page has no records to hand
  // over — its subject is a window over runs, and which window is the whole
  // question, so it is described rather than pointed at.
  usePageChatSubject({
    title: t('costs.title'),
    hints: [
      `Scope: ${scope === 'workspace' ? (selectedWorkspace || 'the current workspace') : 'all workspaces'}`,
      `Period: ${since || 'the beginning'} to ${until || 'now'}`,
      `Totals on screen: ${totals.runs} runs, ${totals.total_tokens} tokens, $${(totals.cost || 0).toFixed(2)}`,
    ].join('\n'),
  });

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={DollarSign}
        title={t('costs.title')}
        description={t('costs.estimatedTokenSpendFromRecorded')}
        actions={<>
          {/* The Runs tab draws these in its own filter bar. */}
          {tab === 'overview' && isDefaultWorkspace && (
            <select value={scope} onChange={(e) => setScope(e.target.value)} className={inputCls}>
              <option value="workspace">{t('costs.thisWorkspace')}{selectedWorkspace ? ` (${selectedWorkspace})` : ''}</option>
              <option value="all">{t('costs.allWorkspaces')}</option>
            </select>
          )}
          {tab === 'overview' && <>
            <label className="text-xs text-gray-500">{t('costs.from')}</label>
            <DateInput value={since} onChange={setSince} className={inputCls} />
            <label className="text-xs text-gray-500">{t('costs.to')}</label>
            <DateInput value={until} onChange={setUntil} className={inputCls} />
          </>}
          <button onClick={() => { loadCosts(); setRefreshTick((n) => n + 1); }} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-100 hover:bg-gray-200 text-gray-700">
            <RefreshCw className="w-4 h-4" /> {t('costs.refresh')}
          </button>
        </>}
      />

      {/* Tabs sit flush on their content: the page's space-y gap applies to
          this block as a whole, the blocks inside a tab keep their own. */}
      <div>
      <div className="flex items-center gap-1 border-b border-gray-200 overflow-x-auto">
        {[
          { id: 'overview', label: t('costs.tabs.overview'), icon: PieChart },
          { id: 'runs', label: t('costs.tabs.runs'), icon: ListOrdered },
        ].map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex shrink-0 items-center gap-2 px-3 sm:px-4 py-2.5 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
              tab === id ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
      </div>

      {tab === 'runs' && (
        <CostRunsTab
          workspace={wsParam} since={since} until={until} setSince={setSince} setUntil={setUntil}
          scope={scope} setScope={setScope} canPickScope={isDefaultWorkspace}
          selectedWorkspace={selectedWorkspace} refreshTick={refreshTick}
        />
      )}

      {tab === 'overview' && <div className="space-y-6">
      {/* Totals */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className={card}>
          <div className="text-xs text-gray-500 uppercase tracking-wide">{t('costs.totalCost')}</div>
          <div className="text-3xl font-bold text-gray-800 mt-1">{fmtUsd(totals.cost)}</div>
          {totals.container_cost > 0 && (
            <div className="text-xs text-gray-400 mt-1" title={t('costs.containerHint')}>
              {t('costs.ofWhichContainers', { amount: fmtUsd(totals.container_cost) })}
            </div>
          )}
        </div>
        <div className={card}>
          <div className="text-xs text-gray-500 uppercase tracking-wide">{t('costs.runs')}</div>
          <div className="text-3xl font-bold text-gray-800 mt-1">{fmtInt(totals.runs)}</div>
        </div>
        <div className={card}>
          <div className="text-xs text-gray-500 uppercase tracking-wide">{t('costs.totalTokens')}</div>
          <div className="text-3xl font-bold text-gray-800 mt-1">{fmtInt(totals.total_tokens)}</div>
          {totals.cached_tokens > 0 && (
            <div className="text-xs text-gray-400 mt-1" title={t('costs.cachedHint')}>
              {t('costs.ofWhichCached', { count: fmtInt(totals.cached_tokens) })}
            </div>
          )}
        </div>
      </div>

      {loading ? (
        <PageLoader label={t('costs.loadingCosts')} />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Breakdown title={t('costs.byModel')} rows={data?.by_model} />
          <Breakdown title={t('costs.byAgent')} rows={data?.by_agent} />
          {scope === 'all' && <Breakdown title={t('costs.byWorkspace')} rows={data?.by_workspace} />}
          <Breakdown title={t('costs.byProject')} rows={data?.by_project} />
        </div>
      )}

      </div>}
      </div>
    </PageContainer>
  );
}
