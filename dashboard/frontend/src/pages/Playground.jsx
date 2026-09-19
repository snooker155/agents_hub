import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Gamepad2, Plus, Trash2, Loader, Users, X, Save, AlertTriangle, Radio, History,
  Sparkles, Wrench, CheckCircle2,
} from 'lucide-react';
import {
  getSimEnvironments, getScenarios, createScenario, deleteScenario,
  streamGenerateScenario,
} from '../api';
import { toolInline } from '../components/toolFormatters';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch } from '../components/stream';
import { isLiveRun } from './playground/status';
import PlaygroundSwitch from './playground/nav';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n, statusLabel } from '../i18n';

// The card shows its scenario's last run, because a scenario has no status of
// its own — "is this one running right now" is a fact about that run.
const RUN_STATUS_STYLES = {
  starting: 'bg-blue-100 text-blue-700',
  running: 'bg-blue-100 text-blue-700',
  stopping: 'bg-amber-100 text-amber-700',
  completed: 'bg-green-100 text-green-700',
  stopped: 'bg-amber-100 text-amber-700',
  failed: 'bg-red-100 text-red-700',
};

function RunStatusBadge({ run }) {
  const { t } = useI18n();
  if (!run) {
    return (
      <span className="shrink-0 px-1.5 py-0.5 rounded bg-gray-100 text-[10px] font-semibold text-gray-500">
        {t('playground.neverRun')}
      </span>
    );
  }
  const live = isLiveRun(run);
  return (
    <span
      title={t('playground.lastRunAt', { when: new Date(run.started_at).toLocaleString() })}
      className={`shrink-0 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold ${
        RUN_STATUS_STYLES[run.status] || 'bg-gray-100 text-gray-600'
      }`}
    >
      {live && <Radio className="w-2.5 h-2.5 animate-pulse" />}
      {statusLabel(run.status, t)}
    </span>
  );
}
/**
 * Agent Playground — N agents with personal goals acting in parallel against a
 * shared, deterministic environment.
 *
 * This page is the catalogue only: every scenario is a card that links to its
 * own page, where it is set up and watched. Splitting the two keeps the run
 * surface (world view, roster, event stream, transport) full-width and gives a
 * simulation a URL that can be shared.
 */

export default function Playground() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { selectedWorkspace, liveUpdates } = useWorkspace();
  const [environments, setEnvironments] = useState([]);
  const [scenarios, setScenarios] = useState([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [showNew, setShowNew] = useState(false);
  const [showGenerate, setShowGenerate] = useState(false);

  // Workspace-aware: the catalogue includes the worlds this workspace has
  // built, and a scenario can be cast in one of those exactly as in a shipped
  // environment.
  useEffect(() => {
    (async () => {
      try {
        const { data } = await getSimEnvironments(selectedWorkspace);
        setEnvironments(data.environments || []);
      } catch { /* the catalogue is optional */ }
    })();
  }, [selectedWorkspace]);

  // `quiet` refetches keep the cards on screen: a live update must not blank
  // the list it is updating.
  const loadScenarios = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const { data } = await getScenarios(selectedWorkspace);
      setScenarios(data.scenarios || []);
    } catch {
      if (!quiet) setScenarios([]);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [selectedWorkspace]);

  useEffect(() => { loadScenarios(); }, [loadScenarios]);

  // A run starting, ending or being stopped is what the status badge shows, so
  // the catalogue follows exactly those events — not the tick stream.
  useLiveRefetch(() => loadScenarios(true), { type: 'sim_runs.changed', enabled: liveUpdates });
  useLiveRefetch(() => loadScenarios(true), { type: 'scenarios.changed', enabled: liveUpdates });

  const handleDelete = async (e, scenarioId) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await deleteScenario(scenarioId);
      loadScenarios();
    } catch (err) {
      setMessage(err.response?.data?.detail || t('playground.deleteFailed'));
    }
  };

  const envName = (envId) => environments.find((e) => e.env_id === envId)?.env_name || envId;

  return (
    <PageContainer>
      <PageHeader
        icon={Gamepad2}
        title={t('playground.playground')}
        description={t('playground.nAgentsEachWithIts')}
        badges={<PlaygroundSwitch active="scenarios" />}
        actions={
          <>
            {/* The catalogue answers "what can I run"; the history answers
                "what did we run" — a question about the runs, which no
                scenario card can hold. */}
            <Link
              to="/playground/runs"
              className="inline-flex items-center px-3 py-2 text-sm font-medium text-gray-600 bg-white border border-gray-300 rounded-lg hover:text-indigo-700 hover:border-indigo-300"
            >
              <History className="w-4 h-4 mr-1.5" /> {t('playground.runHistory')}
            </Link>
            {/* Describing a world is a far shorter way into a scenario than
                filling in a cast by hand, so it sits next to the manual path
                rather than behind it. */}
            <button
              onClick={() => setShowGenerate(true)}
              className="inline-flex items-center px-3 py-2 text-sm font-medium text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-lg hover:bg-indigo-100"
            >
              <Sparkles className="w-4 h-4 mr-1.5" /> {t('playground.generateWithAi')}
            </button>
            <button
              onClick={() => setShowNew(true)}
              className="inline-flex items-center px-3 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
            >
              <Plus className="w-4 h-4 mr-1.5" /> {t('playground.newScenario')}
            </button>
          </>
        }
      />

      {message && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {message}
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-gray-500 py-10">
          <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
        </div>
      ) : scenarios.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-10 text-center">
          <Gamepad2 className="w-8 h-8 text-gray-300 mx-auto mb-3" />
          <p className="text-sm text-gray-500 max-w-md mx-auto">
            {t('playground.noScenariosYetCreateOne')}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {scenarios.map((s) => (
            <Link
              key={s.scenario_id}
              to={`/playground/${s.scenario_id}`}
              className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm hover:border-indigo-300 hover:shadow transition-colors flex flex-col gap-2"
            >
              <div className="flex items-start justify-between gap-2">
                <h3 className="text-sm font-bold text-gray-900 truncate">{s.name}</h3>
                <button
                  onClick={(e) => handleDelete(e, s.scenario_id)}
                  title={t('playground.deleteScenario')}
                  className="p-1 text-gray-400 hover:text-red-600 shrink-0"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
              {s.description && (
                <p className="text-xs text-gray-500 line-clamp-2">{s.description}</p>
              )}
              {/* How far the last run got, and why it ended — what you would
                  otherwise open the scenario to see. */}
              {s.last_run && (
                <div className="text-[11px] text-gray-500 flex items-center gap-1.5 flex-wrap">
                  <span>
                    {t('playground.tickProgress', {
                      tick: s.last_run.ticks_done || 0,
                      total: s.max_ticks,
                    })}
                  </span>
                  {s.last_run.stop_reason && (
                    <span className="text-gray-400">
                      · {t(`playground.stopReason.${s.last_run.stop_reason}`,
                           { defaultValue: s.last_run.stop_reason })}
                    </span>
                  )}
                </div>
              )}
              <div className="text-xs text-gray-500 mt-auto pt-1 flex items-center gap-1.5">
                <Users className="w-3.5 h-3.5 text-gray-300 shrink-0" />
                <span className="truncate min-w-0">
                  {t('playground.scenarioListMeta', {
                    env: envName(s.environment),
                    agents: t('playground.agentCount', { count: s.roles.length }),
                    ticks: t('playground.tickCount', { count: s.max_ticks }),
                  })}
                </span>
                {/* Whether agents act on the clock or only when something
                    reaches them changes what a run costs and how it reads, so
                    it sits with the tick count it qualifies. */}
                <span className="shrink-0 px-1.5 py-0.5 rounded bg-gray-100 text-[10px] font-semibold text-gray-600">
                  {t(`playground.activation.${s.activation || 'synchronous'}`)}
                </span>
                {/* The last run's status closes the card: it is the one thing
                    on it that changes on its own while you are looking. */}
                <span className="ml-auto shrink-0">
                  <RunStatusBadge run={s.last_run} />
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}

      {showNew && (
        <NewScenarioModal
          environments={environments}
          workspace={selectedWorkspace}
          onClose={() => setShowNew(false)}
          onCreated={(s) => { setShowNew(false); navigate(`/playground/${s.scenario_id}`); }}
        />
      )}

      {showGenerate && (
        <GenerateScenarioModal
          workspace={selectedWorkspace}
          onClose={() => setShowGenerate(false)}
          onCreated={(scenarioId) => { setShowGenerate(false); navigate(`/playground/${scenarioId}`); }}
        />
      )}
    </PageContainer>
  );
}


/** One tool the Creator called, as the generate modal reports it. */
function GenerateStep({ step }) {
  // The arguments, summarised: "create_scenario_tool · market" is a step you
  // can follow where a bare tool name repeated eight times is not.
  const inline = step.input ? toolInline(step.tool, step.input, 52) : '';
  const failed = step.status === 'error';
  return (
    <div className={`flex items-start gap-1.5 text-[11px] ${failed ? 'text-red-600' : 'text-gray-500'}`}>
      {failed
        ? <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        : step.status === 'done'
          ? <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 text-green-500 shrink-0" />
          : <Wrench className="w-3.5 h-3.5 mt-0.5 text-indigo-400 shrink-0" />}
      <div className="min-w-0">
        <span className="font-mono">{step.tool}</span>
        {inline ? <span className="text-gray-400"> · {inline}</span> : null}
        {step.status === 'running' ? <span className="text-indigo-400 animate-pulse"> …</span> : null}
        {failed && step.error ? <span className="break-words"> — {step.error}</span> : null}
      </div>
    </div>
  );
}


/**
 * A scenario from a sentence.
 *
 * The Scenario Creator does the whole job in one run — picks the environment,
 * casts registered agents into roles, sets the limits — and persists it itself,
 * so all this has to do is take the description and open what came back. When
 * the available environments and agents cannot cover the request the agent says
 * so instead of building something ill-fitting, and that answer is the result
 * worth showing rather than an error.
 */
function GenerateScenarioModal({ workspace, onClose, onCreated }) {
  const { t } = useI18n();
  const [requirement, setRequirement] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [limitations, setLimitations] = useState('');
  // What the Creator is doing, one line per tool. Steps only: the reply it
  // writes and the scenario payload are not narrated here — a waiting room
  // owes you evidence of progress, not a transcript.
  const [steps, setSteps] = useState([]);
  // What came of it: one line, then the scenario's own page. The pause is the
  // point — navigating the instant the last tool returns means the only
  // summary of what was built flashes past unread.
  const [created, setCreated] = useState(null);   // {id, message}
  const abortRef = useRef(null);
  const stepsRef = useRef(null);
  // The list page re-renders under us (it polls its runs), so the hand-off
  // callback must not be what the timer effect depends on — a fresh closure
  // every render would restart the pause and never fire it.
  const onCreatedRef = useRef(onCreated);
  useEffect(() => { onCreatedRef.current = onCreated; }, [onCreated]);

  // Leaving the modal drops our listener; the run is detached server-side and
  // finishes (and persists what it built) regardless.
  useEffect(() => () => { if (abortRef.current) abortRef.current.abort(); }, []);

  useEffect(() => {
    if (!created) return undefined;
    const timer = setTimeout(() => onCreatedRef.current(created.id), 1600);
    return () => clearTimeout(timer);
  }, [created]);

  // Follow the run: the newest step stays in view without the user chasing it.
  useEffect(() => {
    if (stepsRef.current) stepsRef.current.scrollTop = stepsRef.current.scrollHeight;
  }, [steps, busy]);

  const submit = async () => {
    if (!requirement.trim() || busy) return;
    setBusy(true);
    setError('');
    setLimitations('');
    setSteps([]);

    // The tool that is still open is the last one — a turn runs them one at a
    // time — so settling "the running step" needs no correlation id.
    const settle = (patch) => setSteps((list) => {
      const i = list.map((it) => it.status === 'running').lastIndexOf(true);
      if (i === -1) return list;
      const copy = [...list];
      copy[i] = { ...copy[i], ...patch };
      return copy;
    });

    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await streamGenerateScenario({
        signal: ac.signal,
        body: { requirement: requirement.trim(), workspace: workspace || undefined },
        onEvent: (ev) => {
          if (ev.type === 'tool_start') {
            setSteps((list) => [...list, {
              tool: ev.tool || '', input: ev.input, status: 'running',
            }]);
          } else if (ev.type === 'tool_end') {
            settle({ status: 'done' });
          } else if (ev.type === 'tool_error') {
            settle({ status: 'error', error: ev.error || '' });
          } else if (ev.type === 'result') {
            const outcome = ev.outcome || {};
            if (outcome.type === 'scenario' && outcome.scenario_id) {
              setCreated({ id: outcome.scenario_id, message: outcome.message || '' });
            } else if (outcome.type === 'error') {
              setError(outcome.error || t('playground.generateFailed'));
            } else {
              setLimitations(outcome.message || t('playground.generateLimitations'));
            }
          } else if (ev.type === 'error') {
            setError(ev.error || t('playground.generateFailed'));
          }
        },
      });
    } catch (e) {
      if (e.name !== 'AbortError') {
        setError(e.response?.data?.detail || e.message || t('playground.generateFailed'));
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <h3 className="text-sm font-bold text-gray-900 flex items-center gap-1.5">
            <Sparkles className="w-4 h-4 text-indigo-500" /> {t('playground.generateWithAi')}
          </h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-700">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-5 space-y-4">
          {error && <div className="text-xs text-red-600">{error}</div>}
          {limitations && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 whitespace-pre-wrap">
              {limitations}
            </div>
          )}
          <div>
            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">
              {t('playground.generateDescribe')}
            </label>
            <textarea
              rows={5}
              value={requirement}
              onChange={(e) => setRequirement(e.target.value)}
              placeholder={t('playground.generatePlaceholder')}
              disabled={busy}
              className="w-full text-sm border border-gray-300 rounded-md px-3 py-2 resize-y disabled:bg-gray-50"
            />
          </div>

          {/* The working, under the form: designing a scenario is a dozen tool
              calls and a minute of clock, and a bare spinner cannot tell that
              apart from a hang. Steps only — one line each — and then the one
              sentence that says what was built. */}
          {(busy || steps.length > 0 || created) && (
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div className="text-[11px] font-bold text-gray-500 uppercase mb-1.5">
                {t('playground.generateProgress')}
              </div>
              <div ref={stepsRef} className="max-h-40 overflow-y-auto space-y-1">
                {steps.map((step, i) => <GenerateStep key={i} step={step} />)}
                {busy && !created && (
                  <div className="flex items-center gap-1.5 text-[11px] text-indigo-500">
                    <Loader className="w-3.5 h-3.5 shrink-0 animate-spin" />
                    {t('playground.generating')}
                  </div>
                )}
              </div>
              {created && (
                <div className="mt-2 pt-2 border-t border-gray-200 flex items-start gap-1.5 text-xs text-green-700">
                  <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                  <span className="break-words">
                    {created.message}
                    <span className="block text-[11px] text-gray-400 mt-0.5">
                      {t('playground.generateOpening')}
                    </span>
                  </span>
                </div>
              )}
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-1">
            <button onClick={onClose} className="px-3 py-2 text-sm font-semibold text-gray-600 hover:text-gray-900">
              {t('playground.cancel')}
            </button>
            <button
              onClick={submit} disabled={busy || !!created || !requirement.trim()}
              className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {busy
                ? <Loader className="w-4 h-4 mr-1.5 animate-spin" />
                : <Sparkles className="w-4 h-4 mr-1.5" />}
              {busy ? t('playground.generating') : t('playground.generate')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}


function NewScenarioModal({ environments, workspace, onClose, onCreated }) {
  const { t } = useI18n();
  const [name, setName] = useState('');
  const [environment, setEnvironment] = useState(environments[0]?.env_id || 'market');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    if (!name.trim()) { setError(t('playground.nameRequired')); return; }
    setSaving(true);
    try {
      const { data } = await createScenario({
        name, description, environment, workspace, env_params: {}, roles: [],
      });
      onCreated(data);
    } catch (e) {
      setError(e.response?.data?.detail || t('playground.createFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <h3 className="text-sm font-bold text-gray-900">{t('playground.newScenario')}</h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-700">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-5 space-y-4">
          {error && <div className="text-xs text-red-600">{error}</div>}
          <div>
            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">{t('playground.name')}</label>
            <input value={name} onChange={(e) => setName(e.target.value)}
                   className="w-full text-sm border border-gray-300 rounded-md px-3 py-2" />
          </div>
          <div>
            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">{t('playground.description')}</label>
            <input value={description} onChange={(e) => setDescription(e.target.value)}
                   className="w-full text-sm border border-gray-300 rounded-md px-3 py-2" />
          </div>
          <div>
            <label className="block text-xs font-bold text-gray-500 uppercase mb-1">{t('playground.environment')}</label>
            <select value={environment} onChange={(e) => setEnvironment(e.target.value)}
                    className="w-full text-sm border border-gray-300 rounded-md px-3 py-2">
              {environments.map((e) => (
                <option key={e.env_id} value={e.env_id}>
                  {/* Worlds built here are marked, because which ones you can
                      edit is the one thing the two kinds differ in. */}
                  {e.custom ? `${e.env_name} — ${t('worlds.yourWorld')}` : e.env_name}
                </option>
              ))}
            </select>
            <p className="text-xs text-gray-400 mt-1">
              {environments.find((e) => e.env_id === environment)?.description}
            </p>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button onClick={onClose} className="px-3 py-2 text-sm font-semibold text-gray-600 hover:text-gray-900">
              {t('playground.cancel')}
            </button>
            <button onClick={submit} disabled={saving}
                    className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
              {saving ? <Loader className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}
              {t('playground.create')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
