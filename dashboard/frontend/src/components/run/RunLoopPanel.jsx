import React from 'react';
import { Link } from 'react-router-dom';
import {
  History, Bot, Layers, MessageSquareText, Wrench,
  ShieldCheck, ShieldAlert, FileJson, KeySquare, Cpu, ScrollText, FileOutput, Box, Repeat,
} from 'lucide-react';
import { useI18n } from '../../i18n';
import AgentVersion from './AgentVersion';

/**
 * RunLoopPanel: what the agent loop recorded beyond the tool trail
 * (agents/agent_loop.py's LoopState.summary(), stored as `run.loop`), plus
 * the agent version this run built from (`run.agent_version`) with a
 * one-button rollback.
 *
 * The run page shows the version beside the agent's name instead and passes
 * `showVersion={false}`.
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

export default function RunLoopPanel({ run, onChanged, showVersion = true }) {
  const { t } = useI18n();
  const loop = run?.loop && typeof run.loop === 'object' ? run.loop : null;
  const hasVersion = showVersion && run?.agent_version != null;

  // The container's own line of the run's cost (common/pricing.py
  // container_cost_usd): how long it lived and the sandbox size or cpu
  // profile it was priced against. Read off the run record directly, not
  // `run.loop`: it is not a loop policy, it is what the run executed in.
  const containerSize = run?.container_size ? String(run.container_size).toLowerCase() : '';
  const containerCpus = run?.container_cpus;
  const containerHours = (() => {
    if (run?.execution_mode !== 'docker' || !run?.started_at || !run?.finished_at) return null;
    const start = new Date(run.started_at).getTime();
    const end = new Date(run.finished_at).getTime();
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
    return (end - start) / 3600000;
  })();
  const hasContainerProfile = containerHours != null && (containerSize || containerCpus);

  const hasLoop = !!loop && Object.keys(loop).length > 0;
  if (!hasVersion && !hasLoop && !hasContainerProfile) return null;

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-6 space-y-3" data-testid="run-loop-panel">
      {hasVersion && (
        <Section icon={History} title={t('runLoop.agentVersion')}>
          <AgentVersion run={run} onChanged={onChanged} />
        </Section>
      )}

      {hasLoop && loop.answered_by?.length > 0 && (
        <Section icon={Bot} title={t('runLoop.answeredBy')}>
          <ul className="space-y-1">
            {loop.answered_by.map((a, i) => (
              <li key={i} className="text-sm text-gray-700 flex items-center gap-2 flex-wrap">
                <span className="font-mono text-xs">{[a.provider, a.model].filter(Boolean).join('/')}</span>
                {a.switched ? (
                  <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-800">
                    {t('runLoop.switched')}
                  </span>
                ) : a.fallback && (
                  <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-amber-100 text-amber-800">
                    {t('runLoop.fallback')}
                  </span>
                )}
                {a.reason && !a.switched && <span className="text-xs text-gray-400">{a.reason}</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* A person moved the run to another model mid-run (steering mode
          `switch_model`); every model call after the step ran on the new one. */}
      {hasLoop && loop.model_switches?.length > 0 && (
        <Section icon={Repeat} title={t('runLoop.modelSwitches')}>
          <ul className="space-y-1">
            {loop.model_switches.map((sw, i) => (
              <li key={sw.msg_id || i} className="text-sm text-gray-700 flex items-center gap-2 flex-wrap">
                <span className="text-xs text-gray-400">{t('runLoop.afterStep', { step: sw.after_step })}</span>
                <span className="font-mono text-xs">
                  {sw.from ? t('runLoop.switchFromTo', { from: sw.from, to: sw.to }) : sw.to}
                </span>
                {sw.by && <span className="text-xs text-gray-400">{t('runLoop.switchBy', { name: sw.by })}</span>}
                {sw.error && <span className="text-xs text-red-600">{t('runLoop.switchFailed', { error: sw.error })}</span>}
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

      {/* Operator instructions added mid-run (steering mode `system`),
          appended to the system prompt from the step they arrived at. */}
      {hasLoop && loop.system_messages?.length > 0 && (
        <Section icon={ScrollText} title={t('runLoop.systemMessages')}>
          <ul className="space-y-1">
            {loop.system_messages.map((inj, i) => (
              <li key={i} className="text-sm text-gray-700">
                <span className="text-xs text-gray-400 mr-2">{t('runLoop.afterStep', { step: inj.after_step })}</span>
                {inj.text}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* Tool results too long for the context, saved to a workspace file
          (agents/tool_spill.py); the model saw the head and the tail. */}
      {hasLoop && loop.tool_spills?.length > 0 && (
        <Section icon={FileOutput} title={t('runLoop.toolSpills')}>
          <ul className="space-y-1">
            {loop.tool_spills.map((sp, i) => (
              <li key={i} className="text-sm text-gray-700 flex items-center gap-2 flex-wrap">
                <span className="font-mono text-xs">{sp.tool}</span>
                {sp.file_id
                  ? <Link to={`/files?file=${encodeURIComponent(sp.file_id)}`} className="font-mono text-xs text-indigo-600 hover:underline">{sp.path}</Link>
                  : <span className="font-mono text-xs text-gray-500">{sp.path}</span>}
                <span className="text-xs text-gray-400">{t('runLoop.spillChars', { chars: sp.chars ?? 0 })}</span>
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

      {/* Model calls made for the run beside its own loop (the tool policy's
          classifier, a guardrail judge, a schema repair, the outcome grader):
          counted in the run's cost at their own models (common/aux_usage.py). */}
      {hasLoop && loop.aux_calls?.length > 0 && (
        <Section icon={Cpu} title={t('runLoop.auxCalls')}>
          <ul className="space-y-1">
            {loop.aux_calls.map((c, i) => (
              <li key={i} className="text-sm text-gray-700 flex items-center gap-2 flex-wrap">
                <span className="text-xs">{t(`runLoop.auxPurpose.${c.purpose}`, { defaultValue: c.purpose })}</span>
                <span className="font-mono text-xs text-gray-500">{[c.provider, c.model].filter(Boolean).join('/')}</span>
                <span className="text-xs text-gray-400">
                  {t('runLoop.auxTokens', { input: c.input_tokens ?? 0, output: c.output_tokens ?? 0 })}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* The container the run executed in: its own line of the run's cost
          (common/pricing.py container_cost_usd), priced per sandbox size or,
          for a custom profile, per vCPU-hour. */}
      {hasContainerProfile && (
        <Section icon={Box} title={t('runLoop.containerProfile')}>
          <p className="text-sm text-gray-700">
            {containerSize
              ? t('runLoop.containerSummarySized', {
                size: t(`runLoop.containerSize.${containerSize}`, { defaultValue: containerSize }),
                hours: containerHours.toFixed(2),
              })
              : t('runLoop.containerSummaryCustom', { cpus: containerCpus, hours: containerHours.toFixed(2) })}
          </p>
        </Section>
      )}
    </div>
  );
}
