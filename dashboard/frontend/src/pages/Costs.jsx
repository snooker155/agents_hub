import { useState, useEffect, useCallback } from 'react';
import { DollarSign, Loader, Save, RefreshCw, AlertTriangle, ShieldCheck } from 'lucide-react';
import { useWorkspace } from '../components/workspace';
import { getCosts, getBudget, setBudget } from '../api';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import DateInput from '../components/DateInput';
import { usePageChatSubject } from '../components/pageChat/pageChat';
const card = 'bg-white p-6 rounded-xl shadow-sm border border-gray-100';
const inputCls = 'border border-gray-300 rounded-lg px-2 py-1 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none';

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
  const [scope, setScope] = useState('workspace'); // 'workspace' | 'all'
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const [budget, setBudgetState] = useState(null);
  const [savingBudget, setSavingBudget] = useState(false);

  const wsParam = scope === 'workspace' ? (selectedWorkspace || undefined) : undefined;

  const loadCosts = useCallback(() => {
    setLoading(true);
    getCosts({ workspace: wsParam, since: since || undefined, until: until || undefined })
      .then(({ data }) => setData(data))
      .catch((e) => console.error('Error fetching costs:', e))
      .finally(() => setLoading(false));
  }, [wsParam, since, until]);

  const loadBudget = useCallback(() => {
    if (!selectedWorkspace) { setBudgetState(null); return; }
    getBudget(selectedWorkspace)
      .then(({ data }) => setBudgetState(data))
      .catch((e) => console.error('Error fetching budget:', e));
  }, [selectedWorkspace]);

  useEffect(() => { loadCosts(); }, [loadCosts]);
  useEffect(() => { loadBudget(); }, [loadBudget]);

  const saveBudget = async () => {
    if (!selectedWorkspace || !budget) return;
    setSavingBudget(true);
    try {
      const { data } = await setBudget(selectedWorkspace, {
        hard_limit_usd: Number(budget.hard_limit_usd) || 0,
        soft_limit_usd: Number(budget.soft_limit_usd) || 0,
        period: budget.period || 'monthly',
      });
      setBudgetState(data);
    } catch (e) {
      console.error('Error saving budget:', e);
    } finally {
      setSavingBudget(false);
    }
  };

  const totals = data?.totals || { runs: 0, total_tokens: 0, cached_tokens: 0, cost: 0 };

  // What the page chat is looking at. The costs page has no records to hand
  // over — its subject is a window over runs, and which window is the whole
  // question, so it is described rather than pointed at.
  usePageChatSubject({
    title: t('costs.costsBudgets'),
    hints: [
      `Scope: ${scope === 'workspace' ? (selectedWorkspace || 'the current workspace') : 'all workspaces'}`,
      `Period: ${since || 'the beginning'} to ${until || 'now'}`,
      `Totals on screen: ${totals.runs} runs, ${totals.total_tokens} tokens, $${(totals.cost || 0).toFixed(2)}`,
      budget ? `Budget: soft $${budget.soft_limit_usd}, hard $${budget.hard_limit_usd}, ${budget.period}`
             : 'No budget set for this workspace',
    ].join('\n'),
  });

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={DollarSign}
        title={t('costs.costsBudgets')}
        description={t('costs.estimatedTokenSpendFromRecorded')}
        actions={<>
          <select value={scope} onChange={(e) => setScope(e.target.value)} className={inputCls}>
            <option value="workspace">{t('costs.thisWorkspace')}{selectedWorkspace ? ` (${selectedWorkspace})` : ''}</option>
            <option value="all">{t('costs.allWorkspaces')}</option>
          </select>
          <label className="text-xs text-gray-500">{t('costs.from')}</label>
          <DateInput value={since} onChange={setSince} className={inputCls} />
          <label className="text-xs text-gray-500">{t('costs.to')}</label>
          <DateInput value={until} onChange={setUntil} className={inputCls} />
          <button onClick={loadCosts} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-100 hover:bg-gray-200 text-gray-700">
            <RefreshCw className="w-4 h-4" /> {t('costs.refresh')}
          </button>
        </>}
      />

      {/* Totals */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className={card}>
          <div className="text-xs text-gray-500 uppercase tracking-wide">{t('costs.totalCost')}</div>
          <div className="text-3xl font-bold text-gray-800 mt-1">{fmtUsd(totals.cost)}</div>
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

      {/* Budget for the selected workspace */}
      {budget && (
        <div className={card}>
          <div className="flex items-center justify-between flex-wrap gap-3 mb-3">
            <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
              {budget.hard_exceeded ? <AlertTriangle className="w-4 h-4 text-red-500" /> : <ShieldCheck className="w-4 h-4 text-emerald-500" />}
              Budget for <span className="text-indigo-600">{selectedWorkspace}</span>
            </h3>
            {budget.enforced && (
              <span className={`text-xs px-2 py-0.5 rounded-full ${budget.hard_exceeded ? 'bg-red-100 text-red-700' : budget.soft_exceeded ? 'bg-amber-100 text-amber-700' : 'bg-emerald-100 text-emerald-700'}`}>
                {fmtUsd(budget.spend)} spent this {budget.period}
                {budget.hard_limit_usd ? ` of ${fmtUsd(budget.hard_limit_usd)}` : ''}
              </span>
            )}
          </div>
          <div className="flex items-end gap-4 flex-wrap">
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('costs.period')}</label>
              <select value={budget.period} onChange={(e) => setBudgetState({ ...budget, period: e.target.value })} className={inputCls}>
                <option value="monthly">{t('costs.monthly')}</option>
                <option value="daily">{t('costs.daily')}</option>
                <option value="total">{t('costs.allTime')}</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('costs.softCapUsd')}</label>
              <input type="number" min="0" step="0.01" value={budget.soft_limit_usd}
                onChange={(e) => setBudgetState({ ...budget, soft_limit_usd: e.target.value })} className={`${inputCls} w-28`} />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('costs.hardCapUsd')}</label>
              <input type="number" min="0" step="0.01" value={budget.hard_limit_usd}
                onChange={(e) => setBudgetState({ ...budget, hard_limit_usd: e.target.value })} className={`${inputCls} w-28`} />
            </div>
            <button onClick={saveBudget} disabled={savingBudget}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-indigo-600 hover:bg-indigo-700 text-white disabled:opacity-60">
              {savingBudget ? <Loader className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Save
            </button>
          </div>
          <p className="text-xs text-gray-500 mt-3">
            The hard cap pauses new task runs for this workspace once reached (set to 0 to disable). The soft cap is advisory. Enforcement fails open — a pricing error never blocks a run.
          </p>
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-gray-500 p-8"><Loader className="w-4 h-4 animate-spin" /> {t('costs.loadingCosts')}</div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Breakdown title={t('costs.byModel')} rows={data?.by_model} />
          <Breakdown title={t('costs.byAgent')} rows={data?.by_agent} />
          {scope === 'all' && <Breakdown title={t('costs.byWorkspace')} rows={data?.by_workspace} />}
          <Breakdown title={t('costs.byProject')} rows={data?.by_project} />
        </div>
      )}
    </PageContainer>
  );
}
