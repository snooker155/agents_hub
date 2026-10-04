/**
 * The Test box: run one guardrail against pasted text without recording an
 * event or an audit row (POST /api/guardrails/{id}/test, a dry run). A
 * sequence guardrail is run over pasted tool calls instead, in order, and
 * says which one it would stop.
 */
import { useState } from 'react';
import { Loader, Play, CheckCircle, XCircle, AlertTriangle } from 'lucide-react';
import { testGuardrail, testGuardrailCalls } from '../../api/guardrails';
import { useI18n } from '../../i18n';

const CALLS_EXAMPLE = '[\n  {"tool": "check_invoice", "input": {"invoice": "42"}},\n  {"tool": "pay_invoice", "input": {"amount": 120}}\n]';

function SequenceTestBox({ guardrail }) {
  const { t } = useI18n();
  const [text, setText] = useState(CALLS_EXAMPLE);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  const run = async () => {
    setError('');
    setResult(null);
    let calls;
    try {
      calls = JSON.parse(text);
      if (!Array.isArray(calls)) throw new Error('not a list');
    } catch {
      setError(t('guardrails.testBox.callsInvalid'));
      return;
    }
    setRunning(true);
    try {
      const { data } = await testGuardrailCalls(guardrail.id, calls);
      setResult(data);
    } catch (err) {
      setError(err?.response?.data?.detail || t('guardrails.errors.testFailed'));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="border border-gray-200 rounded-lg p-3 space-y-2">
      <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">{t('guardrails.testBox.title')}</span>
      <p className="text-[11px] text-gray-400">{t('guardrails.testBox.callsHint')}</p>
      <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)}
        aria-label={t('guardrails.testBox.callsLabel')}
        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500" />
      <button onClick={run} disabled={running || !text.trim()}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
        {running ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
        {t('guardrails.testBox.run')}
      </button>
      {error && <p className="text-xs text-red-600">{error}</p>}
      {result && result.passed && (
        <p className="text-xs text-emerald-600 flex items-center gap-1">
          <CheckCircle className="w-3.5 h-3.5" /> {t('guardrails.testBox.callsPassed')}
        </p>
      )}
      {result && !result.passed && (
        <p className="text-xs text-red-600 flex items-center gap-1">
          <XCircle className="w-3.5 h-3.5" />
          {t(guardrail.action === 'ask' ? 'guardrails.testBox.callAsked' : 'guardrails.testBox.callBlocked', {
            n: (result.index ?? 0) + 1, tool: result.tool || '', reason: result.reason,
          })}
        </p>
      )}
    </div>
  );
}

export default function GuardrailTestBox({ guardrail }) {
  if (guardrail.kind === 'sequence') return <SequenceTestBox guardrail={guardrail} />;
  return <TextTestBox guardrail={guardrail} />;
}

function TextTestBox({ guardrail }) {
  const { t } = useI18n();
  const [text, setText] = useState('');
  const [stage, setStage] = useState(guardrail.stage === 'output' ? 'output' : 'input');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  const stages = guardrail.stage === 'both' ? ['input', 'output'] : [guardrail.stage];

  const run = async () => {
    setRunning(true);
    setError('');
    setResult(null);
    try {
      const { data } = await testGuardrail(guardrail.id, text, stage);
      setResult(data);
    } catch (err) {
      setError(err?.response?.data?.detail || t('guardrails.errors.testFailed'));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="border border-gray-200 rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">{t('guardrails.testBox.title')}</span>
        {stages.length > 1 && (
          <select value={stage} onChange={(e) => setStage(e.target.value)}
            className="border border-gray-200 rounded px-1.5 py-0.5 text-xs">
            {stages.map((s) => <option key={s} value={s}>{t(`guardrails.stage.${s}`)}</option>)}
          </select>
        )}
      </div>
      <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)}
        placeholder={t('guardrails.testBox.textPlaceholder')}
        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500" />
      <button onClick={run} disabled={running || !text.trim()}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
        {running ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
        {t('guardrails.testBox.run')}
      </button>
      {error && <p className="text-xs text-red-600">{error}</p>}
      {result && !result.applies && (
        <p className="text-xs text-gray-500">{t('guardrails.testBox.doesNotApply', { stage })}</p>
      )}
      {result && result.applies && result.error && (
        <p className="text-xs text-amber-600 flex items-center gap-1">
          <AlertTriangle className="w-3.5 h-3.5" /> {t('guardrails.testBox.judgeError', { error: result.error })}
        </p>
      )}
      {result && result.applies && !result.error && result.passed && (
        <p className="text-xs text-emerald-600 flex items-center gap-1">
          <CheckCircle className="w-3.5 h-3.5" /> {t('guardrails.testBox.passed')}
        </p>
      )}
      {result && result.applies && !result.error && !result.passed && (
        <p className="text-xs text-red-600 flex items-center gap-1">
          <XCircle className="w-3.5 h-3.5" />
          {t(guardrail.action === 'warn' ? 'guardrails.testBox.warned' : 'guardrails.testBox.blocked', { reason: result.reason })}
        </p>
      )}
    </div>
  );
}
