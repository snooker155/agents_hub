import React, { useEffect, useState } from 'react';
import {
  History, RotateCcw, Bot, Layers, MessageSquareText, Wrench,
  ShieldCheck, ShieldAlert, FileJson, KeySquare, Loader,
} from 'lucide-react';
import { getRunAgentVersion, rollbackRunAgent } from '../../api/agentVersions';
import { useI18n } from '../../i18n';

/**
 * RunLoopPanel: what the agent loop recorded beyond the tool trail
 * (agents/agent_loop.py's LoopState.summary(), stored as `run.loop`), plus
 * the agent version this run built from (`run.agent_version`) with a
 * one-button rollback.
 *
 * Every section is independently optional — a plain run predating the loop
 * or the version pin has neither, and the panel renders nothing at all in
 * that case; a run with only one or two sections shows only those.
 */

function Section({ icon: Icon, title, children }) {
  return (
    <div className="pt-3 border-t border-gray-100 first:border-t-0 first:pt-0">
      <div className="flex items-center gap-2 mb-2">
        <Icon className="w-4 h-4 text-gray-400" />
        <h4 className="text-xs font-semibold text-gray-600 uppercase tracking-wide">{title}</h4>
      </div>
      {children}
    </div>
  );
}

export default function RunLoopPanel({ run, onChanged }) {
  const { t } = useI18n();
  const runId = run?.run_id;
  const loop = run?.loop && typeof run.loop === 'object' ? run.loop : null;
  const hasVersion = run?.agent_version != null;
  const [versionInfo, setVersionInfo] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setVersionInfo(null);
    setError('');
    if (!runId || !hasVersion) return;
    let cancelled = false;
    getRunAgentVersion(runId)
      .then(({ data }) => { if (!cancelled) setVersionInfo(data); })
      .catch(() => {});
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, run?.agent_version]);

  const rollback = async () => {
    if (!runId) return;
    if (!window.confirm(t('runLoop.rollbackConfirm', { version: run.agent_version }))) return;
    setBusy(true);
    setError('');
    try {
      await rollbackRunAgent(runId);
      const { data } = await getRunAgentVersion(runId);
      setVersionInfo(data);
      if (onChanged) onChanged();
    } catch (err) {
      setError(err?.response?.data?.detail || t('runLoop.rollbackFailed'));
    } finally {
      setBusy(false);
    }
  };

  const hasLoop = !!loop && Object.keys(loop).length > 0;
  if (!hasVersion && !hasLoop) return null;

  const versionLabel = () => {
    if (versionInfo?.is_current) return t('runLoop.versionLive', { version: run.agent_version });
    if (versionInfo?.pinned) return t('runLoop.versionPinned', { version: run.agent_version });
    return t('runLoop.versionRan', { version: run.agent_version });
  };

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-6 space-y-3" data-testid="run-loop-panel">
      {hasVersion && (
        <Section icon={History} title={t('runLoop.agentVersion')}>
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-sm text-gray-800">{versionLabel()}</span>
            {versionInfo && !versionInfo.is_current && (
              <button
                type="button"
                onClick={rollback}
                disabled={busy}
                className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                {busy ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
                {t('runLoop.rollback')}
              </button>
            )}
          </div>
          {error && <p className="text-sm text-red-600 mt-1">{error}</p>}
        </Section>
      )}

      {hasLoop && loop.answered_by?.length > 0 && (
        <Section icon={Bot} title={t('runLoop.answeredBy')}>
          <ul className="space-y-1">
            {loop.answered_by.map((a, i) => (
              <li key={i} className="text-sm text-gray-700 flex items-center gap-2 flex-wrap">
                <span className="font-mono text-xs">{[a.provider, a.model].filter(Boolean).join('/')}</span>
                {a.fallback && (
                  <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-amber-100 text-amber-800">
                    {t('runLoop.fallback')}
                  </span>
                )}
                {a.reason && <span className="text-xs text-gray-400">{a.reason}</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {hasLoop && loop.compactions?.length > 0 && (
        <Section icon={Layers} title={t('runLoop.compactions')}>
          <ul className="space-y-1">
            {loop.compactions.map((c, i) => (
              <li key={i} className="text-sm text-gray-700">
                {c.kind === 'server'
                  // Cleared by the provider (Anthropic context editing): the
                  // streamed reply does not say how much, only that it is on.
                  ? t('runLoop.compactionServer', { keep: c.keep ?? '' })
                  : t('runLoop.compactionEntry', {
                    kind: c.kind, step: c.at_step, before: c.chars_before, after: c.chars_after,
                  })}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {hasLoop && loop.injections?.length > 0 && (
        <Section icon={MessageSquareText} title={t('runLoop.injections')}>
          <ul className="space-y-1">
            {loop.injections.map((inj, i) => (
              <li key={i} className="text-sm text-gray-700">
                <span className="text-xs text-gray-400 mr-2">{t('runLoop.afterStep', { step: inj.after_step })}</span>
                {inj.text}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {hasLoop && loop.loaded_tools?.length > 0 && (
        <Section icon={Wrench} title={t('runLoop.loadedTools')}>
          <div className="flex flex-wrap gap-1.5">
            {loop.loaded_tools.map((name) => (
              <span key={name} className="text-xs font-mono px-2 py-0.5 rounded bg-gray-100 text-gray-700">
                {name}
              </span>
            ))}
          </div>
        </Section>
      )}

      {hasLoop && loop.guardrails?.length > 0 && (
        <Section icon={ShieldCheck} title={t('runLoop.guardrails')}>
          <ul className="space-y-1">
            {loop.guardrails.map((g, i) => (
              <li key={i} className="text-sm text-gray-700 flex items-center gap-2 flex-wrap">
                {g.passed
                  ? <ShieldCheck className="w-3.5 h-3.5 text-green-600 shrink-0" />
                  : <ShieldAlert className="w-3.5 h-3.5 text-red-600 shrink-0" />}
                <span className="font-medium">{g.name || g.guardrail_id}</span>
                {g.stage && <span className="text-xs text-gray-400">{g.stage}</span>}
                {g.reason && <span className="text-xs text-gray-500">{g.reason}</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {hasLoop && loop.structured && (
        <Section icon={FileJson} title={t('runLoop.structured')}>
          <p className="text-sm text-gray-700">
            {t('runLoop.structuredSummary', {
              // A list of attempts (agents/loop_ext/structured.py records one
              // entry per check); a count from an older record still reads.
              attempts: Array.isArray(loop.structured.attempts)
                ? loop.structured.attempts.length
                : (loop.structured.attempts ?? 0),
              valid: loop.structured.valid ? t('runLoop.yes') : t('runLoop.no'),
            })}
          </p>
          {(loop.structured.errors || []).length > 0 && (
            <ul className="mt-1 space-y-0.5">
              {loop.structured.errors.map((e, i) => (
                <li key={i} className="text-xs text-red-600">{e}</li>
              ))}
            </ul>
          )}
        </Section>
      )}

      {hasLoop && loop.tool_decisions?.length > 0 && (
        <Section icon={KeySquare} title={t('runLoop.toolDecisions')}>
          <ul className="space-y-1">
            {loop.tool_decisions.map((d, i) => (
              <li key={i} className="text-sm text-gray-700 flex items-center gap-2 flex-wrap">
                <span className="font-mono text-xs">{d.tool}</span>
                <span className="text-xs text-gray-500">{d.mode} → {d.decision}</span>
                {d.reason && <span className="text-xs text-gray-400">{d.reason}</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}
