import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  FileText, FlaskConical, PanelRightClose, PanelRightOpen, Play, Repeat, Settings2, Square, Trash2, Workflow,
} from 'lucide-react';
import {
  getLoop, updateLoop, deleteLoop, estimateLoop, startLoop, getLoopRuns, getLoopRun,
  getLoopIterations, getLoopRunLog, stopLoopRun, resumeLoopRun, deleteLoopRun, getFlow, listFlows, getAgents,
} from '../api';
import { getLoopChat, clearLoopChat, stopLoopChat, loopChatUrl } from '../api';
import SaveAsEvalCaseDialog from '../components/evals/SaveAsEvalCaseDialog';
import InPanelNote from '../components/pageChat/InPanelNote';
import { usePageChat, usePageChatPanel } from '../components/pageChat/pageChat';
import { useWorkspace } from '../components/workspace';
import { useChannel, useLiveRefetch, useStream } from '../components/stream';
import { errorDetail, useToast } from '../components/toast';
import { AppBar } from '../components/PageLayout';
import { PaneTab } from '../components/PageTabs';
import PageLoader from '../components/PageLoader';
import FlowCanvas from '../components/flow/FlowCanvas';
import { normalizeNode } from '../components/flowEditor/graphHelpers';
import { toCanvasEdges } from '../components/flowEditor/useFlowDocument';
import { nodeStatusesFor } from '../components/flowEditor/nodeStatus';
import FlowArtifacts from '../components/flow/FlowArtifacts';
import { conversationArtifacts } from '../components/chat/artifactOps';
import { conversationViews } from '../components/chat/turnViews';
import { useMessagesWithTrails } from '../components/chat/runTrail';
import { attachmentsPayload, referencesPayload } from '../components/chat/composerPayload';
import LoopPassLogs from '../components/loops/LoopPassLogs';
import LoopRunChat from '../components/loops/LoopRunChat';
import LoopCreatorChat from '../components/loops/LoopCreatorChat';
import LoopRunHistory from '../components/loops/LoopRunHistory';
import LoopSetupForm from '../components/loops/LoopSetupForm';
import { isLive } from '../components/loops/loopFormat';
import { passReplies } from '../components/loops/passLog';
import { liveReplies, useLiveTurns, withLiveTurns } from '../components/runs/liveTurns';
import { useI18n } from '../i18n';

/**
 * One loop (/loops/:loopId, one of its runs at /loops/:loopId/runs/:runId).
 *
 * The flow it repeats is drawn at the top (it can be folded away), with the
 * node that is working lit. Under it the run on screen as a chat, pass by
 * pass with what the judge made of each (the score, the output and the
 * comments that went into the next pass), the scores across them pinned
 * above. The runs of this loop are listed on the left, what the flow did on
 * each pass (its log) on the right, and the Loop Creator opens beside them,
 * as the Planner does on the Tasks page; on a narrower screen it opens over
 * the page instead. The Artifacts tab holds the files and views the passes
 * produced, as on the flow page.
 */
const NO_EVENTS = [];
const GRAPH_KEY = 'loops.graphOpen';

function readGraphOpen() {
  try { return window.localStorage.getItem(GRAPH_KEY) !== '0'; } catch { return true; }
}

export default function LoopDetail() {
  const { t } = useI18n();
  const toast = useToast();
  const navigate = useNavigate();
  const { loopId, runId: routeRunId } = useParams();
  const { selectedWorkspace } = useWorkspace();

  const [loop, setLoop] = useState(null);
  const [draft, setDraft] = useState(null);
  const [flows, setFlows] = useState([]);
  const [agents, setAgents] = useState([]);
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [runs, setRuns] = useState([]);
  const [run, setRun] = useState(null);
  const [iterations, setIterations] = useState([]);
  // iteration number -> that pass's flow-log lines
  const [logs, setLogs] = useState({});
  const [goal, setGoal] = useState('');
  const [estimate, setEstimate] = useState(null);
  // "To eval case": a finished run seeds an eval dataset (components/evals/).
  const [caseDialogOpen, setCaseDialogOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [resuming, setResuming] = useState(null);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState('run'); // run | setup | artifacts
  const [chatOpen, setChatOpen] = useState(false);
  // New run pressed: no run on screen, an empty chat waiting for a request.
  const [fresh, setFresh] = useState(false);
  const [graphOpen, setGraphOpen] = useState(readGraphOpen);
  const [artifactPick, setArtifactPick] = useState(null);
  const toggleGraph = () => setGraphOpen((open) => {
    try { window.localStorage.setItem(GRAPH_KEY, open ? '0' : '1'); } catch { /* a remembered fold only */ }
    return !open;
  });

  // The run on screen: the one the address names, else the latest, unless
  // a new run is being asked for.
  const shownRunId = routeRunId || (fresh ? null : runs[0]?.loop_run_id) || null;
  const live = isLive(run);

  const loadRuns = useCallback(() => getLoopRuns(loopId)
    .then(({ data }) => setRuns(data.runs || []))
    .catch(() => {}), [loopId]);

  // The loop and its history. Promise chains, not async functions: the React
  // Compiler lint treats an async function called from an effect as a
  // synchronous setState.
  useEffect(() => {
    let alive = true;
    Promise.all([getLoop(loopId), getLoopRuns(loopId)])
      .then(([{ data: l }, { data: hist }]) => {
        if (!alive) return;
        setLoop(l);
        setDraft(l);
        setGoal(l.description || '');
        setRuns(hist.runs || []);
        if (!l.flow_id) setTab('setup');
      })
      .catch(() => {
        if (!alive) return;
        toast.error(t('loops.loadLoopFailed'));
        navigate('/loops', { replace: true });
      });
    return () => { alive = false; };
  }, [loopId, navigate, toast, t]);

  useEffect(() => {
    Promise.all([listFlows(selectedWorkspace), getAgents(selectedWorkspace)])
      .then(([f, a]) => {
        setFlows(f.data.flows || f.data || []);
        setAgents(a.data.agents || a.data || []);
      })
      .catch(() => { /* catalogs only fill the setup form's selects */ });
  }, [selectedWorkspace]);

  // The graph of the flow the loop repeats.
  const flowId = loop?.flow_id || '';
  useEffect(() => {
    if (!flowId) return undefined;
    let alive = true;
    getFlow(flowId)
      .then(({ data }) => {
        if (!alive) return;
        setNodes((data.nodes || []).map((n) => normalizeNode(n)));
        setEdges(toCanvasEdges(data.edges));
      })
      .catch(() => {
        if (!alive) return;
        setNodes([]);
        setEdges([]);
      });
    return () => { alive = false; };
  }, [flowId]);

  const loadLog = useCallback((id, iteration) => getLoopRunLog(id, iteration)
    .then(({ data }) => {
      const byIteration = {};
      for (const p of data.iterations || []) byIteration[p.iteration] = p.events || [];
      setLogs((prev) => (iteration ? { ...prev, ...byIteration } : byIteration));
    })
    .catch(() => {}), []);

  // The run on screen, its iterations and their logs.
  useEffect(() => {
    if (!shownRunId) return undefined;
    let alive = true;
    getLoopRun(shownRunId)
      .then(({ data }) => {
        if (!alive) return;
        setRun(data);
        setIterations(data.iterations || []);
        setLogs({});
        loadLog(shownRunId);
      })
      .catch(() => { if (alive) toast.error(t('loops.loadRunFailed')); });
    return () => { alive = false; };
  }, [shownRunId, loadLog, toast, t]);

  const lastIteration = iterations.length ? iterations[iterations.length - 1].iteration : null;

  // Iterations and node progress arrive on the loop channel; the log of the
  // pass in progress is read again whenever a node starts or ends.
  useChannel(run?.loop_run_id ? `loop:${run.loop_run_id}` : null, (ev) => {
    // Channel events arrive flat (common/session_broker.py); `data` is the
    // older wrapped shape.
    const d = ev?.type ? ev : ev?.data;
    if (!d) return;
    if (d.type === 'iteration' || d.type === 'iteration_start') {
      setIterations((prev) => [...prev.filter((i) => i.iteration !== d.iteration), d]
        .sort((a, b) => a.iteration - b.iteration));
      loadLog(run.loop_run_id, d.iteration);
      if (d.type === 'iteration') loadRuns();
    } else if (d.type === 'node_start' || d.type === 'node_done') {
      loadLog(run.loop_run_id, d.iteration);
    } else if (d.type === 'loop_start' || d.type === 'loop_resume' || d.type === 'loop_done') {
      setRun((prev) => ({ ...prev, ...d }));
      loadRuns();
    }
  });

  // Catch-up on `loop_runs.changed` (loops/store.py `_notify`); `fallbackMs`
  // is the floor under a dropped channel, not a poll.
  const catchUp = useCallback(async () => {
    const id = run?.loop_run_id;
    if (!id) return;
    try {
      const { data } = await getLoopIterations(id, 0);
      setIterations(data.iterations || []);
      setRun((prev) => ({ ...prev, ...data, iterations: undefined }));
      const last = (data.iterations || []).at(-1);
      if (last) loadLog(id, last.iteration);
      loadRuns();
    } catch { /* transient */ }
  }, [run?.loop_run_id, loadLog, loadRuns]);

  useLiveRefetch(catchUp, {
    type: 'loop_runs.changed',
    enabled: Boolean(run?.loop_run_id) && live,
    fallbackMs: 30000,
  });

  // A pass is a whole flow and its nodes can work for minutes: while one is
  // running its log is read every few seconds, so a long node is not a frozen
  // page.
  useEffect(() => {
    if (!live || !run?.loop_run_id || lastIteration === null) return undefined;
    const id = run.loop_run_id;
    const timer = setInterval(() => loadLog(id, lastIteration), 4000);
    return () => clearInterval(timer);
  }, [live, run?.loop_run_id, lastIteration, loadLog]);

  // The history lists each run with its status: when the run on screen
  // changes status (it ends, above all), the list is read again. The run's
  // process publishes on its own channel, which may not reach this page, so
  // the status seen here is the one to follow.
  const runStatus = run?.status;
  useEffect(() => {
    if (runStatus) loadRuns();
  }, [runStatus, loadRuns]);

  const { onRefetch } = useStream();
  useEffect(() => onRefetch(() => { catchUp(); loadRuns(); }), [onRefetch, catchUp, loadRuns]);

  // The node working right now, lit on the graph.
  const activeNodeId = useMemo(() => {
    if (!live || lastIteration === null) return null;
    const statuses = nodeStatusesFor(nodes, logs[lastIteration] || NO_EVENTS, true);
    return statuses.find((s) => s.status === 'running')?.id || null;
  }, [live, lastIteration, nodes, logs]);

  const nodeLabels = useMemo(
    () => Object.fromEntries(nodes.map((n) => [n.id, n.data?.label || n.id])),
    [nodes],
  );

  // The Artifacts tab: the files and views the passes' node runs produced,
  // read off their trails as the flow page reads a task run's.
  const replies = useMemo(() => (run ? passReplies(iterations, logs) : []), [run, iterations, logs]);

  // The pass under way streams on its flow run's channel (common/run_stream.py):
  // its nodes at work are laid over the pass's log and drawn in the chat as
  // replies being written.
  const runningPass = live ? iterations.find((it) => it.status === 'running') : null;
  const passTurns = useLiveTurns(runningPass?.flow_run_id ? [`run:${runningPass.flow_run_id}`] : [], 'flow');
  const shownLogs = useMemo(() => {
    if (!runningPass || !logs[runningPass.iteration]) return logs;
    return { ...logs, [runningPass.iteration]: withLiveTurns(logs[runningPass.iteration], passTurns) };
  }, [logs, runningPass, passTurns]);
  // The pass keeps each node's reply, finished ones too, until the pass ends
  // and its evaluation takes over.
  const liveMessages = useMemo(() => liveReplies(passTurns), [passTurns]);
  const repliesWithTrails = useMessagesWithTrails(replies, true);
  const shownArtifacts = useMemo(() => ({
    artifacts: conversationArtifacts(repliesWithTrails),
    views: conversationViews(repliesWithTrails).filter((v) => v.view_kind !== 'code'),
  }), [repliesWithTrails]);
  const artifactCount = Object.keys(shownArtifacts.artifacts).length + shownArtifacts.views.length;

  const openRun = (id) => {
    setFresh(false);
    navigate(`/loops/${encodeURIComponent(loopId)}/runs/${encodeURIComponent(id)}`);
  };
  const startNewRun = () => {
    setFresh(true);
    setRun(null);
    setIterations([]);
    setLogs({});
    setTab('run');
    navigate(`/loops/${encodeURIComponent(loopId)}`);
  };

  // A run is only a run: it makes no task. What the box carried (files, hub
  // records) goes with it; an attached task is the one the loop works on.
  // Says whether the run started, so the box can let go of its attachments.
  const handleStart = async ({ attachments = [], references = [] } = {}) => {
    if (!loop) return false;
    setStarting(true);
    try {
      const { data } = await startLoop(loop.loop_id, {
        goal,
        workspace: selectedWorkspace,
        attachments: attachmentsPayload(attachments, selectedWorkspace),
        references: referencesPayload(references),
      });
      if (!data.loop_run_id) return false;
      setTab('run');
      await loadRuns();
      openRun(data.loop_run_id);
      return true;
    } catch (e) {
      toast.error(t('loops.startFailed'), errorDetail(e));
      return false;
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

  const handleResume = async (id) => {
    setResuming(id);
    try {
      const { data } = await resumeLoopRun(id);
      await loadRuns();
      if (id === shownRunId) setRun((prev) => ({ ...prev, ...data }));
      else openRun(id);
    } catch (e) {
      toast.error(t('loops.resumeFailed'), errorDetail(e));
    } finally {
      setResuming(null);
    }
  };

  // Deleting a History entry: the run with its passes' record. The run on
  // screen, when it is that one, gives way to a new empty run.
  const handleDeleteRun = async (r) => {
    if (!window.confirm(t('loops.confirmDeleteRun'))) return;
    try {
      await deleteLoopRun(r.loop_run_id);
    } catch (e) {
      toast.error(t('loops.deleteRunFailed'), errorDetail(e));
      return;
    }
    setRuns((prev) => prev.filter((x) => x.loop_run_id !== r.loop_run_id));
    if (r.loop_run_id === shownRunId) startNewRun();
  };

  const handleEstimate = async () => {
    try {
      const { data } = await estimateLoop(loop.loop_id);
      setEstimate(data);
    } catch (e) {
      toast.error(t('loops.estimateFailed'), errorDetail(e));
    }
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const { data } = await updateLoop(draft.loop_id, draft);
      setLoop(data);
      setDraft(data);
      toast.success(t('common.saved'));
    } catch (e) {
      toast.error(t('loops.saveFailed'), errorDetail(e));
    } finally {
      setSaving(false);
    }
  };

  // Deleting a loop takes its runs with it, so it asks first.
  const handleDelete = async () => {
    if (!window.confirm(t('loops.confirmDelete', { name: loop.name }))) return;
    try {
      await deleteLoop(loop.loop_id);
      navigate('/loops');
    } catch (e) {
      toast.error(t('loops.deleteFailed'), errorDetail(e));
    }
  };

  // The chat edits the same loop the form does. Take the stored version it
  // reports back, but never over unsaved edits in the form: silently replacing
  // what someone just typed is worse than leaving a stale field they can save.
  const loopRef = useRef(null);
  useEffect(() => { loopRef.current = loop; }, [loop]);
  const applyLoopFromChat = useCallback((next) => {
    if (!next?.loop_id) return;
    setDraft((prev) => {
      if (!prev || prev.loop_id !== next.loop_id) return prev;
      const hasEdits = JSON.stringify(prev) !== JSON.stringify(loopRef.current);
      return hasEdits ? prev : next;
    });
    setLoop((prev) => (prev?.loop_id === next.loop_id ? next : prev));
  }, []);

  // The Loop Creator's conversation, drawn either in the column here or in the
  // floating page-chat panel: one descriptor, so it is the same conversation
  // either way, and the column says so while the panel holds it.
  const loopChat = useLoopChatDescriptor(loop?.loop_id, applyLoopFromChat);
  usePageChat(loopChat);
  const { inlineSuppressed: panelHoldsChat } = usePageChatPanel();

  if (!loop || !draft) {
    return <div className="h-full bg-white"><PageLoader /></div>;
  }

  const judge = loop.resolved_evaluator?.mode === 'rubric'
    ? t('outcomes.loopJudgedByRubric')
    : (loop.resolved_evaluator?.agent_id || t('loops.aModelCall'));

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <AppBar
        icon={Repeat}
        title={loop.name}
        // The whole name, on two lines when it needs them.
        titleWrap
        subtitle={[
          loop.flow_name,
          t('loops.judgedBy', { agent: judge }),
          t('loops.upToIterations', { count: loop.max_iterations }),
        ].filter(Boolean).join(' · ')}
        backTo="/loops"
        backLabel={t('loops.loops')}
        // The flow the loop repeats, right after the loop's name and its info.
        badges={loop.flow_exists ? (
          <Link
            to={`/flows/${encodeURIComponent(loop.flow_id)}`}
            title={t('loops.openFlow')}
            className="inline-flex min-w-0 max-w-[14rem] shrink items-center gap-1 rounded-md border border-slate-200 px-2 py-0.5 text-xs font-medium text-indigo-600 hover:text-indigo-800"
            data-testid="loop-flow-link"
          >
            <Workflow className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{loop.flow_name}</span>
          </Link>
        ) : (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-slate-200 px-2 py-0.5 text-xs text-red-600">
            <Workflow className="h-3.5 w-3.5 shrink-0" />
            {loop.flow_name} · {t('loops.flowIsMissing')}
          </span>
        )}
        center={(
          <div className="inline-flex items-center gap-1 rounded-lg bg-gray-100 p-0.5">
            <PaneTab active={tab === 'run'} onClick={() => setTab('run')} icon={Play} label={t('loops.tabs.run')} />
            <PaneTab active={tab === 'setup'} onClick={() => setTab('setup')} icon={Settings2} label={t('loops.tabs.setup')} />
            <PaneTab
              active={tab === 'artifacts'} onClick={() => setTab('artifacts')}
              icon={FileText} label={t('chat.artifacts')} badge={artifactCount}
            />
          </div>
        )}
        actions={<>
          {/* Run is in the run chat's box; Stop stays up here too, as on the
              flow page, so a run can be stopped from Setup. */}
          {run?.loop_run_id && !live && (
            <button
              onClick={() => setCaseDialogOpen(true)}
              title={t('messageDetails.toEvalCaseHint')}
              className="flex items-center rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-sm font-semibold text-indigo-700 shadow-sm transition hover:bg-indigo-100"
              data-testid="loop-to-eval-case"
            >
              <FlaskConical className="mr-1.5 h-4 w-4" /> {t('messageDetails.toEvalCase')}
            </button>
          )}
          {live && (
            <button
              onClick={handleStop}
              disabled={run?.status === 'stopping'}
              className="flex items-center rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-sm font-semibold text-rose-600 shadow-sm transition hover:bg-rose-100 disabled:opacity-50"
            >
              <Square className="mr-1.5 h-4 w-4" /> {t('loops.stop')}
            </button>
          )}
          <button
            onClick={toggleGraph} aria-pressed={graphOpen}
            title={graphOpen ? t('loops.hideGraph') : t('loops.showGraph')}
            aria-label={graphOpen ? t('loops.hideGraph') : t('loops.showGraph')}
            className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-semibold shadow-sm transition ${
              graphOpen ? 'border-indigo-200 bg-indigo-50 text-indigo-700' : 'border-gray-200 bg-gray-50 text-gray-600 hover:bg-gray-100'}`}
            data-testid="loop-graph-toggle"
          >
            <Workflow className="h-4 w-4" />
            <span className="hidden sm:inline">{t('loops.graph')}</span>
          </button>
          <button
            onClick={() => setChatOpen((v) => !v)} aria-pressed={chatOpen}
            title={chatOpen ? t('loops.hideLoopCreator') : t('loops.showLoopCreator')}
            className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-semibold shadow-sm transition ${
              chatOpen ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-gray-200 bg-gray-50 text-emerald-600 hover:bg-emerald-50'}`}
            data-testid="loop-creator-toggle"
          >
            {chatOpen ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
            <span className="hidden sm:inline">{t('loops.loopCreator')}</span>
          </button>
          <button
            onClick={handleDelete}
            title={t('loops.deleteLoop')} aria-label={t('loops.deleteLoop')}
            className="rounded-lg border border-gray-200 bg-gray-50 p-1.5 text-gray-400 shadow-sm transition hover:text-rose-600"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </>}
      />

      <section className="relative flex min-h-0 flex-1 overflow-hidden bg-white">
        {/* Left column: the loop's runs, as wide as the flow page's. */}
        <LoopRunHistory
          runs={runs}
          selectedId={shownRunId}
          onSelect={openRun}
          onResume={handleResume}
          resuming={resuming}
          onNew={startNewRun}
          onDelete={handleDeleteRun}
          className="hidden w-[220px] shrink-0 border-r border-slate-200 lg:flex xl:w-[260px] 2xl:w-[300px] min-[1800px]:w-[340px]"
        />

        {/* Centre column: the flow, then the run, the settings or the
            artifacts. */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {/* The flow the loop repeats, the working node lit; folded away
              from the header, and out of the way of the artifacts. */}
          {graphOpen && tab !== 'artifacts' && (
            <div className="relative h-[38%] min-h-[220px] shrink-0 border-b border-slate-200" data-testid="loop-flow-graph">
              {/* Mounted again when the chat opens or closes, so the graph
                  fits the width it has then. */}
              <FlowCanvas key={chatOpen ? 'beside-chat' : 'wide'} readOnly nodes={nodes} edges={edges}
                activeNodeId={activeNodeId} setSelectedNodeId={() => {}} />
            </div>
          )}

          {tab === 'setup' && (
            <div className="min-h-0 flex-1 overflow-y-auto">
              <LoopSetupForm
                draft={draft} onChange={(patch) => setDraft((d) => ({ ...d, ...patch }))}
                flows={flows} agents={agents} onSave={handleSave} saving={saving}
              />
            </div>
          )}
          {tab === 'artifacts' && (
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
              <FlowArtifacts
                artifacts={shownArtifacts.artifacts}
                views={shownArtifacts.views}
                workspace={run?.workspace || selectedWorkspace}
                picked={artifactPick}
                onPick={setArtifactPick}
              />
            </div>
          )}
          {/* Always mounted, so what the box holds survives a tab switch. */}
          <div className={`min-h-0 flex-1 overflow-hidden ${tab === 'run' ? '' : 'hidden'}`}>
            <LoopRunChat
              loop={loop} run={run} iterations={iterations} logs={shownLogs}
              nodes={nodes} live={live} liveMessages={liveMessages} workspace={selectedWorkspace} active={tab === 'run'}
              passFlowRunId={runningPass?.flow_run_id || null}
              goal={goal} onGoalChange={setGoal}
              onRun={handleStart} onStop={handleStop} starting={starting}
              estimate={estimate} onEstimate={handleEstimate} onEstimateClose={() => setEstimate(null)}
            />
          </div>
        </div>

        {/* Right column: what the flow did on each pass, as wide as the flow
            page's right column. */}
        <LoopPassLogs
          runId={run?.loop_run_id}
          iterations={run ? iterations : []}
          logs={shownLogs}
          nodeLabels={nodeLabels}
          className="hidden w-[220px] shrink-0 border-l border-slate-200 lg:flex xl:w-[260px] 2xl:w-[300px] min-[1800px]:w-[340px]"
        />

        {/* The Loop Creator, when open: a column at the right edge on a wide
            screen, over the page below 2xl, where four columns do not fit. */}
        {chatOpen && (panelHoldsChat ? (
          <div className="absolute inset-y-0 right-0 z-30 flex w-full flex-col overflow-hidden border-l border-slate-200 bg-white p-4 shadow-2xl sm:w-[26rem] 2xl:static 2xl:z-auto 2xl:w-[30rem] 2xl:shrink-0 2xl:shadow-none">
            <InPanelNote />
          </div>
        ) : (
          <LoopCreatorChat
            loopId={loop.loop_id}
            onLoopChanged={applyLoopFromChat}
            onClose={() => setChatOpen(false)}
            className="absolute inset-y-0 right-0 z-30 w-full border-l border-slate-200 shadow-2xl sm:w-[26rem] 2xl:static 2xl:z-auto 2xl:w-[30rem] 2xl:shrink-0 2xl:shadow-none"
          />
        ))}
      </section>
      {caseDialogOpen && run?.loop_run_id && (
        <SaveAsEvalCaseDialog
          runId={run.loop_run_id}
          workspace={run.workspace || selectedWorkspace}
          onClose={() => setCaseDialogOpen(false)}
        />
      )}
    </div>
  );
}

// The loop's build chat as the page-chat panel's descriptor (components/pageChat):
// the same callbacks LoopCreatorChat hands EntityChat, so both draw one conversation.
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
      t('loops.chatSuggestRun'),
    ],
  } : null), [loopId, loadChat, clearChat, stopChat, onEvent, t]);
}
