import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  AlertTriangle, ClipboardList, DollarSign, Factory, FileText, History, ListChecks, Loader2,
  MessageSquare, Pencil, Play, Plus, Square, Workflow,
} from 'lucide-react';
import { getAgents, getFlow, getTasks } from '../api';
import LiveRunStream from '../components/LiveRunStream';
import { useWorkspace } from '../components/workspace';
import CreateTaskModal from '../components/CreateTaskModal';
import FlowCanvas from '../components/flow/FlowCanvas';
import FlowChat from '../components/flow/FlowChat';
import { AppBar, InfoHint } from '../components/PageLayout';
import { useI18n } from '../i18n';
import { useToast } from '../components/toast';
import { FlowHistory, FlowRunMessages } from '../components/flowEditor/FlowHistory';
import { reconstructRunMessages } from '../components/flowEditor/runFormat';
import { FlowLog } from '../components/flowEditor/FlowLog';
import { EXCLUDED_AGENT_IDS, toCanvasEdges } from '../components/flowEditor/useFlowDocument';
import { newChatNonce, useFlowRuns } from '../components/flowEditor/useFlowRuns';
import { isUnchosenAgent, normalizeNode } from '../components/flowEditor/graphHelpers';
import PageLoader from '../components/PageLoader';
import { ColumnTab, PaneTab } from '../components/PageTabs';

const STATUS_STYLES = {
  running: 'bg-cyan-50 text-cyan-700',
  done: 'bg-emerald-50 text-emerald-700',
  error: 'bg-rose-50 text-rose-700',
  pending: 'bg-amber-50 text-amber-700',
  ready: 'bg-slate-100 text-slate-500',
};

// One node as the run page sees it: what it is and what it was told, read
// only, with the one action that belongs here, running it on its own.
// Clicking the empty canvas deselects it.
function NodeRunCard({ node, agentLabels, onRun, running }) {
  const { t } = useI18n();
  const d = node.data || {};
  const unchosen = isUnchosenAgent(d);
  const what = d.category === 'agent'
    ? (unchosen ? t('flowEditor.noAgentChosen') : agentLabels[d.agent_id] || d.agent_id)
    : d.entity_id;
  return (
    <div className="space-y-3">
      <div className="rounded-[20px] border border-slate-200 bg-slate-50 p-4">
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1 truncate text-sm font-bold text-slate-900">{d.label}</div>
          <InfoHint
            text={d.description ? <span className="whitespace-pre-wrap">{d.description}</span> : null}
            label={t('flowEditor.aboutNode')}
            align="right"
          />
        </div>
        <div className="mt-1.5 flex min-w-0 items-center gap-2">
          <span className="shrink-0 rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-medium text-slate-600">
            {d.category || 'agent'}
          </span>
          <span className={`truncate text-xs font-medium ${unchosen ? 'text-amber-600' : 'text-slate-400'}`}>{what}</span>
        </div>
        {d.nodeTask ? (
          <div className="mt-3 max-h-48 overflow-y-auto whitespace-pre-wrap rounded-xl bg-white p-3 text-xs leading-5 text-slate-600">
            {d.nodeTask}
          </div>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onRun}
          disabled={running || unchosen}
          className="inline-flex items-center gap-2 rounded-2xl border border-cyan-200 bg-cyan-50 px-4 py-2 text-sm font-semibold text-cyan-700 transition hover:bg-cyan-100 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          {running ? t('flowEditor.runningNode') : t('flowEditor.runSelectedNode')}
        </button>
      </div>
    </div>
  );
}

// The flow run page (/flows/:flowId): start the flow, chat with it, follow
// its nodes live and go through its history. It never changes the flow: the
// canvas is read only and the task picked here goes with the run, not into
// the flow. Editing is the editor's (/flows/:flowId/edit, pages/FlowEditor.jsx).
function FlowRun() {
  const { t } = useI18n();
  const toast = useToast();
  const navigate = useNavigate();
  const { flowId } = useParams();
  const { selectedWorkspace, liveUpdates } = useWorkspace();
  // The flow is loaded for this flow and workspace; another one shows the
  // loader again, derived rather than set inside the effect.
  const loadKey = `${flowId}|${selectedWorkspace || ''}`;
  const [loadedKey, setLoadedKey] = useState(null);
  const loading = loadedKey !== loadKey;
  const [running, setRunning] = useState(false);
  // Session the currently-running flow publishes its agent events on. Captured
  // from the run response so the live panel can attach before any log arrives.
  const [flowSessionId, setFlowSessionId] = useState(null);
  const [stopping, setStopping] = useState(false);
  const [runNodeId, setRunNodeId] = useState(null);
  const [flow, setFlow] = useState(null);
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
  // ?run=<flow_run_id> opens one execution's History record (the owner chip
  // on a view, a run group). Written back when a record is picked here, so
  // the address stays shareable.
  const [params, setParams] = useSearchParams();
  const runFromUrl = params.get('run');
  const [selectedRunGroup, setSelectedRunGroup] = useState(runFromUrl || null);
  const [rightTab, setRightTab] = useState(runFromUrl ? 'logs' : 'node'); // node | logs
  const [leftTab, setLeftTab] = useState(runFromUrl ? 'history' : 'tasks'); // tasks | history
  const [centerTab, setCenterTab] = useState('canvas'); // canvas | chat
  // A fresh, unique token per chat thread (see newChatNonce).
  const [chatNonce, setChatNonce] = useState(newChatNonce);
  // A resumed chat History record: { conversationId, messages, key } or null.
  const [resumedChat, setResumedChat] = useState(null);
  const [showCreateModal, setShowCreateModal] = useState(false);

  // Nodes carry onRunNode for the canvas; this keeps it pointing at the
  // current handler without reloading the flow when the handler changes.
  const handleRunNodeRef = useRef(() => {});
  const runNode = useCallback((nodeId) => handleRunNodeRef.current(nodeId), []);

  useEffect(() => {
    let alive = true;
    Promise.all([getFlow(flowId), getAgents(selectedWorkspace || null)])
      .then(([flowResponse, agentsResponse]) => {
        if (!alive) return;
        const nextFlow = flowResponse.data;
        setFlow(nextFlow);
        setTaskId(nextFlow.task_id || null);
        setNodes((nextFlow.nodes || []).map((node) => normalizeNode(node, runNode)));
        setEdges(toCanvasEdges(nextFlow.edges));
        setAgents((agentsResponse.data || []).filter((a) => !EXCLUDED_AGENT_IDS.has(a.id)));
      })
      .catch((error) => {
        console.error('Failed to load flow', error);
        if (alive) navigate('/flows');
      })
      .finally(() => { if (alive) setLoadedKey(loadKey); });
    return () => { alive = false; };
  }, [flowId, selectedWorkspace, loadKey, navigate, runNode]);

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
    chatNonce, flow, flowId, flowSessionId, liveUpdates, loadTasks, logs, nodes,
    resumedChat, running, runs, selectedNode, selectedNodeId, selectedRunGroup,
    selectedWorkspace, setCenterTab, setChatNonce, setEstimate, setEstimating, setFlow,
    setFlowSessionId, setInstances, setLogs, setResumedChat, setResumingRun, setRightTab,
    setRunNodeId, setRunning, setRuns, setSelectedNodeId, setSelectedRunGroup, setStopping,
    setStreamLogs, streamLogs, t, taskId, toast,
  });

  useEffect(() => { handleRunNodeRef.current = runApi.handleRunNode; });

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

  if (loading || !flow) {
    return (
      <div className="h-full bg-white"><PageLoader /></div>
    );
  }

  const chatWorkspace = flow.workspace || selectedWorkspace;
  const isRunning = running || !!flow.running;
  const editPath = `/flows/${flowId}/edit`;

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <AppBar
        icon={Factory}
        title={flow.name}
        subtitle={flow.description}
        backTo="/flows"
        backLabel={t('flowEditor.flows')}
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

      {unchosenCount > 0 ? (
        <div className="flex items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
          <AlertTriangle className="h-4 w-4 shrink-0 text-amber-500" />
          <span className="min-w-0 flex-1">{t('flowEditor.unchosenAgentsRun', { count: unchosenCount })}</span>
          <Link to={editPath} className="shrink-0 font-semibold underline">{t('flowEditor.editFlow')}</Link>
        </div>
      ) : null}

      <section className="flex min-h-0 flex-1 overflow-hidden bg-white">
        {/* Left column — tasks and history */}
        <div className="hidden w-[340px] shrink-0 flex-col overflow-hidden border-r border-slate-200 lg:flex">
          {/* The tabs are the column's title, as on the playground's
              scenario page (components/PageTabs.jsx). */}
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
          <div className={`min-h-0 flex-1 overflow-y-auto p-3 ${leftTab === 'tasks' ? '' : 'hidden'}`}>
            <div className="space-y-5">
              <button
                type="button"
                onClick={runApi.handleNewChat}
                className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm font-bold text-emerald-700 shadow-sm transition hover:bg-emerald-100"
              >
                <MessageSquare className="h-4 w-4" />
                {t('flowEditor.newChat')}
              </button>

              {assignedTask ? (
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
              ) : (
                <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
                  {t('flowEditor.noTaskIsCurrentlyAttached')}
                </div>
              )}

              <button
                onClick={runApi.handleRunFlow}
                disabled={isRunning || unchosenCount > 0}
                className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-cyan-600 px-4 py-2.5 text-sm font-bold text-white shadow-sm transition hover:bg-cyan-700 disabled:cursor-not-allowed disabled:bg-cyan-400"
                data-testid="flow-run-button"
              >
                {isRunning
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <Play className="h-4 w-4" />}
                {isRunning ? t('common.running') : assignedTask ? t('flowEditor.runFlowWithTask') : t('flowEditor.runFlow')}
              </button>

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
                {tasks.length === 0 ? (
                  <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-center text-sm text-slate-400">
                    {t('flowEditor.noTasksYetCreateOne')}
                  </div>
                ) : (
                  <div className="space-y-2 overflow-y-auto pr-1">
                    {tasks.map((task) => {
                      const isAttachedTask = String(task.id) === String(taskId);
                      return (
                        <button
                          key={task.id}
                          type="button"
                          onClick={() => setTaskId(String(task.id))}
                          className={`w-full rounded-2xl border px-4 py-3 text-left transition ${
                            isAttachedTask
                              ? 'border-cyan-300 bg-cyan-50'
                              : 'border-slate-200 bg-slate-50 hover:border-cyan-300 hover:bg-cyan-50'
                          }`}
                        >
                          <div className="text-sm font-semibold text-slate-800">{task.title}</div>
                          {task.description && (
                            <div className="mt-0.5 truncate text-xs text-slate-400">{task.description}</div>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className={`min-h-0 flex-1 overflow-y-auto p-3 ${leftTab === 'history' ? '' : 'hidden'}`}>
            <FlowHistory
              runs={runApi.displayRuns}
              instances={instances}
              onResume={runApi.handleResumeRun}
              resumingRun={resumingRun}
              selectedRunGroup={selectedRunGroup}
              onSelect={(run) => {
                const group = run.run_group;
                const isReselect = selectedRunGroup === group;
                setSelectedRunGroup(isReselect ? null : group);
                setParams(isReselect ? {} : { run: group }, { replace: true });
                setSelectedNodeId(null);
                setRightTab('logs');
                setCenterTab('chat');
                if (run.kind === 'chat' && !isReselect) {
                  // Resume this conversation in the live chat so it can continue.
                  setResumedChat({
                    conversationId: run.conversation_id || run.run_group,
                    messages: reconstructRunMessages(run),
                    key: run.run_group,
                  });
                } else {
                  // Deselecting, or opening a read-only task-run record: clear the
                  // resumed conversation so the chat returns to an empty new chat.
                  setResumedChat(null);
                }
              }}
            />
          </div>
        </div>

        {/* Center column — live canvas and chat */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <div className="flex shrink-0 items-center border-b border-gray-200 px-4 py-2">
            <div className="inline-flex items-center gap-1 rounded-lg bg-gray-100 p-0.5">
              <PaneTab
                active={centerTab === 'canvas'} onClick={() => setCenterTab('canvas')}
                icon={Workflow} label={t('flowEditor.canvas')}
              />
              <PaneTab
                active={centerTab === 'chat'} onClick={() => setCenterTab('chat')}
                icon={MessageSquare} label={t('flowEditor.chat')}
              />
            </div>
          </div>

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
              setSelectedNodeId={setSelectedNodeId}
            />
          </div>

          {/* Chat — always mounted (thread survives tab switches). On the Canvas
              tab it's a bottom strip; on the Chat tab it fills the pane. A chat
              History record resumes in the live chat; a task-run record shows a
              read-only transcript instead. */}
          <div
            className={`flex min-h-0 min-w-0 flex-col overflow-hidden ${
              centerTab === 'chat' ? 'flex-1' : 'flex-1 basis-1/2 border-t border-slate-200'
            }`}
          >
            {runApi.selectedRun && runApi.selectedRun.kind !== 'chat' ? (
              <FlowRunMessages
                run={runApi.selectedRun}
                messages={runApi.runMessages}
                onClose={() => setSelectedRunGroup(null)}
                workspace={chatWorkspace}
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
                onStreamEvent={runApi.handleStreamEvent}
                onActivity={runApi.handleChatActivity}
              />
            )}
          </div>
        </div>

        {/* Right column — nodes and their state, execution log */}
        <div className="flex w-[340px] shrink-0 flex-col overflow-hidden border-l border-slate-200 bg-white">
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
                <NodeRunCard
                  node={selectedNode}
                  agentLabels={agentLabels}
                  onRun={() => runApi.handleRunNode(selectedNode.id)}
                  running={runNodeId === selectedNode.id}
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
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${STATUS_STYLES[row.status] || STATUS_STYLES.ready}`}>
                        {t(`flowEditor.status.${row.status}`)}
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
                <LiveRunStream sessionId={runApi.liveSessionId} title={t('flowEditor.liveNodeOutput')} className="mb-3" />
                <FlowLog
                  logs={runApi.visibleLogs}
                  stateLogs={runApi.baseLogs}
                  filterLabel={
                    runApi.selectedRun && selectedNode
                      ? `${runApi.selectedRun.title || t('flowEditor.selectedRun')} · ${selectedNode.data.label}`
                      : runApi.selectedRun
                      ? (runApi.selectedRun.title || t('flowEditor.selectedRun'))
                      : selectedNode?.data.label
                  }
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
  );
}

export default FlowRun;
