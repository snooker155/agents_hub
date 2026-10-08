/**
 * The card a refused turn shows instead of a red error string
 * (chat/refusals.py): the capability guard stopped the agent, or a spending
 * limit is reached. It says the reason in plain words and carries the action in
 * place: "Allow for this agent" records the agent level capability exception
 * (the same endpoint as the agent editor's switch, editors only), a budget
 * refusal raises a workspace limit right here (administrators, or the single
 * operator) or opens the page of the limit to raise. After either, "Try again"
 * sends the same message once more.
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ShieldAlert, Wallet } from 'lucide-react';
import { useI18n } from '../../i18n';
import { MULTI, useAuth } from '../auth';
import { getAgentCapabilityOverride, updateAgentCapabilityOverride, getBudget, setBudget } from '../../api';

const btn = 'inline-flex items-center rounded-lg border px-3 py-1.5 text-xs font-medium disabled:opacity-50';
const primary = `${btn} border-gray-800 bg-gray-800 text-white hover:bg-gray-700`;
const secondary = `${btn} border-gray-300 bg-white text-gray-700 hover:bg-gray-50`;

const usd = (n) => (Number.isFinite(Number(n)) ? Number(n).toFixed(2) : '?');

function joinCaps(t, caps) {
  const names = (caps || []).map((c) => t(`capabilities.labels.${c}`, { defaultValue: c }));
  if (names.length < 2) return names.join('');
  return `${names.slice(0, -1).join(', ')}${t('refusal.guard.capsJoin')}${names[names.length - 1]}`;
}

function GuardRefusal({ refusal, onRetry, canRetry }) {
  const { t } = useI18n();
  const [canEdit, setCanEdit] = useState(null);
  const [state, setState] = useState('idle'); // idle | saving | allowed | blocked
  const [error, setError] = useState('');
  const agentId = refusal.agent_id;
  const allowable = refusal.override_allowed !== false && !!agentId;

  useEffect(() => {
    if (!allowable) return undefined;
    let live = true;
    getAgentCapabilityOverride(agentId)
      .then(({ data }) => { if (live) setCanEdit(data?.can_edit !== false); })
      .catch(() => { if (live) setCanEdit(false); });
    return () => { live = false; };
  }, [agentId, allowable]);

  const allow = async () => {
    setState('saving');
    setError('');
    try {
      const { data } = await updateAgentCapabilityOverride(agentId, true);
      setState(data?.honoured_at_build === false ? 'blocked' : 'allowed');
    } catch (err) {
      setState('idle');
      setError(err?.response?.status === 403 ? t('refusal.guard.forbidden') : t('refusal.guard.failed'));
    }
  };

  const rule = refusal.rule_id;
  const explanation = t(`capabilities.rules.${rule}.explanation`, { defaultValue: refusal.explanation || '' });
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 font-semibold text-amber-900">
        <ShieldAlert className="h-4 w-4" />
        {t('refusal.guard.title')}
      </div>
      {refusal.capabilities?.length >= 2 ? (
        <p>{t('refusal.guard.body', { caps: joinCaps(t, refusal.capabilities) })}</p>
      ) : (
        <p>{explanation}</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {allowable && canEdit && state !== 'allowed' && state !== 'blocked' && (
          <button type="button" className={primary} onClick={allow} disabled={state === 'saving'}>
            {state === 'saving' ? t('refusal.guard.allowing') : t('refusal.guard.allow')}
          </button>
        )}
        {allowable && canEdit === false && <span className="text-xs">{t('refusal.guard.viewerNote')}</span>}
        {!allowable && <span className="text-xs">{t('refusal.guard.noOverride')}</span>}
        {state === 'allowed' && <span className="text-xs text-emerald-700">{t('refusal.guard.allowed')}</span>}
        {state === 'blocked' && <span className="text-xs">{t('refusal.guard.allowedButBlocked')}</span>}
        {(state === 'allowed' || state === 'blocked') && canRetry && (
          <button type="button" className={secondary} onClick={onRetry} title={t('refusal.retryHint')}>
            {t('refusal.retry')}
          </button>
        )}
      </div>
      {error && <p className="text-xs text-red-700">{error}</p>}
    </div>
  );
}

function BudgetRefusal({ refusal, onRetry, canRetry }) {
  const { t } = useI18n();
  const auth = useAuth();
  const mayRaise = auth.mode !== MULTI || auth.user?.role === 'admin';
  const kind = refusal.kind;
  const vars = { spent: usd(refusal.spent_usd), limit: usd(refusal.limit_usd) };
  const hasNumbers = refusal.spent_usd != null && refusal.limit_usd != null;
  let body;
  let to;
  let label;
  if (kind === 'person') {
    body = t('refusal.budget.person', vars);
    to = '/users';
    label = t('refusal.budget.raisePerson');
  } else if (kind === 'turn') {
    body = t('refusal.budget.turn', vars);
    to = refusal.service_id ? `/services/${encodeURIComponent(refusal.service_id)}` : '/services';
    label = t('refusal.budget.raiseTurn');
  } else {
    body = hasNumbers ? t('refusal.budget.workspace', vars) : t('refusal.budget.workspaceNoNumbers');
    to = '/costs';
    label = t('refusal.budget.raiseWorkspace');
  }
  const open = kind === 'turn' || mayRaise;
  // A workspace limit is raised in place: the new hard limit goes through the
  // same POST /costs/budget the Costs page uses, every other budget setting
  // kept as it is, then the turn is sent again.
  const inPlace = kind !== 'person' && kind !== 'turn' && mayRaise && !!refusal.workspace;
  const suggested = Math.max(1, Math.ceil(Number(refusal.limit_usd) * 2 || 10));
  const [amount, setAmount] = useState(String(suggested));
  const [raise, setRaise] = useState('idle'); // idle | saving | done | failed
  const raiseNow = async () => {
    const value = Number(amount);
    if (!Number.isFinite(value) || value <= 0) return;
    setRaise('saving');
    try {
      const { data: current } = await getBudget(refusal.workspace);
      await setBudget(refusal.workspace, {
        hard_limit_usd: value,
        soft_limit_usd: Number(current?.soft_limit_usd) || 0,
        period: current?.period || 'monthly',
        run_limit_usd: Number(current?.run_limit_usd) || 0,
        fail_closed: !!current?.fail_closed,
      });
      setRaise('done');
      if (canRetry) onRetry();
    } catch {
      setRaise('failed');
    }
  };
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 font-semibold text-amber-900">
        <Wallet className="h-4 w-4" />
        {t('refusal.budget.title')}
      </div>
      <p>{body}</p>
      {inPlace && raise !== 'done' && (
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs" htmlFor={`raise-${refusal.workspace}`}>{t('refusal.budget.raiseTo')}</label>
          <span className="text-xs">$</span>
          <input
            id={`raise-${refusal.workspace}`}
            type="number"
            min="1"
            step="1"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            className="w-24 rounded-lg border border-amber-300 bg-white px-2 py-1 text-xs"
          />
          <button type="button" className={primary} onClick={raiseNow} disabled={raise === 'saving'}>
            {raise === 'saving' ? t('refusal.budget.raising') : (canRetry ? t('refusal.budget.raiseAndRetry') : t('refusal.budget.raise'))}
          </button>
        </div>
      )}
      {raise === 'done' && <p className="text-xs text-green-800">{t('refusal.budget.raised', { limit: usd(amount) })}</p>}
      {raise === 'failed' && <p className="text-xs text-red-700">{t('refusal.budget.raiseFailed')}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {open ? (
          <Link to={to} className={inPlace ? secondary : primary}>{label}</Link>
        ) : (
          <span className="text-xs">{t('refusal.budget.askAdmin')}</span>
        )}
        {canRetry && (
          <button type="button" className={secondary} onClick={onRetry} title={t('refusal.retryHint')}>
            {t('refusal.retry')}
          </button>
        )}
      </div>
    </div>
  );
}

export default function RefusalCard({ refusal, onRetry }) {
  if (!refusal || !refusal.code) return null;
  const canRetry = typeof onRetry === 'function';
  return (
    <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900" role="alert">
      {refusal.code === 'capability_guard'
        ? <GuardRefusal refusal={refusal} onRetry={onRetry} canRetry={canRetry} />
        : <BudgetRefusal refusal={refusal} onRetry={onRetry} canRetry={canRetry} />}
    </div>
  );
}
