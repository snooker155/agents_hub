/**
 * The card a refused turn shows instead of a red error string
 * (chat/refusals.py): the capability guard stopped the agent. It says the
 * reason in plain words and carries the action in place: "Allow for this
 * agent" records the agent level capability exception (the same endpoint as
 * the agent editor's switch). After that, "Try again" sends the same message
 * once more.
 *
 * A guardrail that stopped the turn (code ``guardrail``) gets a card of its
 * own: which rule, on the message or on the answer, and what it found, said
 * from the refusal's data in the person's language rather than the English
 * reason the backend logs.
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
      // The retry is a chat turn, built on the hub and never in a container.
      setState(data?.honoured_in_chat === false ? 'blocked' : 'allowed');
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

// What the guardrail found, in a sentence. Never the matched text: the
// backend does not send it (guardrails/checks.py rule_detail).
function guardrailFinding(t, refusal) {
  const detail = refusal.detail || {};
  switch (refusal.kind) {
    case 'pii': {
      const found = (detail.detectors || []).map((d) => t(`refusal.guardrail.pii.${d}`, { defaultValue: d }));
      return found.length ? t('refusal.guardrail.found.pii', { what: found.join(', ') }) : '';
    }
    case 'keywords':
      return detail.keyword ? t('refusal.guardrail.found.keywords', { keyword: detail.keyword }) : '';
    case 'max_chars':
      return t('refusal.guardrail.found.max_chars', { length: detail.length, limit: detail.limit });
    case 'regex':
      return t('refusal.guardrail.found.regex');
    case 'judge':
      if (detail.unreachable) return t('refusal.guardrail.found.judgeUnreachable');
      // The judge model's own words: there is nothing to translate them from.
      return refusal.reason ? t('refusal.guardrail.found.judge', { reason: refusal.reason }) : '';
    default:
      return '';
  }
}

function GuardrailRefusal({ refusal }) {
  const { t } = useI18n();
  const output = refusal.stage === 'output';
  const finding = guardrailFinding(t, refusal);
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 font-semibold text-amber-900">
        <ShieldAlert className="h-4 w-4" />
        {t(output ? 'refusal.guardrail.titleOutput' : 'refusal.guardrail.titleInput', { name: refusal.name })}
      </div>
      {finding && <p>{finding}</p>}
      <p className="text-xs">{t(output ? 'refusal.guardrail.hintOutput' : 'refusal.guardrail.hintInput')}</p>
    </div>
  );
}

/** Under a reply: the warn guardrails that found something in the message or
 * the answer. The turn went on; this only says so. */
export function GuardrailWarnings({ warnings }) {
  const { t } = useI18n();
  if (!warnings?.length) return null;
  return (
    <div className="mt-2 space-y-1.5" data-testid="guardrail-warnings">
      {warnings.map((w, i) => {
        const finding = guardrailFinding(t, w);
        return (
          <div key={`${w.guardrail_id || w.name}-${w.stage}-${i}`} role="status"
            className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-900">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
            <span>
              <span className="font-semibold">
                {t(w.stage === 'output' ? 'refusal.guardrail.warnOutput' : 'refusal.guardrail.warnInput', { name: w.name })}
              </span>
              {finding ? ` ${finding}` : ''}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export default function RefusalCard({ refusal, onRetry }) {
  if (!refusal || !['capability_guard', 'guardrail'].includes(refusal.code)) return null;
  const canRetry = typeof onRetry === 'function';
  return (
    <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900" role="alert">
      {refusal.code === 'guardrail'
        ? <GuardrailRefusal refusal={refusal} />
        : <GuardRefusal refusal={refusal} onRetry={onRetry} canRetry={canRetry} />}
    </div>
  );
}
