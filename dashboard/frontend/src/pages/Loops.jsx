import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Repeat, Plus, Play, Square, Trash2, Loader, Save, AlertTriangle, X,
  ChevronDown, ChevronRight, Target, Gauge, DollarSign, ExternalLink,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  getLoops, createLoop, getLoop, updateLoop, deleteLoop, estimateLoop,
  startLoop, getLoopRuns, getLoopRun, getLoopIterations, stopLoopRun, resumeLoopRun,
  listFlows, getAgents,
  getLoopChat, clearLoopChat, stopLoopChat, loopChatUrl,
} from '../api';
import EntityChat from '../components/EntityChat';
import InPanelNote from '../components/pageChat/InPanelNote';
import { usePageChat, usePageChatPanel } from '../components/pageChat/pageChat';
import { useWorkspace } from '../components/workspace';
import { useChannel, useLiveRefetch, useStream } from '../components/stream';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
/**
 * Loops — a flow that repeats until an agent says the work is good enough.
 *
 * The page is built around the one thing a flow run cannot show you: the
 * trajectory. Every iteration is its own row with its own score, verdict and
 * feedback, so "is this converging or just spending?" is answerable at a glance
 * rather than by reading four transcripts.
 */

const STATUS_STYLES = {
  running: 'bg-blue-100 text-blue-700',
  stopping: 'bg-amber-100 text-amber-700',
  completed: 'bg-green-100 text-green-700',
  stopped: 'bg-amber-100 text-amber-700',
  failed: 'bg-red-100 text-red-700',
};

// Reason and evaluator wording lives in the i18n namespace; these lists keep
// the order the selects render in.
const STOP_REASONS = [
  'criterion_met', 'target_score', 'max_iterations', 'no_improvement',
  'cost_ceiling', 'wall_clock', 'stopped', 'flow_failed', 'error',
];

const EVALUATOR_KINDS = ['final_agent', 'agent', 'model'];

const stopReasonLabel = (reason, t) =>
  (STOP_REASONS.includes(reason) ? t(`loops.stopReasons.${reason}`) : reason);

const scoreColor = (score) => {
  if (score === null || score === undefined) return 'bg-gray-300';
  if (score >= 80) return 'bg-green-500';
  if (score >= 55) return 'bg-amber-500';
  return 'bg-red-500';
};

const emptyLoop = (workspace) => ({
  name: '', description: '', workspace: workspace || null, flow_id: '',
  exit_criterion: '', max_iterations: 5, min_iterations: 1, target_score: 80,
  patience: 2, cost_ceiling: null, max_wall_seconds: 3600,
  evaluator_mode: 'final_agent', evaluator_agent_id: null,
});

/** The score bar chart. Deliberately the first thing on the run panel. */
function Trajectory({ iterations }) {
  const { t } = useI18n();
  const scored = iterations.filter((i) => i.score !== null && i.score !== undefined);
  if (!scored.length) {
    return (
      <p className="text-xs text-gray-500 italic">
        {t('loops.noScoresYetTheFirst')}
      </p>
    );
  }
  const best = Math.max(...scored.map((i) => i.score));
  return (
    <div className="flex items-end gap-2 h-28">
      {iterations.map((it) => {
        const score = it.score ?? 0;
        return (
          <div key={it.iteration} className="flex-1 flex flex-col items-center justify-end gap-1 min-w-[28px]">
            <span className="text-[11px] font-semibold text-gray-700">
              {it.score === null || it.score === undefined ? '—' : Math.round(it.score)}
            </span>
            <div
              className={`w-full rounded-t ${scoreColor(it.score)} ${it.score === best ? 'ring-2 ring-indigo-400' : ''}`}
              style={{ height: `${Math.max(4, score)}%` }}
              title={it.reason || ''}
            />
            <span className="text-[11px] text-gray-500">{it.iteration}</span>
          </div>
        );
      })}
    </div>
  );
}

function IterationRow({ iteration, flowId }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-gray-50"
      >
        <Chevron className="w-4 h-4 text-gray-400 shrink-0" />
        <span className="text-sm font-semibold text-gray-900 shrink-0">
          Iteration {iteration.iteration}
        </span>
        <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${STATUS_STYLES[iteration.status] || 'bg-gray-100 text-gray-600'}`}>
          {iteration.status}
        </span>
        {iteration.score !== null && iteration.score !== undefined && (
          <span className="inline-flex items-center gap-1 text-xs font-semibold text-gray-700">
            <span className={`w-2 h-2 rounded-full ${scoreColor(iteration.score)}`} />
            {Math.round(iteration.score)}/100
          </span>
        )}
        {iteration.verdict && (
          <span className={`text-xs font-semibold ${iteration.verdict === 'stop' ? 'text-green-700' : 'text-gray-500'}`}>
            {iteration.verdict === 'stop' ? t('loops.accepted') : t('loops.anotherPass')}
          </span>
        )}
        <span className="ml-auto text-xs text-gray-400 truncate max-w-[45%]">
          {iteration.reason}
        </span>
      </button>
      {open && (
        <div className="px-4 py-3 border-t border-gray-100 bg-gray-50 space-y-3">
          {iteration.reason && (
            <div>
              <h4 className="text-xs font-bold uppercase tracking-wide text-gray-500 mb-1">{t('loops.verdict')}</h4>
              <p className="text-sm text-gray-800 whitespace-pre-wrap">{iteration.reason}</p>
            </div>
          )}
          {iteration.feedback && (
            <div>
              <h4 className="text-xs font-bold uppercase tracking-wide text-gray-500 mb-1">
                {t('loops.fedIntoTheNextIteration')}
              </h4>
              <p className="text-sm text-gray-800 whitespace-pre-wrap">{iteration.feedback}</p>
            </div>
          )}
          <div>
            <h4 className="text-xs font-bold uppercase tracking-wide text-gray-500 mb-1">{t('loops.output')}</h4>
            <pre className="text-xs text-gray-700 whitespace-pre-wrap max-h-72 overflow-auto bg-white border border-gray-200 rounded p-2">
              {iteration.output || t('loops.noOutput')}
            </pre>
          </div>
          <div className="flex items-center gap-4 text-xs text-gray-500 flex-wrap">
            {iteration.evaluator_agent && <span>{t('loops.judgedBy', { agent: iteration.evaluator_agent })}</span>}
            <span>{(iteration.duration_ms / 1000).toFixed(1)}s</span>
            <span>${iteration.cost.toFixed(4)}</span>
            {flowId && (
              <Link
                to={`/flows/${encodeURIComponent(flowId)}`}
                className="inline-flex items-center gap-1 text-indigo-600 hover:text-indigo-800"
              >
                <ExternalLink className="w-3 h-3" /> {t('loops.openTheFlowRun')}
              </Link>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function Loops() {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const [loops, setLoops] = useState([]);
  const [flows, setFlows] = useState([]);
  const [agents, setAgents] = useState([]);
  const [selected, setSelected] = useState(null);
  const [draft, setDraft] = useState(null);
  const [mode, setMode] = useState('watch');        // 'setup' | 'watch'
  const [runs, setRuns] = useState([]);
  const [run, setRun] = useState(null);
  const [iterations, setIterations] = useState([]);
  const [goal, setGoal] = useState('');
  const [estimate, setEstimate] = useState(null);
  const [starting, setStarting] = useState(false);
  const [resuming, setResuming] = useState(null);
  const [saving, setSaving] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [message, setMessage] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const [f, a] = await Promise.all([listFlows(selectedWorkspace), getAgents(selectedWorkspace)]);
        setFlows(f.data.flows || f.data || []);
        setAgents(a.data.agents || a.data || []);
      } catch { /* catalogs are optional */ }
    })();
  }, [selectedWorkspace]);

  const loadLoops = useCallback(async () => {
    try {
      const { data } = await getLoops(selectedWorkspace);
      setLoops(data.loops || []);
    } catch {
      setLoops([]);
    }
  }, [selectedWorkspace]);

  useEffect(() => { loadLoops(); }, [loadLoops]);

  const loadRun = async (loopRunId) => {
    try {
      const { data } = await getLoopRun(loopRunId);
      setRun(data);
      setIterations(data.iterations || []);
    } catch {
      setMessage(t('loops.loadRunFailed'));
    }
  };

  // What the form was last synced to, so "has the user edited this?" is a
  // comparison against that version rather than against whatever the chat just
  // wrote.
  const selectedRef = useRef(null);
  useEffect(() => { selectedRef.current = selected; }, [selected]);

  const selectLoop = async (id) => {
    setMessage(''); setEstimate(null); setRun(null); setIterations([]);
    try {
      const [{ data: loop }, { data: hist }] = await Promise.all([getLoop(id), getLoopRuns(id)]);
      setSelected(loop);
      setDraft(loop);
      setGoal(loop.description || '');
      setRuns(hist.runs || []);
      setMode(loop.flow_id ? 'watch' : 'setup');
      if (hist.runs?.length) loadRun(hist.runs[0].loop_run_id);
    } catch {
      setMessage(t('loops.loadLoopFailed'));
    }
  };

  // Iterations arrive on the loop channel; polling is the fallback so a dropped
  // stream degrades to a slower page rather than a frozen one.
  useChannel(run?.loop_run_id ? `loop:${run.loop_run_id}` : null, (ev) => {
    const d = ev?.data;
    if (!d) return;
    if (d.type === 'iteration' || d.type === 'iteration_start') {
      setIterations((prev) => {
        const next = prev.filter((i) => i.iteration !== d.iteration);
        return [...next, d].sort((a, b) => a.iteration - b.iteration);
      });
    } else if (d.type === 'loop_done') {
      setRun((prev) => ({ ...prev, ...d }));
    }
  });

  // Iteration catch-up, replacing what used to be a 4-second poll.
  //
  // Iterations arrive on the `loop:<run_id>` channel subscribed above, and
  // `loop_runs.changed` (loops/store.py `_notify`) fires when a run starts,
  // records an iteration or ends. `fallbackMs` is the floor under a dropped
  // channel, not a poll.
  const catchUpIterations = useCallback(async () => {
    const runId = run?.loop_run_id;
    if (!runId) return;
    try {
      const { data } = await getLoopIterations(runId, 0);
      setIterations(data.iterations || []);
      setRun((prev) => ({ ...prev, ...data }));
    } catch { /* transient */ }
  }, [run?.loop_run_id]);

  useLiveRefetch(catchUpIterations, {
    type: 'loop_runs.changed',
    enabled: Boolean(run?.loop_run_id)
      && (run?.status === 'running' || run?.status === 'stopping'),
    fallbackMs: 30000,
  });

  // A reconnect that could not resume, or events dropped because this tab fell
  // behind, leaves the trajectory holding whatever it had.
  const { onRefetch } = useStream();
  useEffect(() => onRefetch(() => { catchUpIterations(); loadLoops(); }),
    [onRefetch, catchUpIterations, loadLoops]);

  const handleCreate = async (name, flowId) => {
    try {
      const { data } = await createLoop({ ...emptyLoop(selectedWorkspace), name, flow_id: flowId });
      setShowNew(false);
      await loadLoops();
      selectLoop(data.loop_id);
    } catch (e) {
      setMessage(e.response?.data?.detail || t('loops.createFailed'));
    }
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true); setMessage('');
    try {
      const { data } = await updateLoop(draft.loop_id, draft);
      setSelected(data); setDraft(data);
      await loadLoops();
      setMessage('Saved.');
    } catch (e) {
      setMessage(e.response?.data?.detail || t('loops.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const handleStart = async () => {
    if (!selected) return;
    setStarting(true); setMessage('');
    try {
      const { data } = await startLoop(selected.loop_id, { goal, workspace: selectedWorkspace });
      if (data.loop_run_id) {
        setIterations([]);
        setRun(data);
        setMode('watch');
        const { data: hist } = await getLoopRuns(selected.loop_id);
        setRuns(hist.runs || []);
      }
    } catch (e) {
      setMessage(e.response?.data?.detail || t('loops.startFailed'));
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    if (!run?.loop_run_id) return;
    try {
      await stopLoopRun(run.loop_run_id);
      setRun((prev) => ({ ...prev, status: 'stopping' }));
    } catch { /* already finished */ }
  };

  // A loop runs inside the backend process, so a restart ends it mid-run. The
  // stored position is what makes picking it up cheaper than starting over; a
  // run that never finished an iteration has nothing to resume from.
  const isResumable = (r) => r.status === 'failed' && !!(r.position?.iterations_done);

  const handleResume = async (loopRunId) => {
    setResuming(loopRunId);
    setMessage('');
    try {
      const { data } = await resumeLoopRun(loopRunId);
      setRun(data);
      const { data: hist } = await getLoopRuns(selected.loop_id);
      setRuns(hist.runs || []);
    } catch (e) {
      setMessage(e.response?.data?.detail || t('loops.resumeFailed'));
    } finally {
      setResuming(null);
    }
  };

  const handleEstimate = async () => {
    try {
      const { data } = await estimateLoop(selected.loop_id);
      setEstimate(data);
    } catch (e) {
      setMessage(e.response?.data?.detail || t('loops.estimateFailed'));
    }
  };

  const live = run?.status === 'running' || run?.status === 'stopping';
  const set = (patch) => setDraft((d) => ({ ...d, ...patch }));

  // The chat edits the same loop the form does. Take the stored version it
  // reports back — but never over unsaved edits in the form: silently replacing
  // what someone just typed is worse than leaving a stale field they can save.
  const applyLoopFromChat = useCallback((loop) => {
    if (!loop?.loop_id) return;
    setSelected((prev) => (prev?.loop_id === loop.loop_id ? loop : prev));
    setDraft((prev) => {
      if (!prev || prev.loop_id !== loop.loop_id) return prev;
      const hasEdits = JSON.stringify(prev) !== JSON.stringify(selectedRef.current);
      return hasEdits ? prev : loop;
    });
    setLoops((prev) => prev.map((l) => (l.loop_id === loop.loop_id ? { ...l, ...loop } : l)));
  }, []);

  // The selected loop's build chat, drawn either in the Chat tab or in the
  // floating panel — one descriptor, so it is the same conversation either way.
  const loopChat = useLoopChatDescriptor(selected?.loop_id, applyLoopFromChat);
  usePageChat(loopChat);
  const { inlineSuppressed: panelHoldsChat } = usePageChatPanel();

  return (
    <PageContainer>
      <PageHeader
        icon={Repeat}
        title={t('loops.loops')}
        description={t('loops.aFlowRunsItsNodes')}
        actions={
          <button
            onClick={() => setShowNew(true)}
            className="inline-flex items-center px-3 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
          >
            <Plus className="w-4 h-4 mr-1.5" /> {t('loops.newLoop2')}
          </button>
        }
      />

      {message && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {message}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-6">
        {/* Loop list */}
        <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm h-fit">
          <h2 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-3">{t('loops.loops')}</h2>
          {loops.length === 0 ? (
            <p className="text-sm text-gray-500 italic py-3">
              No loops yet. Pick a flow, write the criterion it must meet, and let
              the final agent decide when it is done.
            </p>
          ) : (
            <ul className="space-y-1">
              {loops.map((l) => (
                <li key={l.loop_id}>
                  <div className={`flex items-center justify-between gap-2 px-3 py-2 rounded-lg ${
                    selected?.loop_id === l.loop_id
                      ? 'bg-indigo-50 border border-indigo-200'
                      : 'hover:bg-gray-50 border border-transparent'
                  }`}>
                    <button onClick={() => selectLoop(l.loop_id)} className="min-w-0 flex-1 text-left">
                      <div className="text-sm font-semibold text-gray-900 truncate">{l.name}</div>
                      <div className="text-xs text-gray-500 truncate">
                        {l.flow_name} · up to {l.max_iterations}x
                        {l.target_score !== null && l.target_score !== undefined ? ` · target ${l.target_score}` : ''}
                      </div>
                      {!l.flow_exists && (
                        <div className="text-xs text-red-600">{t('loops.flowIsMissing')}</div>
                      )}
                    </button>
                    <button
                      onClick={async () => {
                        await deleteLoop(l.loop_id);
                        if (selected?.loop_id === l.loop_id) { setSelected(null); setDraft(null); }
                        loadLoops();
                      }}
                      className="p-1 text-gray-400 hover:text-red-600 shrink-0"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="space-y-6">
          {!selected || !draft ? (
            <div className="bg-white rounded-xl border border-gray-200 p-10 text-center text-sm text-gray-500">
              {t('loops.selectALoopToSet')}
            </div>
          ) : (
            <>
              {/* Header + transport */}
              <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
                <div className="flex items-center justify-between gap-4 flex-wrap">
                  <div className="min-w-0">
                    <h2 className="text-lg font-bold text-gray-900 truncate">{selected.name}</h2>
                    <p className="text-xs text-gray-500">
                      {selected.flow_name} ·{' '}
                      {t('loops.judgedBy', { agent: selected.resolved_evaluator?.agent_id || t('loops.aModelCall') })} ·{' '}
                      {t('loops.upToIterations', { count: selected.max_iterations })}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {/* Three ways to work on one loop: fill the form in, talk
                        it into shape, or watch it run. The chat is a peer of
                        the form rather than a panel beside it — a build
                        conversation wants the whole column. */}
                    <div className="inline-flex rounded-lg border border-gray-300 overflow-hidden">
                      {['setup', 'chat', 'watch'].map((m) => (
                        <button
                          key={m} onClick={() => setMode(m)}
                          className={`px-3 py-1.5 text-xs font-semibold ${
                            mode === m ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600'
                          }`}
                        >
                          {t(`loops.mode.${m}`)}
                        </button>
                      ))}
                    </div>
                    <button
                      onClick={handleEstimate}
                      className="inline-flex items-center px-3 py-2 text-xs font-semibold text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-lg hover:bg-indigo-100"
                    >
                      <DollarSign className="w-3.5 h-3.5 mr-1" /> {t('loops.estimate')}
                    </button>
                    {live ? (
                      <button
                        onClick={handleStop}
                        className="inline-flex items-center px-3 py-2 text-xs font-semibold text-white bg-amber-600 rounded-lg hover:bg-amber-700"
                      >
                        <Square className="w-3.5 h-3.5 mr-1" /> {t('loops.stop')}
                      </button>
                    ) : (
                      <button
                        onClick={handleStart}
                        disabled={starting || !selected.flow_exists}
                        className="inline-flex items-center px-3 py-2 text-xs font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                      >
                        {starting ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" />
                                  : <Play className="w-3.5 h-3.5 mr-1" />}
                        Run
                      </button>
                    )}
                  </div>
                </div>

                <div className="mt-3">
                  <label className="block text-xs font-semibold text-gray-600 mb-1">
                    {t('loops.whatThisRunShouldProduce')}
                  </label>
                  <textarea
                    value={goal} onChange={(e) => setGoal(e.target.value)} rows={2}
                    placeholder={t('loops.theRequestTheFlowWorks')}
                    className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                  />
                </div>

                {estimate && (
                  <div className="mt-3 rounded-lg border border-indigo-100 bg-indigo-50 p-3 text-xs text-indigo-800">
                    <span className="font-bold">
                      {t('loops.upToLlmCalls', { count: estimate.llm_calls_upper_bound })}
                    </span>
                    {' — '}{t('loops.estimateBreakdown', {
                      nodes: estimate.agent_nodes,
                      iterations: estimate.max_iterations,
                    })}{' '}
                    <span className="text-indigo-600">
                      {t(`loops.${estimate.note_key}`, { defaultValue: estimate.note })}
                    </span>
                  </div>
                )}
              </div>

              {mode === 'chat' ? (
                <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
                  {/* The tab is where this chat lives; while the floating panel
                      is holding the same conversation, it says so rather than
                      running a second copy of it. */}
                  {panelHoldsChat
                    ? <InPanelNote />
                    : <EntityChat {...loopChat} heightClass="max-h-[32rem] min-h-[18rem]" />}
                </div>
              ) : mode === 'setup' ? (
                <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm space-y-5">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">{t('loops.name')}</label>
                      <input
                        value={draft.name} onChange={(e) => set({ name: e.target.value })}
                        className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">{t('loops.flowToRepeat')}</label>
                      <select
                        value={draft.flow_id} onChange={(e) => set({ flow_id: e.target.value })}
                        className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                      >
                        <option value="">{t('loops.selectAFlow')}</option>
                        {flows.map((f) => (
                          <option key={f.id} value={f.id}>{f.name || f.id}</option>
                        ))}
                      </select>
                    </div>
                  </div>

                  <div>
                    <label className="block text-xs font-semibold text-gray-600 mb-1">{t('loops.description')}</label>
                    <textarea
                      value={draft.description} onChange={(e) => set({ description: e.target.value })}
                      rows={2}
                      className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                    />
                  </div>

                  <div>
                    <label className="block text-xs font-semibold text-gray-600 mb-1">
                      <Target className="w-3.5 h-3.5 inline mr-1 -mt-0.5" />
                      {t('loops.exitCriterion')}
                    </label>
                    <textarea
                      value={draft.exit_criterion} onChange={(e) => set({ exit_criterion: e.target.value })}
                      rows={4}
                      placeholder={t('loops.eGEveryClaimIn')}
                      className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                    />
                    <p className="text-xs text-gray-500 mt-1">
                      Handed to the evaluator verbatim. Written as a standard to meet,
                      not as a topic — a vague criterion produces a loop that never
                      converges or one that stops immediately.
                    </p>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">{t('loops.whoJudges')}</label>
                      <select
                        value={draft.evaluator_mode}
                        onChange={(e) => set({ evaluator_mode: e.target.value })}
                        className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                      >
                        {EVALUATOR_KINDS.map((k) => [k, t(`loops.evaluators.${k}`)]).map(([k, v]) => (
                          <option key={k} value={k}>{v}</option>
                        ))}
                      </select>
                      <p className="text-xs text-gray-500 mt-1">
                        The judge needs no reviewing instructions of its own — the
                        evaluation prompt supplies that role for one call.
                      </p>
                    </div>
                    {draft.evaluator_mode === 'agent' && (
                      <div>
                        <label className="block text-xs font-semibold text-gray-600 mb-1">{t('loops.reviewer')}</label>
                        <select
                          value={draft.evaluator_agent_id || ''}
                          onChange={(e) => set({ evaluator_agent_id: e.target.value || null })}
                          className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                        >
                          <option value="">{t('loops.selectAnAgent')}</option>
                          {agents.map((a) => (
                            <option key={a.id} value={a.id}>{a.name || a.id}</option>
                          ))}
                        </select>
                      </div>
                    )}
                  </div>

                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">{t('loops.maxIterations')}</label>
                      <input
                        type="number" min={1} max={50} value={draft.max_iterations}
                        onChange={(e) => set({ max_iterations: Number(e.target.value) })}
                        className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">{t('loops.minIterations')}</label>
                      <input
                        type="number" min={1} max={50} value={draft.min_iterations}
                        onChange={(e) => set({ min_iterations: Number(e.target.value) })}
                        className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                      />
                      <p className="text-[11px] text-gray-500 mt-1">
                        Overrules an early "stop" — models praise their own first draft.
                      </p>
                    </div>
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">
                        <Gauge className="w-3.5 h-3.5 inline mr-1 -mt-0.5" /> {t('loops.targetScore')}
                      </label>
                      <input
                        type="number" min={0} max={100}
                        value={draft.target_score ?? ''}
                        onChange={(e) => set({ target_score: e.target.value === '' ? null : Number(e.target.value) })}
                        placeholder={t('loops.none')}
                        className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">{t('loops.patience')}</label>
                      <input
                        type="number" min={0} max={20} value={draft.patience}
                        onChange={(e) => set({ patience: Number(e.target.value) })}
                        className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                      />
                      <p className="text-[11px] text-gray-500 mt-1">
                        {t('loops.stopAfterThisManyPasses')}
                      </p>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">{t('loops.costCeiling')}</label>
                      <input
                        type="number" step="0.01" min={0} value={draft.cost_ceiling ?? ''}
                        onChange={(e) => set({ cost_ceiling: e.target.value === '' ? null : Number(e.target.value) })}
                        placeholder={t('loops.none')}
                        className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">{t('loops.wallClockCap')}</label>
                      <input
                        type="number" min={60} value={draft.max_wall_seconds}
                        onChange={(e) => set({ max_wall_seconds: Number(e.target.value) })}
                        className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                      />
                    </div>
                  </div>

                  <div className="flex justify-end">
                    <button
                      onClick={handleSave} disabled={saving}
                      className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                    >
                      {saving ? <Loader className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}
                      Save
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  {/* Run picker */}
                  {runs.length > 0 && (
                    <div className="bg-white rounded-xl border border-gray-200 p-3 shadow-sm flex items-center gap-2 flex-wrap">
                      <span className="text-xs font-bold uppercase tracking-wide text-gray-500 mr-1">{t('loops.runs')}</span>
                      {runs.slice(0, 8).map((r) => (
                        <span key={r.loop_run_id} className="inline-flex items-center gap-1">
                          <button
                            onClick={() => loadRun(r.loop_run_id)}
                            className={`px-2.5 py-1 rounded-lg text-xs font-semibold border ${
                              run?.loop_run_id === r.loop_run_id
                                ? 'bg-indigo-50 border-indigo-300 text-indigo-700'
                                : 'border-gray-200 text-gray-600 hover:bg-gray-50'
                            }`}
                          >
                            {new Date(r.started_at).toLocaleString()} · {r.iterations_done}x
                            {r.final_score !== null && r.final_score !== undefined ? ` · ${Math.round(r.final_score)}` : ''}
                          </button>
                          {isResumable(r) && (
                            <button
                              onClick={() => handleResume(r.loop_run_id)}
                              disabled={resuming === r.loop_run_id}
                              title={t('loops.resumeHint')}
                              className="px-2 py-1 rounded-lg text-xs font-semibold border border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100 disabled:opacity-50"
                            >
                              {resuming === r.loop_run_id ? t('loops.resuming') : t('loops.resume')}
                            </button>
                          )}
                        </span>
                      ))}
                    </div>
                  )}

                  {!run ? (
                    <div className="bg-white rounded-xl border border-gray-200 p-10 text-center text-sm text-gray-500">
                      {t('loops.noRunsYetPressRun')}
                    </div>
                  ) : (
                    <>
                      <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
                        <div className="flex items-center gap-3 mb-4 flex-wrap">
                          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${STATUS_STYLES[run.status] || 'bg-gray-100 text-gray-600'}`}>
                            {run.status}
                          </span>
                          <span className="text-sm text-gray-600">
                            {t('loops.iterationCount', { count: run.iterations_done })}
                          </span>
                          {run.best_score !== null && run.best_score !== undefined && (
                            <span className="text-sm text-gray-600">{t('loops.bestScore', { score: Math.round(run.best_score) })}</span>
                          )}
                          <span className="text-sm text-gray-600">${(run.total_cost || 0).toFixed(4)}</span>
                          {run.stop_reason && (
                            <span className="text-sm text-gray-500 italic">
                              — {stopReasonLabel(run.stop_reason, t)}
                            </span>
                          )}
                          {run.task_id && (
                            <Link to={`/tasks/${run.task_id}`} className="text-xs text-indigo-600 hover:text-indigo-800 inline-flex items-center gap-1">
                              <ExternalLink className="w-3 h-3" /> {t('loops.task')}
                            </Link>
                          )}
                        </div>
                        <Trajectory iterations={iterations} />
                        {run.error && (
                          <p className="mt-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-2">
                            {run.error}
                          </p>
                        )}
                      </div>

                      <div className="space-y-2">
                        {iterations.map((it) => (
                          <IterationRow key={it.iteration} iteration={it} flowId={selected.flow_id} />
                        ))}
                        {live && (
                          <div className="flex items-center gap-2 text-sm text-gray-500 px-3 py-2">
                            <Loader className="w-4 h-4 animate-spin" /> {t('loops.running')}
                          </div>
                        )}
                      </div>

                      {run.result && !live && (
                        <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
                          <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-2">{t('loops.result')}</h3>
                          <pre className="text-sm text-gray-800 whitespace-pre-wrap max-h-96 overflow-auto">
                            {run.result}
                          </pre>
                        </div>
                      )}
                    </>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </div>

      {showNew && (
        <NewLoopModal flows={flows} onClose={() => setShowNew(false)} onCreate={handleCreate} />
      )}
    </PageContainer>
  );
}

/**
 * The loop's build chat.
 *
 * A loop is three decisions — which flow, what "good enough" means, and when to
 * give up — and two of them are prose. That is a conversation, so it gets one,
 * pinned to this loop: the Loop Creator edits it in place and the form picks up
 * the result.
 *
 * The callbacks are memoised on the loop id because EntityChat loads its
 * transcript in an effect keyed on them — fresh closures each render would
 * refetch the conversation continuously.
 */
function useLoopChatDescriptor(loopId, onLoopChanged) {
  const { t } = useI18n();

  const loadChat = useCallback(() => getLoopChat(loopId), [loopId]);
  const clearChat = useCallback(() => clearLoopChat(loopId), [loopId]);
  const stopChat = useCallback(() => stopLoopChat(loopId), [loopId]);

  const onEvent = useCallback((ev) => {
    if (ev.type === 'loop' && ev.loop) onLoopChanged(ev.loop);
  }, [onLoopChanged]);

  return useMemo(() => (loopId ? {
    scope: `loop:${loopId}`,
    path: loopChatUrl(loopId),
    loadChat, clearChat, stopChat, onEvent,
    title: t('loops.buildChat'),
    emptyHint: t('loops.buildChatHint'),
    suggestions: [
      t('loops.chatSuggestCriterion'),
      t('loops.chatSuggestReviewer'),
      t('loops.chatSuggestCeilings'),
      // The chat can start the loop too — behind an approval step.
      t('loops.chatSuggestRun'),
    ],
  } : null), [loopId, loadChat, clearChat, stopChat, onEvent, t]);
}


function NewLoopModal({ flows, onClose, onCreate }) {
  const { t } = useI18n();
  const [name, setName] = useState('');
  const [flowId, setFlowId] = useState('');
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <h3 className="text-base font-bold text-gray-900">{t('loops.newLoop')}</h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">{t('loops.name')}</label>
            <input
              value={name} onChange={(e) => setName(e.target.value)} autoFocus
              className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
              placeholder={t('loops.refineTheLaunchPost')}
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">{t('loops.flowToRepeat')}</label>
            <select
              value={flowId} onChange={(e) => setFlowId(e.target.value)}
              className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
            >
              <option value="">{t('loops.selectAFlow')}</option>
              {flows.map((f) => <option key={f.id} value={f.id}>{f.name || f.id}</option>)}
            </select>
          </div>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-gray-200">
          <button onClick={onClose} className="px-3 py-2 text-sm font-semibold text-gray-600 hover:text-gray-800">
            {t('loops.cancel')}
          </button>
          <button
            onClick={() => onCreate(name.trim(), flowId)}
            disabled={!name.trim() || !flowId}
            className="px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {t('loops.create')}
          </button>
        </div>
      </div>
    </div>
  );
}
