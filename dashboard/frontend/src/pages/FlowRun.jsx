import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  AlertTriangle, ClipboardList, Factory, FileText, History, ListChecks, Loader2,
  MessageSquare, Pencil, Pin, Play, Plus, Square, Workflow,
  DollarSign,
} from 'lucide-react';
import { deleteFlowChat, getAgents, getFlow, getTasks, updateFlow } from '../api';
import { useWorkspace } from '../components/workspace';
import CreateTaskModal from '../components/CreateTaskModal';
import FlowCanvas from '../components/flow/FlowCanvas';
import FlowChat from '../components/flow/FlowChat';
import FlowArtifacts from '../components/flow/FlowArtifacts';
import { AppBar } from '../components/PageLayout';
import FlowTitle from '../components/flowEditor/FlowTitle';
import NodeRunPanel from '../components/flowEditor/NodeRunPanel';
import { useI18n } from '../i18n';
import { errorDetail, useToast } from '../components/toast';
import { FlowHistory, FlowRunMessages } from '../components/flowEditor/FlowHistory';
import { reconstructRunMessages } from '../components/flowEditor/runFormat';
import { conversationArtifacts } from '../components/chat/artifactOps';
import { conversationViews } from '../components/chat/turnViews';
import { useMessagesWithTrails } from '../components/chat/runTrail';
import { FileOpenContext } from '../components/chat/fileOpenContext';
import { FlowLog } from '../components/flowEditor/FlowLog';
import { initialFlowState } from '../components/flowEditor/stateKeys';
import { nodeStatusText } from '../components/flowEditor/nodeStatus';
import { EXCLUDED_AGENT_IDS, toCanvasEdges } from '../components/flowEditor/useFlowDocument';
import { newChatNonce, useFlowRuns } from '../components/flowEditor/useFlowRuns';
import { isUnchosenAgent, normalizeNode } from '../components/flowEditor/graphHelpers';
import PageLoader from '../components/PageLoader';
import { ColumnTab, PaneTab } from '../components/PageTabs';
import FlowTaskList from '../components/flowEditor/FlowTaskList';

const STATUS_STYLES = {
  running: 'bg-cyan-50 text-cyan-700',
  done: 'bg-emerald-50 text-emerald-700',
  error: 'bg-rose-50 text-rose-700',
  pending: 'bg-amber-50 text-amber-700',
  stopped: 'bg-orange-50 text-orange-700',
  skipped: 'bg-slate-100 text-slate-500',
  ready: 'bg-slate-100 text-slate-500',
};

// The flow run page (/flows/:flowId): start the flow, chat with it, follow
// its nodes live and go through its history. It never changes the flow: the
// canvas is read only and the task picked here goes with the run, not into
// the flow. Editing is the editor's (/flows/:flowId/edit, pages/FlowEditor.jsx).
const NO_MESSAGES = [];
// The task picker of the left column, hidden: a task goes with a run as an
// attachment in the chat box now. Kept, for when it comes back.
const SHOW_TASK_PICKER = false;

function FlowRun() {
  const { t } = useI18n();
  const toast = useToast();
  const navigate = useNavigate();
  // `/flows/<id>/runs/<run_group>` opens one conversation or run of the
  // flow (App.jsx); the bare `/flows/<id>` is a new chat.
  const { flowId, runGroup: routeGroup } = useParams();
  const { selectedWorkspace, liveUpdates } = useWorkspace();
  // The flow is loaded for this flow and workspace; another one shows the
  // loader again, derived rather than set inside the effect.
  const loadKey = `${flowId}|${selectedWorkspace || ''}`;
  const [loadedKey, setLoadedKey] = useState(null);
  const loading = loadedKey !== loadKey;
  const [running, setRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [runNodeId, setRunNodeId] = useState(null);
  const [flow, setFlow] = useState(null);
  const [keeping, setKeeping] = useState(false);
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [agents, setAgents] = useState([]);
  const [tasks, setTasks] = useState([]);
  // The task the next run works on. Starts as the flow's own task and is
  // never written back to the flow.
  const [taskId, setTaskId] = useState(null);
  const [logs, setLogs] = useState([]);
  // Live execution events for the active chat run, fed from the chat stream
  // (FlowChat -> onStreamEvent); see useFlowRuns.handleStreamEvent.
  const [streamLogs, setStreamLogs] = useState([]);
  const [runs, setRuns] = useState([]);
  // One record per execution (flow/run_store.py), keyed by flow_run_id: what
  // says whether a History record can be resumed.
  const [instances, setInstances] = useState({});
  const [resumingRun, setResumingRun] = useState(null);
  const [estimate, setEstimate] = useState(null);
  const [estimating, setEstimating] = useState(false);
  // The record the address names, open from the start: its route, or the
  // older `?run=<run_group>` link, which is moved to the route below.
  const [params] = useSearchParams();
  const runFromUrl = routeGroup || params.get('run') || null;
  const [selectedRunGroup, setSelectedRunGroup] = useState(runFromUrl);
  const [rightTab, setRightTab] = useState(runFromUrl ? 'logs' : 'node'); // node | logs
  const [leftTab, setLeftTab] = useState(runFromUrl || !SHOW_TASK_PICKER ? 'history' : 'tasks'); // tasks | history
  const [centerTab, setCenterTab] = useState('canvas'); // canvas | chat | artifacts
  // The files and views the live chat's nodes produced (FlowChat onArtifacts).
  const [chatArtifacts, setChatArtifacts] = useState({ artifacts: {}, views: [] });
  // What the Artifacts tab has open (`file:<path>` / `view:<id>`), null for its
  // own default. A file clicked in the chat or the log opens it there.
  const [artifactPick, setArtifactPick] = useState(null);
  const showFileInArtifacts = useCallback((path) => {
    setArtifactPick(`file:${path}`);
    setCenterTab('artifacts');
  }, []);
  // A fresh, unique token per chat thread (see newChatNonce).
  const [chatNonce, setChatNonce] = useState(newChatNonce);
  // A resumed chat History record: { conversationId, messages, key } or null.
  const [resumedChat, setResumedChat] = useState(null);
  const [showCreateModal, setShowCreateModal] = useState(false);

  // Starting from a node (NodeRunPanel). Without a History record picked, the
  // node the next chat message goes to; with one, a request to run that chat
  // record's last turn again from a node (FlowChat rerunRequest).
  const [startNodeId, setStartNodeId] = useState(null);
  const [rerunRequest, setRerunRequest] = useState(null);

  useEffect(() => {
    let alive = true;
    Promise.all([getFlow(flowId), getAgents(selectedWorkspace || null)])
      .then(([flowResponse, agentsResponse]) => {
        if (!alive) return;
        const nextFlow = flowResponse.data;
        setFlow(nextFlow);
        setTaskId(nextFlow.task_id || null);
        setNodes((nextFlow.nodes || []).map((node) => normalizeNode(node)));
        setEdges(toCanvasEdges(nextFlow.edges));
        setAgents((agentsResponse.data || []).filter((a) => !EXCLUDED_AGENT_IDS.has(a.id)));
      })
      .catch((error) => {
        console.error('Failed to load flow', error);
        if (alive) navigate('/flows');
      })
      .finally(() => { if (alive) setLoadedKey(loadKey); });
    return () => { alive = false; };
  }, [flowId, selectedWorkspace, loadKey, navigate]);

  const loadTasks = useCallback(async (workspaceName) => {
    if (!workspaceName) return;
    try {
      const response = await getTasks(workspaceName);
      setTasks(response.data || []);
    } catch (error) {
      console.error('Failed to load tasks', error);
    }
  }, []);

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) || null,
    [nodes, selectedNodeId]
  );

  const runApi = useFlowRuns({
    chatNonce, flow, flowId, liveUpdates, loadTasks, logs, nodes,
    resumedChat, running, runs, selectedNode, selectedRunGroup,
    selectedWorkspace, setCenterTab, setChatNonce, setEstimate, setEstimating, setFlow,
    setInstances, setLogs, setResumedChat, setResumingRun, setRightTab,
    setRunNodeId, setRunning, setRuns, setSelectedNodeId, setSelectedRunGroup, setStopping,
    setStreamLogs, streamLogs, t, taskId, toast,
  });


  const agentLabels = useMemo(() => {
    const map = {};
    for (const agent of agents) map[agent.id] = agent.name;
    for (const node of nodes) {
      if (node.data?.agent_id) map[node.data.agent_id] = node.data.label || map[node.data.agent_id];
    }
    return map;
  }, [agents, nodes]);

  const assignedTask = useMemo(
    () => tasks.find((task) => String(task.id) === String(taskId)) || null,
    [tasks, taskId]
  );

  const unchosenCount = useMemo(() => nodes.filter((n) => isUnchosenAgent(n.data)).length, [nodes]);

  // The address names the conversation on screen, so coming back to it (a
  // reload, the browser's Back, a link) opens it again: a picked History
  // record, a resumed chat, or the new chat once its first turn has started.
  // The bare flow address is a new chat with nothing sent yet. What changes
  // the conversation on screen changes the address with it (goTo).
  const liveGroup = streamLogs.length ? runApi.activeConversationId : null;
  const shownGroup = selectedRunGroup || resumedChat?.key || liveGroup;
  const goTo = (group, { push = false } = {}) => {
    const path = group ? `/flows/${flowId}/runs/${encodeURIComponent(group)}` : `/flows/${flowId}`;
    navigate(path, { replace: !push });
  };
  const startNewChat = () => {
    runApi.handleNewChat();
    setStartNodeId(null);
    goTo(null);
  };
  // An older `?run=<run_group>` link moves to the record's own address.
  useEffect(() => {
    const legacy = params.get('run');
    if (legacy && !routeGroup) navigate(`/flows/${flowId}/runs/${encodeURIComponent(legacy)}`, { replace: true });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // An address changed from outside (Back / Forward, a link to another run
  // of this flow) opens what it names. Adjusted while rendering.
  const addressGroup = routeGroup || null;
  const [seenAddress, setSeenAddress] = useState(addressGroup);
  if (addressGroup !== seenAddress) {
    setSeenAddress(addressGroup);
    if (addressGroup !== shownGroup) {
      if (addressGroup) {
        setResumedChat(null);
        setSelectedRunGroup(addressGroup);
        setLeftTab('history');
        setRightTab('logs');
      } else {
        runApi.handleNewChat();
      }
    }
  }

  // A chat record opened by its address or from History continues in the
  // live chat: its turns are rebuilt from the record once it has loaded,
  // unless it is the conversation the chat already holds. Adjusted while
  // rendering, once per record opened.
  const openRecord = runApi.selectedRun;
  const openChatKey = openRecord?.kind === 'chat' ? openRecord.run_group : null;
  const [resumedFor, setResumedFor] = useState(null);
  if (openChatKey !== resumedFor) {
    setResumedFor(openChatKey);
    const holding = resumedChat
      ? resumedChat.key === openChatKey
      : openChatKey === runApi.activeConversationId;
    if (openChatKey && !holding) {
      setResumedChat({
        conversationId: openRecord.conversation_id || openRecord.run_group,
        messages: reconstructRunMessages(openRecord),
        key: openRecord.run_group,
      });
      setCenterTab('chat');
    }
  }
  // A new chat's first turn gives it its address.
  const handleStreamEvent = (event) => {
    if (event?.type === 'flow_meta' && !resumedChat && !selectedRunGroup) goTo(runApi.activeConversationId);
    runApi.handleStreamEvent(event);
  };

  // The Artifacts tab: a task-run record's own files and views (its nodes'
  // runs), otherwise the live chat's.
  const taskRunOpen = Boolean(runApi.selectedRun && runApi.selectedRun.kind !== 'chat');
  const taskRunMessages = useMessagesWithTrails(taskRunOpen ? runApi.runMessages : NO_MESSAGES, taskRunOpen);
  const shownArtifacts = useMemo(() => (taskRunOpen
    ? {
      artifacts: conversationArtifacts(taskRunMessages),
      views: conversationViews(taskRunMessages).filter((v) => v.view_kind !== 'code'),
    }
    : chatArtifacts), [taskRunOpen, taskRunMessages, chatArtifacts]);
  const artifactCount = Object.keys(shownArtifacts.artifacts).length + shownArtifacts.views.length;

  // A node picked on the canvas opens on the right with its settings, a
  // History record picked or not; the Logs tab, opened again, shows that
  // node's lines.
  const pickNode = (nodeId) => {
    setSelectedNodeId(nodeId);
    if (nodeId) setRightTab('node');
  };
  // The node the next chat message goes to, only while no History record is
  // picked (a picked one runs again from its own inputs instead).
  const startNodeData = !selectedRunGroup && startNodeId ? nodes.find((n) => n.id === startNodeId) : null;
  const startNode = startNodeData ? { id: startNodeData.id, label: startNodeData.data?.label || startNodeData.id } : null;
  const clearStartNode = () => setStartNodeId(null);
  // A picked History record run again from a node: a chat record as a turn of
  // its conversation, in the live chat that holds it; a task run as a new run.
  const rerunFromNode = (node, mode) => {
    const record = runApi.selectedRun;
    if (!record) return;
    if (record.kind === 'chat') {
      setRerunRequest({ node_id: node.id, label: node.data?.label || node.id, mode, key: newChatNonce() });
      setCenterTab('chat');
    } else {
      runApi.handleRerunTaskRun(node.id, mode);
    }
  };
  const handleRerunHandled = (sent) => {
    if (!sent) toast.error(t('flowEditor.nodePanel.chatBusy'));
  };

  if (loading || !flow) {
    return (
      <div className="h-full bg-white"><PageLoader /></div>
    );
  }

  const chatWorkspace = flow.workspace || selectedWorkspace;
  const isRunning = running || !!flow.running;
  const editPath = `/flows/${flowId}/edit`;
  // Deleting a chat from History. The chat on screen, when it is that one,
  // gives way to a new empty chat.
  // Deleting a History entry: a chat with its conversation, a task run with
  // its record (the task stays on the board).
  const deleteHistoryEntry = async (run) => {
    const isChat = run.kind === 'chat';
    if (!confirm(t(isChat ? 'flowEditor.confirmDeleteChat' : 'flowEditor.confirmDeleteRun'))) return;
    const group = run.run_group;
    try {
      await deleteFlowChat(flowId, group);
    } catch (error) {
      toast.error(t(isChat ? 'flowEditor.deleteChatFailed' : 'flowEditor.deleteRunFailed'), errorDetail(error));
      return;
    }
    setRuns((prev) => prev.filter((r) => r.run_group !== group));
    setInstances((prev) => {
      if (!prev[group]) return prev;
      const { [group]: _gone, ...rest } = prev;
      return rest;
    });
    const liveId = resumedChat?.conversationId || (chatNonce ? `flow-chat-${flowId}-${chatNonce}` : `flow-chat-${flowId}`);
    if (selectedRunGroup === group || liveId === group || liveId === run.conversation_id) {
      startNewChat();
    }
  };
  // Keeping a temporary flow puts it in the flow list for good.
  const keepFlow = async () => {
    setKeeping(true);
    try {
      await updateFlow(flowId, { ephemeral: false });
      setFlow((prev) => (prev ? { ...prev, ephemeral: false } : prev));
      toast.success(t('flowEditor.temporary.kept'));
    } catch (error) {
      toast.error(t('flowEditor.temporary.keepFailed'), errorDetail(error));
    } finally {
      setKeeping(false);
    }
  };

  return (
    <FileOpenContext.Provider value={showFileInArtifacts}>
    <div className="h-full flex flex-col overflow-hidden">
      <AppBar
        icon={Factory}
        title={<FlowTitle flow={flow} onRenamed={(name) => setFlow((prev) => (prev ? { ...prev, name } : prev))} />}
        subtitle={flow.description}
        backTo="/flows"
        backLabel={t('flowEditor.flows')}
        // The center column's switch sits on the title line here, so the
        // canvas and the chat get the row it took. From lg up the title takes
        // the left column's width, less the bar's padding and gap, so the
        // switch starts over the center column where it stood before.
        titleClassName="flex-1 lg:w-[200px] lg:flex-none xl:w-[240px] 2xl:w-[280px] min-[1800px]:w-[320px]"
        center={(
          <div className="inline-flex items-center gap-1 rounded-lg bg-gray-100 p-0.5">
            <PaneTab
              active={centerTab === 'canvas'} onClick={() => setCenterTab('canvas')}
              icon={Workflow} label={t('flowEditor.canvas')}
            />
            <PaneTab
              active={centerTab === 'chat'} onClick={() => setCenterTab('chat')}
              icon={MessageSquare} label={t('flowEditor.chat')}
            />
            <PaneTab
              active={centerTab === 'artifacts'} onClick={() => setCenterTab('artifacts')}
              icon={FileText} label={t('chat.artifacts')} badge={artifactCount}
            />
          </div>
        )}
        actions={<>
          {isRunning && (
            <button
              onClick={runApi.handleStopFlow}
              disabled={stopping}
              className="flex items-center rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-sm font-semibold text-rose-600 shadow-sm transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {stopping
                ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                : <Square className="mr-2 h-4 w-4" />}
              {stopping ? t('flowEditor.stopping') : t('flowEditor.stop')}
            </button>
          )}
          <Link
            to={editPath}
            className="flex items-center rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm font-semibold text-gray-700 shadow-sm transition-colors hover:bg-gray-100"
            data-testid="flow-edit-link"
          >
            <Pencil className="mr-2 h-4 w-4 text-cyan-500" />
            {t('flowEditor.editFlow')}
          </Link>
        </>}
      />

      {/* A flow an agent built for one request (run_new_flow_tool): in no list,
          deleted a week after its last change unless someone keeps it. */}
      {flow.ephemeral ? (
        <div className="flex items-center gap-2 border-b border-sky-200 bg-sky-50 px-4 py-2 text-sm text-sky-800"
          data-testid="flow-temporary-banner">
          <Workflow className="h-4 w-4 shrink-0 text-sky-500" />
          <span className="min-w-0 flex-1">
            {t('flowEditor.temporary.banner', { agent: flow.created_by_agent || t('flowEditor.temporary.anAgent') })}
          </span>
          <button
            type="button"
            onClick={keepFlow}
            disabled={keeping}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-sky-600 px-3 py-1 text-xs font-semibold text-white hover:bg-sky-700 disabled:opacity-50"
            data-testid="flow-keep"
          >
            {keeping ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Pin className="h-3.5 w-3.5" />}
            {t('flowEditor.temporary.keep')}
          </button>
        </div>
      ) : null}

      {unchosenCount > 0 ? (
        <div className="flex items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
          <AlertTriangle className="h-4 w-4 shrink-0 text-amber-500" />
          <span className="min-w-0 flex-1">{t('flowEditor.unchosenAgentsRun', { count: unchosenCount })}</span>
          <Link to={editPath} className="shrink-0 font-semibold underline">{t('flowEditor.editFlow')}</Link>
        </div>
      ) : null}

      <section className="flex min-h-0 flex-1 overflow-hidden bg-white">
        {/* Left column — tasks and history */}
        <div className="hidden w-[220px] shrink-0 flex-col overflow-hidden border-r border-slate-200 lg:flex xl:w-[260px] 2xl:w-[300px] min-[1800px]:w-[340px]">
          {/* The tabs are the column's title (components/PageTabs.jsx). */}
          {SHOW_TASK_PICKER ? (
            <div className="grid shrink-0 grid-cols-2 border-b border-gray-200">
              <ColumnTab
                active={leftTab === 'tasks'} onClick={() => setLeftTab('tasks')}
                icon={ClipboardList} label={t('flowEditor.tasks')}
              />
              <ColumnTab
                active={leftTab === 'history'} onClick={() => setLeftTab('history')}
                icon={History} label={t('flowEditor.history')}
              />
            </div>
          ) : (
            // Only the history, with New chat over it.
            <div className="shrink-0 border-b border-slate-200 p-3">
              <button
                type="button"
                onClick={startNewChat}
                className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm font-bold text-emerald-700 shadow-sm transition hover:bg-emerald-100"
                data-testid="flow-new-chat"
              >
                <MessageSquare className="h-4 w-4" />
                {t('flowEditor.newChat')}
              </button>
            </div>
          )}
          {SHOW_TASK_PICKER && (
            <div className={`min-h-0 flex-1 overflow-y-auto p-3 ${leftTab === 'tasks' ? '' : 'hidden'}`}>
              <div className="space-y-5">
                <button
                  type="button"
                  onClick={startNewChat}
                  className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm font-bold text-emerald-700 shadow-sm transition hover:bg-emerald-100"
                >
                  <MessageSquare className="h-4 w-4" />
                  {t('flowEditor.newChat')}
                </button>

                {/* The chosen task and the button that runs the flow on it,
                    only while a task is chosen: picking one in the list below
                    brings them back. */}
                {assignedTask ? (
                  <>
                    <div className="rounded-[20px] border border-cyan-100 bg-cyan-50/70 p-4">
                      <div className="flex items-center justify-between">
                        <div className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-700">{t('flowEditor.currentTask')}</div>
                        <button
                          type="button"
                          onClick={() => setTaskId(null)}
                          className="text-xs font-medium text-slate-400 transition hover:text-rose-500"
                        >
                          {t('flowEditor.detach')}
                        </button>
                      </div>
                      <div className="mt-2 text-sm font-bold text-slate-900">{assignedTask.title}</div>
                      <div className="mt-1 text-sm leading-6 text-slate-600">{assignedTask.description || t('flowEditor.noTaskDescription')}</div>
                    </div>

                    <button
                      onClick={runApi.handleRunFlow}
                      disabled={isRunning || unchosenCount > 0}
                      className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-cyan-600 px-4 py-2.5 text-sm font-bold text-white shadow-sm transition hover:bg-cyan-700 disabled:cursor-not-allowed disabled:bg-cyan-400"
                      data-testid="flow-run-button"
                    >
                      {isRunning
                        ? <Loader2 className="h-4 w-4 animate-spin" />
                        : <Play className="h-4 w-4" />}
                      {isRunning ? t('common.running') : t('flowEditor.runFlowWithTask')}
                    </button>
                  </>
                ) : null}

                <div className="space-y-2">
                  <button
                    type="button"
                    onClick={runApi.handleEstimate}
                    disabled={estimating}
                    className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-cyan-300 hover:bg-cyan-50 hover:text-cyan-700 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <DollarSign className="h-4 w-4" />
                    {estimating ? t('flowEditor.estimating') : t('flowEditor.estimateCost')}
                  </button>
                  {estimate ? (
                    <div className="rounded-[20px] border border-slate-200 bg-white p-4">
                      <div className="flex items-baseline justify-between gap-2">
                        <div className="text-sm font-bold text-slate-900">
                          ${Number(estimate.total_usd || 0).toFixed(4)}
                        </div>
                        <button
                          type="button"
                          onClick={() => setEstimate(null)}
                          className="text-[11px] font-medium text-slate-400 transition hover:text-slate-600"
                        >
                          {t('flowEditor.hideEstimate')}
                        </button>
                      </div>
                      <div className="mt-2 space-y-1">
                        {(estimate.per_node || []).map((row) => (
                          <div key={row.node_id} className="flex items-baseline justify-between gap-2 text-[11px]">
                            <span className="truncate text-slate-600">{row.label || row.node_id}</span>
                            <span className="shrink-0 font-mono text-slate-500">
                              {row.model || t('flowEditor.noModelCall')} · ${Number(row.usd || 0).toFixed(4)}
                            </span>
                          </div>
                        ))}
                      </div>
                      <p className="mt-2 text-[11px] leading-5 text-slate-400">
                        {t('flowEditor.flowEstimateNote')}
                      </p>
                    </div>
                  ) : null}
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="text-sm font-bold text-slate-900">{t('flowEditor.tasks')}</div>
                    <button
                      type="button"
                      onClick={() => setShowCreateModal(true)}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-cyan-300 hover:bg-cyan-50 hover:text-cyan-700"
                    >
                      <Plus className="h-3.5 w-3.5" />
                      {t('flowEditor.newTask')}
                    </button>
                  </div>
                  <FlowTaskList
                    tasks={tasks}
                    taskId={taskId}
                    onPick={(task) => setTaskId(String(task.id))}
                    workspace={chatWorkspace}
                  />
                </div>
              </div>
            </div>

          )}
          {/* Edge to edge: the History entries are rows across the column. */}
          <div className={`min-h-0 flex-1 overflow-y-auto ${leftTab === 'history' ? '' : 'hidden'}`}>
            <FlowHistory
              runs={runApi.displayRuns}
              instances={instances}
              onResume={runApi.handleResumeRun}
              onDelete={deleteHistoryEntry}
              resumingRun={resumingRun}
              selectedRunGroup={selectedRunGroup}
              onSelect={(run) => {
                const group = run.run_group;
                const isReselect = selectedRunGroup === group;
                setSelectedRunGroup(isReselect ? null : group);
                // Its own address (Back returns to what was open before);
                // deselected, the address is the chat left on screen.
                if (isReselect) goTo(resumedChat ? null : liveGroup);
                else goTo(group, { push: true });
                setSelectedNodeId(null);
                setStartNodeId(null);
                setRightTab('logs');
                setCenterTab('chat');
                // A chat record resumes in the live chat (the effect above, so
                // an address that names it does the same). Deselecting, or a
                // read-only task-run record, leaves an empty new chat.
                if (run.kind !== 'chat' || isReselect) setResumedChat(null);
              }}
            />
          </div>
        </div>

        {/* Center column — live canvas and chat */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {/* Canvas — read only, kept mounted so the viewport survives tab
              switches. It shows which node is running; clicking a node opens
              it on the right. */}
          <div className={`relative min-h-0 min-w-0 flex-1 basis-1/2 ${centerTab === 'canvas' ? 'block' : 'hidden'}`}>
            <FlowCanvas
              readOnly
              nodes={nodes}
              edges={edges}
              activeNodeId={runApi.activeNodeId}
              selectedNodeId={selectedNodeId}
              setSelectedNodeId={pickNode}
            />
          </div>

          {/* Chat — always mounted (thread survives tab switches). On the Canvas
              tab it's a bottom strip; on the Chat tab it fills the pane. A chat
              History record resumes in the live chat; a task-run record shows a
              read-only transcript instead. */}
          <div
            className={`flex min-h-0 min-w-0 flex-col overflow-hidden ${
              centerTab === 'chat' ? 'flex-1'
                : centerTab === 'artifacts' ? 'hidden'
                  : 'flex-1 basis-1/2 border-t border-slate-200'
            }`}
          >
            {runApi.selectedRun && runApi.selectedRun.kind !== 'chat' ? (
              <FlowRunMessages
                run={runApi.selectedRun}
                messages={taskRunMessages}
                live={runApi.liveRunMessages}
                onClose={() => { setSelectedRunGroup(null); goTo(liveGroup); }}
                workspace={chatWorkspace}
                status={instances[runApi.selectedRun.run_group]?.status || null}
                onAnswer={(answer) => runApi.handleResumeRun(runApi.selectedRun.run_group, answer)}
              />
            ) : (
              <FlowChat
                flow={flow}
                flowId={flowId}
                workspace={chatWorkspace}
                agentLabels={agentLabels}
                chatNonce={chatNonce}
                resumeConversationId={resumedChat?.conversationId || null}
                resumeMessages={resumedChat?.messages || null}
                resumeKey={resumedChat?.key || null}
                onStreamEvent={handleStreamEvent}
                onActivity={runApi.handleChatActivity}
                onArtifacts={setChatArtifacts}
                startNode={startNode}
                onClearStartNode={clearStartNode}
                rerunRequest={rerunRequest}
                onRerunHandled={handleRerunHandled}
              />
            )}
          </div>

          {/* Artifacts — the files and views the run produced, the whole
              pane wide; the chat stays mounted behind it. */}
          {centerTab === 'artifacts' && (
            <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
              <FlowArtifacts
                artifacts={shownArtifacts.artifacts}
                views={shownArtifacts.views}
                workspace={chatWorkspace}
                picked={artifactPick}
                onPick={setArtifactPick}
              />
            </div>
          )}
        </div>

        {/* Right column — nodes and their state, execution log */}
        <div className="flex w-[220px] shrink-0 flex-col overflow-hidden border-l border-slate-200 bg-white xl:w-[260px] 2xl:w-[300px] min-[1800px]:w-[340px]">
          <div className="grid shrink-0 grid-cols-2 border-b border-gray-200">
            <ColumnTab
              active={rightTab === 'node'}
              onClick={() => { setRightTab('node'); setCenterTab('canvas'); }}
              icon={ListChecks} label={selectedNode ? t('flowEditor.node') : t('flowEditor.nodes')}
            />
            <ColumnTab
              active={rightTab === 'logs'} onClick={() => setRightTab('logs')}
              icon={FileText} label={t('flowEditor.logs')}
            />
          </div>

          <div className={`min-h-0 flex-1 ${rightTab === 'logs' ? 'overflow-hidden' : 'overflow-y-auto p-3'}`}>
            {rightTab === 'node' ? (
              selectedNode ? (
                <NodeRunPanel
                  node={selectedNode}
                  agents={agents}
                  editPath={editPath}
                  record={runApi.selectedRun || null}
                  running={runNodeId?.startsWith(`${selectedNode.id}:`) ? runNodeId.slice(selectedNode.id.length + 1) : null}
                  addressed={startNodeId === selectedNode.id}
                  onRerun={(mode) => rerunFromNode(selectedNode, mode)}
                  onAddress={() => { setStartNodeId(selectedNode.id); setCenterTab('canvas'); }}
                  onCancelAddress={() => setStartNodeId(null)}
                />
              ) : (
                <div className="space-y-1.5">
                  {runApi.nodeStatuses.map((row) => (
                    <button
                      key={row.id}
                      type="button"
                      onClick={() => setSelectedNodeId(row.id)}
                      className="flex w-full items-center justify-between gap-2 rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-left transition hover:border-cyan-300 hover:bg-cyan-50"
                    >
                      <span className="truncate text-sm font-semibold text-slate-800">{row.label}</span>
                      <span
                        className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${STATUS_STYLES[row.status] || STATUS_STYLES.ready}`}
                        title={nodeStatusText(t, row).title}
                      >
                        {nodeStatusText(t, row).badge}
                      </span>
                    </button>
                  ))}
                  {nodes.length === 0 ? (
                    <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
                      {t('flowEditor.emptyFlowRun')}{' '}
                      <Link to={editPath} className="font-semibold text-cyan-700 underline">{t('flowEditor.editFlow')}</Link>
                    </div>
                  ) : null}
                </div>
              )
            ) : (
              <>
                <FlowLog
                  logs={runApi.visibleLogs}
                  stateLogs={runApi.baseLogs}
                  initialState={initialFlowState(flow)}
                  // What is picked, and so what the log shows.
                  selection={[
                    runApi.selectedRun
                      ? `${t(runApi.selectedRun.kind === 'chat' ? 'flowEditor.chat' : 'flowEditor.taskRun')}: ${runApi.selectedRun.title || t('flowEditor.selectedRun')}`
                      : '',
                    selectedNode ? `${t('flowEditor.node')}: ${selectedNode.data.label}` : '',
                  ].filter(Boolean).join(' · ')}
                  running={runApi.isExecuting && !runApi.selectedRun}
                  nodeStatuses={runApi.nodeStatuses}
                />
              </>
            )}
          </div>
        </div>
      </section>

      {showCreateModal && (
        <CreateTaskModal
          selectedWorkspace={chatWorkspace}
          onClose={() => setShowCreateModal(false)}
          onCreated={async (task) => {
            if (task?.id) setTaskId(String(task.id));
            await loadTasks(chatWorkspace);
          }}
        />
      )}
    </div>
    </FileOpenContext.Provider>
  );
}

export default FlowRun;