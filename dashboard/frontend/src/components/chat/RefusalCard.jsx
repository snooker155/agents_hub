/**
 * The card a refused turn shows instead of a red error string
 * (chat/refusals.py): the capability guard stopped the agent. It says the
 * reason in plain words and carries the action in place: "Allow for this
 * agent" records the agent level capability exception (the same endpoint as
 * the agent editor's switch). After that, "Try again" sends the same message
 * once more.
 */
import { useState } from 'react';
import { ShieldAlert } from 'lucide-react';
import { useI18n } from '../../i18n';
import { updateAgentCapabilityOverride } from '../../api';

const btn = 'inline-flex items-center rounded-lg border px-3 py-1.5 text-xs font-medium disabled:opacity-50';
const primary = `${btn} border-gray-800 bg-gray-800 text-white hover:bg-gray-700`;
const secondary = `${btn} border-gray-300 bg-white text-gray-700 hover:bg-gray-50`;

function joinCaps(t, caps) {
  const names = (caps || []).map((c) => t(`capabilities.labels.${c}`, { defaultValue: c }));
  if (names.length < 2) return names.join('');
  return `${names.slice(0, -1).join(', ')}${t('refusal.guard.capsJoin')}${names[names.length - 1]}`;
}

function GuardRefusal({ refusal, onRetry, canRetry }) {
  const { t } = useI18n();
  const [state, setState] = useState('idle'); // idle | saving | allowed | blocked
  const [error, setError] = useState('');
  const agentId = refusal.agent_id;
  const allowable = refusal.override_allowed !== false && !!agentId;

  const allow = async () => {
    setState('saving');
    setError('');
    try {
      const { data } = await updateAgentCapabilityOverride(agentId, true);
      setState(data?.honoured_at_build === false ? 'blocked' : 'allowed');
    } catch {
      setState('idle');
      setError(t('refusal.guard.failed'));
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
        {allowable && state !== 'allowed' && state !== 'blocked' && (
          <button type="button" className={primary} onClick={allow} disabled={state === 'saving'}>
            {state === 'saving' ? t('refusal.guard.allowing') : t('refusal.guard.allow')}
          </button>
        )}
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

export default function RefusalCard({ refusal, onRetry }) {
  if (!refusal || refusal.code !== 'capability_guard') return null;
  const canRetry = typeof onRetry === 'function';
  return (
    <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900" role="alert">
      <GuardRefusal refusal={refusal} onRetry={onRetry} canRetry={canRetry} />
    </div>
  );
}
