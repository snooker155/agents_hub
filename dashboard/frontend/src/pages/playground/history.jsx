import React, { useState } from 'react';
import {
  Radio, AlertTriangle, Gauge, Clock, Sliders, ChevronDown, ChevronRight, Lock,
} from 'lucide-react';
import { useI18n, statusLabel, useFormatters } from '../../i18n';
import { runConfig, hasSnapshot, scenarioEditedSince } from './config';
import { isLiveRun } from './status';

/**
 * Run history — every simulation a scenario has ever run, as a list you read
 * rather than a dropdown you guess from.
 *
 * A run is only worth reopening for what it did: how far it got, why it
 * stopped, what it scored and what it cost. Those four are on the row, so the
 * choice is made from the list instead of by opening runs one by one until the
 * right one shows up. Timestamps alone — all a <select> can hold — say nothing
 * about which run that was.
 *
 * The same list serves one scenario's history (on the scenario page) and every
 * scenario's (on the history page); the only difference is whether a row has
 * to name its scenario, which is what `showScenario` decides.
 *
 * Every row reads against the settings the run *actually had*, not the ones the
 * scenario has now: a scenario is edited in place, so after a round of tuning
 * the live row describes the next run, never the finished ones. `run.config` is
 * the snapshot taken at launch; see `./config` for how it is read.
 */

const RUN_STATUS_STYLES = {
  starting: 'bg-blue-100 text-blue-700',
  running: 'bg-blue-100 text-blue-700',
  stopping: 'bg-amber-100 text-amber-700',
  completed: 'bg-green-100 text-green-700',
  stopped: 'bg-amber-100 text-amber-700',
  failed: 'bg-red-100 text-red-700',
};

// The stripe down the left edge of a row: the status again, as something the
// eye can pick a run out by while scanning a long history.
const RUN_STATUS_BARS = {
  starting: 'bg-blue-400',
  running: 'bg-blue-400',
  stopping: 'bg-amber-400',
  completed: 'bg-green-400',
  stopped: 'bg-amber-400',
  failed: 'bg-red-400',
};

/** How long the run took, in the coarsest unit that still says something. */
function runDuration(run) {
  if (!run?.started_at) return '';
  const start = new Date(run.started_at).getTime();
  const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (!Number.isFinite(seconds)) return '';
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function RunStatusChip({ run, className = '' }) {
  const { t } = useI18n();
  return (
    <span
      className={`inline-flex items-center gap-1 shrink-0 px-2 py-0.5 rounded text-xs font-semibold ${
        RUN_STATUS_STYLES[run?.status] || 'bg-gray-100 text-gray-600'
      } ${className}`}
    >
      {isLiveRun(run) && <Radio className="w-3.5 h-3.5 animate-pulse" />}
      {statusLabel(run?.status, t)}
    </span>
  );
}

/** The scores dict as one line.
 *
 * What an environment scores is its own business: the market returns one
 * number per objective, the social world a dict of meters per character. Both
 * are flattened to `name value` pairs here rather than rendered per shape —
 * the row shows how the run came out, and the run itself shows the detail. */
function formatScore(value) {
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object') {
    return Object.entries(value)
      .filter(([, v]) => typeof v === 'number' || typeof v === 'string')
      .slice(0, 3)
      .map(([k, v]) => `${k} ${formatScore(v)}`)
      .join('/');
  }
  return '';
}

function scoreLine(scores) {
  return Object.entries(scores || {})
    .slice(0, 4)
    .map(([k, v]) => {
      const formatted = formatScore(v);
      return formatted ? `${k} ${formatted}` : '';
    })
    .filter(Boolean)
    .join(' · ');
}

/** The knobs worth reading off a row, in the order they answer "what was this
    run". Everything else lives in the run's own parameters panel. */
function paramChips(config, t) {
  if (!config || !Object.keys(config).length) return [];
  const chips = [config.environment];
  if (config.activation) {
    chips.push(t(`playground.activation.${config.activation}`,
      { defaultValue: config.activation }));
  }
  chips.push(t('playground.params.characterCount', { count: (config.roles || []).length }));
  chips.push(t('playground.params.tickCap', { count: config.max_ticks }));
  chips.push(t('playground.params.seed', { seed: config.seed }));
  if (config.default_model) chips.push(config.default_model);
  if (config.cost_ceiling) chips.push(`≤ $${Number(config.cost_ceiling).toFixed(2)}`);
  return chips.filter((c) => c !== undefined && c !== null && c !== '');
}

export function RunRow({ run, selected = false, showScenario = false, maxTicks, onSelect }) {
  const { t } = useI18n();
  const { formatDate } = useFormatters();
  const scores = scoreLine(run.scores);
  // The run's own cap first: "18 / 20" has to be the cap this run stopped
  // against, not the one the scenario was edited to afterwards.
  const cap = run.config?.max_ticks || maxTicks;
  const ticks = cap
    ? t('playground.tickProgress', { tick: run.ticks_done || 0, total: cap })
    : t('playground.tickCount', { count: run.ticks_done || 0 });
  const params = paramChips(run.config, t);

  return (
    <button
      type="button"
      onClick={() => onSelect?.(run)}
      className={`w-full text-left flex items-stretch gap-3 rounded-lg border transition-colors ${
        selected
          ? 'border-indigo-400 bg-indigo-50/60 ring-1 ring-indigo-200'
          : 'border-gray-200 bg-white hover:border-indigo-300 hover:bg-gray-50'
      }`}
    >
      <span className={`w-1 shrink-0 rounded-l-lg ${RUN_STATUS_BARS[run.status] || 'bg-gray-300'}`} />
      <span className="flex-1 min-w-0 py-3.5 pr-4 flex flex-col gap-1.5">
        <span className="flex items-center gap-2 flex-wrap">
          {showScenario && (
            <span className="text-base font-bold text-gray-900 truncate max-w-[20rem]">
              {run.scenario_name || t('playground.unnamedScenario')}
            </span>
          )}
          <span className="text-base font-semibold text-gray-700">
            {formatDate(run.started_at)}
          </span>
          <RunStatusChip run={run} />
          {selected && (
            <span className="px-1.5 py-0.5 rounded bg-indigo-100 text-xs font-semibold text-indigo-700">
              {t('playground.showing')}
            </span>
          )}
        </span>
        <span className="flex items-center gap-2.5 flex-wrap text-sm text-gray-500">
          <span>{ticks}</span>
          {run.stop_reason && (
            <span className="text-gray-400">
              · {t(`playground.stopReason.${run.stop_reason}`, { defaultValue: run.stop_reason })}
            </span>
          )}
          <span className="inline-flex items-center gap-1">
            <Clock className="w-4 h-4 text-gray-300" /> {runDuration(run)}
          </span>
          <span className="font-semibold text-gray-600">
            ${(run.total_cost || 0).toFixed(4)}
          </span>
          {scores && (
            <span
              title={scores}
              className="inline-flex items-center gap-1 min-w-0 max-w-full text-gray-500"
            >
              <Gauge className="w-4 h-4 shrink-0 text-gray-300" />
              <span className="truncate">{scores}</span>
            </span>
          )}
        </span>
        {params.length > 0 && (
          <span
            title={t('playground.params.rowTitle')}
            className="flex items-center gap-1.5 min-w-0 text-xs text-gray-400"
          >
            <Sliders className="w-3.5 h-3.5 shrink-0" />
            <span className="truncate">{params.join(' · ')}</span>
          </span>
        )}
        {run.error && (
          <span className="flex items-start gap-1 text-sm text-amber-700">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            <span className="truncate">{run.error}</span>
          </span>
        )}
      </span>
    </button>
  );
}

/** A number of seconds as something short enough for a readout. */
function seconds(value) {
  const n = Number(value || 0);
  if (!n) return '—';
  return n < 120 ? `${n}s` : `${Math.round(n / 60)}m`;
}

/**
 * The settings one run actually ran with, in full.
 *
 * This is the answer to "what did I have set when this came out this way",
 * which is the whole reason to keep a history at all: the scenario itself has
 * moved on by the time the question gets asked. Collapsed by default — it is
 * provenance, consulted when a run surprises you, not a readout you watch.
 *
 * When the live scenario has been edited since, the card says so, because a
 * sidebar full of numbers that silently disagree with the setup tab is worse
 * than no sidebar.
 */
export function RunParams({ run, scenario, defaultOpen = false }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  const config = runConfig(run, scenario);
  const snapshot = hasSnapshot(run);
  const edited = scenarioEditedSince(run, scenario);
  const roles = config.roles || [];

  const limits = [
    [t('playground.meters.activation'),
      t(`playground.activation.${config.activation || 'synchronous'}`)],
    [t('playground.params.maxTicks'), config.max_ticks],
    [t('playground.params.seedLabel'), config.seed],
    [t('playground.params.concurrency'), config.max_concurrent],
    [t('playground.params.stallTimeout'), seconds(config.stall_timeout)],
    [t('playground.params.maxTurn'), seconds(config.max_turn_seconds)],
    // An empty wall clock is not an unknown one: the run had no time limit,
    // which is a setting, and "—" would read as a missing snapshot.
    [t('playground.params.wallClock'),
      Number(config.max_wall_seconds) > 0
        ? seconds(config.max_wall_seconds)
        : t('playground.params.noLimit')],
  ];
  if (config.activation === 'triggered') {
    limits.push([t('playground.params.idleGrace'), seconds(config.idle_grace_seconds)]);
  }
  if (config.cost_ceiling) {
    limits.push([t('playground.params.costCeiling'),
      `$${Number(config.cost_ceiling).toFixed(2)}`]);
  }
  limits.push([t('playground.params.defaultModel'),
    config.default_model || t('playground.params.perAgent')]);

  const envParams = Object.entries(config.env_params || {});

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-1.5 text-sm font-bold text-gray-700 uppercase tracking-wide"
      >
        <Sliders className="w-4 h-4 text-indigo-500" />
        <span className="flex-1 text-left">{t('playground.params.title')}</span>
        {open ? <ChevronDown className="w-4 h-4 text-gray-400" />
          : <ChevronRight className="w-4 h-4 text-gray-400" />}
      </button>

      {!snapshot && (
        <p className="mt-2 text-[11px] leading-snug text-gray-400">
          {t('playground.params.noSnapshot')}
        </p>
      )}
      {edited && (
        <p className="mt-2 flex items-start gap-1 text-[11px] leading-snug text-amber-700">
          <AlertTriangle className="w-3.5 h-3.5 mt-px shrink-0" />
          {t('playground.params.editedSince')}
        </p>
      )}

      {open && (
        <div className="mt-3 flex flex-col gap-4">
          <dl className="space-y-1">
            {limits.map(([label, value]) => (
              <div key={label} className="flex items-baseline justify-between gap-2 text-[11px]">
                <dt className="text-gray-500">{label}</dt>
                <dd className="font-semibold text-gray-800 text-right">
                  {value === undefined || value === null || value === '' ? '—' : value}
                </dd>
              </div>
            ))}
          </dl>

          {envParams.length > 0 && (
            <div>
              <h4 className="text-[11px] font-bold text-gray-500 uppercase tracking-wide mb-1">
                {t('playground.params.environment', { env: config.environment })}
              </h4>
              <dl className="space-y-1">
                {envParams.map(([key, value]) => (
                  <div key={key} className="flex items-baseline justify-between gap-2 text-[11px]">
                    <dt className="text-gray-500">{key}</dt>
                    <dd className="font-semibold text-gray-800 text-right truncate max-w-[9rem]">
                      {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

          <div>
            <h4 className="text-[11px] font-bold text-gray-500 uppercase tracking-wide mb-1">
              {t('playground.params.characterCount', { count: roles.length })}
            </h4>
            <ul className="flex flex-col gap-2">
              {roles.map((r, i) => (
                <li key={`${r.agent_id}-${i}`} className="rounded-md bg-gray-50 px-2 py-1.5">
                  <div className="flex items-baseline gap-1.5">
                    <span className="text-[11px] font-semibold text-gray-800 truncate">
                      {r.display_name || r.name || r.agent_id}
                    </span>
                    {r.role && (
                      <span className="text-[11px] text-gray-500 truncate">{r.role}</span>
                    )}
                  </div>
                  {r.goal && (
                    <p className="mt-0.5 text-[11px] leading-snug text-gray-500">
                      <span className="text-gray-400">{t('playground.characterDialog.goal')}: </span>
                      {r.goal}
                    </p>
                  )}
                  {/* Spelled out, not reduced to a badge: what one agent knew
                      and the others did not is usually the whole explanation
                      for how a run came out, and it is the part of the setup
                      the transcript never shows. */}
                  {r.private_knowledge && (
                    <p className="mt-1 flex items-start gap-1 rounded bg-amber-50 px-1.5 py-1 text-[11px] leading-snug text-amber-900">
                      <Lock className="w-3 h-3 mt-0.5 shrink-0 text-amber-500" />
                      <span>
                        <span className="text-amber-700">
                          {t('playground.characterDialog.privateKnowledge')}:{' '}
                        </span>
                        {r.private_knowledge}
                      </span>
                    </p>
                  )}
                  <p className="mt-0.5 text-[10px] text-gray-400 truncate">
                    {[
                      r.agent_id,
                      r.model || null,
                      t('playground.memoryChip', { n: r.memory_horizon }),
                      r.wake_every ? t('playground.wakeChip', { n: r.wake_every }) : null,
                      r.starts ? t('playground.startsChip') : null,
                      r.npc ? t('playground.npcChip') : null,
                    ].filter(Boolean).join(' · ')}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}


/** `maxTicks` is the scenario's tick cap, so "tick 7 / 20" reads as progress
    rather than as a bare count. Across scenarios there is no single cap, so it
    also takes a function of the run. */
export function RunHistory({
  runs, selectedId, onSelect, showScenario = false, maxTicks, empty,
}) {
  const { t } = useI18n();
  if (!runs.length) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 p-10 text-center text-base text-gray-500">
        {empty || t('playground.noRunsYet')}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {runs.map((r) => (
        <RunRow
          key={r.sim_run_id}
          run={r}
          selected={r.sim_run_id === selectedId}
          showScenario={showScenario}
          maxTicks={typeof maxTicks === 'function' ? maxTicks(r) : maxTicks}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}
