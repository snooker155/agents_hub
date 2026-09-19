import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { CheckCircle2, ChevronDown, ChevronUp, Circle, ClipboardList, Download, FileText, History, Loader2, MessageSquare, Play, Plus, Save, Square, SquareTerminal, Workflow, XCircle, Factory } from 'lucide-react';
import { exportFlow, getAgents, getFlow, getFlowLogs, getFlowRuns, getTasks, runFlow, stopFlow, runFlowNode, updateFlow, listFlowEntities } from '../api';
import LiveRunStream from '../components/LiveRunStream';
import { useWorkspace } from '../components/workspace';
import { useTheme } from '../components/theme';
import { useStream } from '../components/stream';
import CreateTaskModal from '../components/CreateTaskModal';
import { applyEdgeChanges, applyNodeChanges } from 'reactflow';
import FlowCanvas, { EntityPalette } from '../components/flow/FlowCanvas';
import FlowChat from '../components/flow/FlowChat';

import { AppBar } from '../components/PageLayout';
import { useI18n } from '../i18n';
const DOMAIN_COLORS = {
  management: '#22d3ee',
  analysis: '#fbbf24',
  design: '#a78bfa',
  development: '#34d399',
  testing: '#fb7185',
  operations: '#fb923c',
  flow: '#94a3b8',
  general: '#94a3b8',
};

function normalizeNode(node, onRunNode) {
  const d = node.data || {};
  return {
    id: node.id,
    type: 'flowNode',
    position: node.position || { x: 100, y: 100 },
    style: { width: 90, ...(node.style || {}) },
    data: {
      node_id: node.id,
      label: d.label || node.label || 'Flow Agent',
      description: d.description || node.description || '',
      agent_id: d.agent_id || node.agent_id || '',
      // Non-agent entity fields (processor/condition/transform). entity_id
      // identifies the registry entity; category drives inspector rendering.
      entity_id: d.entity_id || node.entity_id || '',
      category: d.category || node.category || (d.agent_id || node.agent_id ? 'agent' : ''),
      input: d.input || node.input || [],
      output: d.output || node.output || [],
      config: d.config || node.config || {},
      domain: d.domain || node.domain || 'general',
      nodeTask: d.nodeTask || node.nodeTask || '',
      onRunNode,
    },
  };
}

function serializeNode(node) {
  const d = node.data || {};
  const out = {
    id: node.id,
    position: node.position,
    type: node.type,
    style: { width: 90 },
    data: {
      label: d.label || '',
      description: d.description || '',
      agent_id: d.agent_id || '',
      domain: d.domain || 'general',
      nodeTask: d.nodeTask || '',
    },
  };
  // Persist entity fields only when present, keeping plain agent nodes clean.
  if (d.entity_id) out.data.entity_id = d.entity_id;
  if (d.category) out.data.category = d.category;
  if (Array.isArray(d.input) && d.input.length) out.data.input = d.input;
  if (Array.isArray(d.output) && d.output.length) out.data.output = d.output;
  if (d.config && Object.keys(d.config).length) out.data.config = d.config;
  return out;
}

function serializeEdge(edge) {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: edge.type,
  };
}

const INPUT_CLS =
  'w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white';

// Parse / format a comma-separated list of state keys.
const parseKeys = (s) => s.split(',').map((x) => x.trim()).filter(Boolean);
const fmtKeys = (arr) => (Array.isArray(arr) ? arr.join(', ') : '');

// Per-node lifecycle event types. Only these drive a node's running/done status;
// other node-tagged events (notably `flow_state`, a post-node state snapshot the
// chat driver emits with the finished node's id) must NOT mask the real terminal
// event — otherwise a completed node reverts to "pending" the moment its
// flow_state snapshot lands. The printed log stream already filters flow_state.
const NODE_LIFECYCLE_TYPES = new Set([
  'agent_start', 'agent_finish', 'agent_error', 'agent_stopped', 'node_skip',
]);

// Node inspector: edits label/description/task for any node, plus the state
// contract (input/output keys) and config JSON for non-agent entity nodes.
function NodeInspector({ node, isAgent, onPatch, onRun, running }) {
  const { t } = useI18n();
  const d = node.data || {};
  const ident = isAgent ? d.agent_id : d.entity_id;
  const category = d.category || (isAgent ? 'agent' : '');

  // Local text state for the config JSON so invalid intermediate input doesn't
  // wipe the stored object; committed to node data only when it parses.
  // Selecting another node remounts this panel (key={node.id} at the call site),
  // so the draft below starts from that node's config — no re-sync effect.
  const [configText, setConfigText] = useState(JSON.stringify(d.config || {}, null, 2));
  const [configErr, setConfigErr] = useState('');

  const commitConfig = (text) => {
    setConfigText(text);
    if (!text.trim()) {
      setConfigErr('');
      onPatch({ config: {} });
      return;
    }
    try {
      const parsed = JSON.parse(text);
      setConfigErr('');
      onPatch({ config: parsed });
    } catch {
      setConfigErr(t('flowEditor.invalidJson'));
    }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-[20px] border border-slate-200 bg-slate-50 p-4">
        <div className="flex items-center justify-between gap-2">
          <div className="text-sm font-bold text-slate-900">{d.label}</div>
          {category ? (
            <span className="shrink-0 rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-medium text-slate-600">
              {category}
            </span>
          ) : null}
        </div>
        <div className="mt-1 text-xs font-medium text-slate-400">{ident}</div>
        {d.description ? (
          <div className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-500">{d.description}</div>
        ) : null}
      </div>

      {/* State contract — read/write keys shared via flow state */}
      <div className="space-y-2">
        <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          {t('flowEditor.inputStateKeys')}
        </label>
        <input
          value={fmtKeys(d.input)}
          onChange={(e) => onPatch({ input: parseKeys(e.target.value) })}
          placeholder={t('flowEditor.commaSeparatedKeys')}
          className={INPUT_CLS}
        />
        <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          {t('flowEditor.outputStateKeys')}
        </label>
        <input
          value={fmtKeys(d.output)}
          onChange={(e) => onPatch({ output: parseKeys(e.target.value) })}
          placeholder={t('flowEditor.commaSeparatedKeys')}
          className={INPUT_CLS}
        />
      </div>

      {isAgent ? (
        <textarea
          value={d.nodeTask || ''}
          onChange={(e) => onPatch({ nodeTask: e.target.value })}
          rows={4}
          placeholder={t('flowEditor.optionalNodeSpecificTask')}
          className={INPUT_CLS}
        />
      ) : (
        <div className="space-y-1">
          <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Config (JSON)
          </label>
          <textarea
            value={configText}
            onChange={(e) => commitConfig(e.target.value)}
            rows={6}
            spellCheck={false}
            placeholder="{}"
            className={`${INPUT_CLS} font-mono text-xs ${configErr ? 'border-red-300' : ''}`}
          />
          {configErr ? <div className="px-1 text-[11px] text-red-500">{configErr}</div> : null}
        </div>
      )}

      <button
        onClick={onRun}
        disabled={running}
        className="inline-flex items-center gap-2 rounded-2xl border border-cyan-200 bg-cyan-50 px-4 py-2 text-sm font-semibold text-cyan-700 transition hover:bg-cyan-100 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <Play className="h-4 w-4" />
        {running ? t('flowEditor.runningNode') : t('flowEditor.runSelectedNode')}
      </button>
    </div>
  );
}

// Flow-level meta editor: entry_point, mutability, recordability, and the
// initial state-key defaults. Patches go to `flow` and mark the editor dirty.
function FlowSettings({ flow, nodes, onPatch }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const mutability = flow.mutability !== false; // default true

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between py-1 text-sm font-bold text-slate-900"
      >
        Flow settings
        {open ? <ChevronUp className="h-4 w-4 text-slate-400" /> : <ChevronDown className="h-4 w-4 text-slate-400" />}
      </button>
      {open ? (
        <div className="space-y-3 pt-2">
          <div>
            <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{t('flowEditor.entryPoint')}</label>
            <select
              value={flow.entry_point || ''}
              onChange={(e) => onPatch({ entry_point: e.target.value })}
              className={INPUT_CLS}
            >
              <option value="">{t('flowEditor.autoRootNodes')}</option>
              {nodes.map((n) => (
                <option key={n.id} value={n.id}>{n.data?.label || n.id}</option>
              ))}
            </select>
          </div>

          <label className="flex items-center gap-2 px-1 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={mutability}
              onChange={(e) => onPatch({ mutability: e.target.checked })}
            />
            {t('flowEditor.mutableState')}
            <span className="text-[11px] text-slate-400">
              {mutability ? t('flowEditor.keysOverwritable') : t('flowEditor.keysWriteOnce')}
            </span>
          </label>

          <div>
            <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{t('flowEditor.recordability')}</label>
            <select
              value={flow.recordability || 'full'}
              onChange={(e) => onPatch({ recordability: e.target.value })}
              className={INPUT_CLS}
            >
              <option value="full">{t('flowEditor.full')}</option>
              <option value="none">{t('flowEditor.none')}</option>
            </select>
          </div>
        </div>
      ) : null}
    </div>
  );
}

// Graph-tab state block: edits the flow's seed/initial state JSON and can
// auto-build the state structure by scanning every node's declared input/output
// keys, adding any that are missing with an empty-string default.
function GraphStateBlock({ flow, nodes, onPatch }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  // Keyed on flow.id at the call site, so opening another flow remounts this
  // block with that flow's state as the draft.
  const [stateText, setStateText] = useState(JSON.stringify(flow.state || {}, null, 2));
  const [stateErr, setStateErr] = useState('');

  const commitState = (text) => {
    setStateText(text);
    if (!text.trim()) { setStateErr(''); onPatch({ state: {} }); return; }
    try { onPatch({ state: JSON.parse(text) }); setStateErr(''); }
    catch { setStateErr(t('flowEditor.invalidJson')); }
  };

  // All state keys referenced by node contracts (input + output), de-duplicated.
  const contractKeys = useMemo(() => {
    const keys = new Set();
    for (const node of nodes) {
      const d = node.data || {};
      for (const k of d.input || []) if (k) keys.add(k);
      for (const k of d.output || []) if (k) keys.add(k);
    }
    return [...keys];
  }, [nodes]);

  const current = useMemo(() => {
    try { return JSON.parse(stateText || '{}'); } catch { return null; }
  }, [stateText]);

  const missingKeys = current ? contractKeys.filter((k) => !(k in current)) : [];

  const buildFromGraph = () => {
    const base = current && typeof current === 'object' ? current : {};
    const next = { ...base };
    for (const k of contractKeys) if (!(k in next)) next[k] = '';
    const text = JSON.stringify(next, null, 2);
    setStateText(text);
    setStateErr('');
    onPatch({ state: next });
  };

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between py-1 text-sm font-bold text-slate-900"
      >
        State
        {open ? <ChevronUp className="h-4 w-4 text-slate-400" /> : <ChevronDown className="h-4 w-4 text-slate-400" />}
      </button>
      {open ? (
        <div className="space-y-3 pt-2">
          <div className="flex items-center justify-between gap-2">
            <div className="text-[11px] text-slate-400">
              {contractKeys.length} key{contractKeys.length === 1 ? '' : 's'} used by nodes
              {missingKeys.length ? ` · ${missingKeys.length} missing` : ''}
            </div>
            <button
              type="button"
              onClick={buildFromGraph}
              disabled={!current || !contractKeys.length}
              className="inline-flex items-center gap-1.5 rounded-xl border border-cyan-200 bg-cyan-50 px-3 py-1.5 text-xs font-semibold text-cyan-700 transition hover:bg-cyan-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Workflow className="h-3.5 w-3.5" />
              {t('flowEditor.buildFromGraph')}
            </button>
          </div>
          <textarea
            value={stateText}
            onChange={(e) => commitState(e.target.value)}
            rows={6}
            spellCheck={false}
            placeholder="{}"
            className={`${INPUT_CLS} font-mono text-xs ${stateErr ? 'border-red-300' : ''}`}
          />
          {stateErr ? <div className="px-1 text-[11px] text-red-500">{stateErr}</div> : null}
          <div className="px-1 text-[11px] text-slate-400">
            {t('flowEditor.seedValuesHint')}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// One value card in the runtime-state block. `accent` lets the second section
// (unkeyed node outputs) read visually distinct from declared state keys.
function StateEntry({ label, value, accent = 'cyan' }) {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  const isEmpty = text === '' || text === '""' || value == null;
  const labelColor = accent === 'slate'
    ? 'text-slate-500 dark:text-slate-400'
    : 'text-cyan-700 dark:text-cyan-400';
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-800/60">
      <div className={`font-mono text-[10px] font-semibold ${labelColor}`}>{label}</div>
      <pre className={`mt-0.5 max-h-28 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-[1.5] ${isEmpty ? 'text-slate-400 dark:text-slate-500' : 'text-slate-700 dark:text-slate-300'}`}>
        {isEmpty ? '(pending)' : text}
      </pre>
    </div>
  );
}

// Logs-tab runtime state block: shows the live shared-state values produced as a
// run unfolds. Declared state keys ride on flow_start / per-node events emitted
// by the engine (`state` field) — we render the latest snapshot seen. Agent node
// outputs are NOT shared state: nodes that declare no output keys never write to
// it, so when a flow declares no state fields this block shows nothing.
function RuntimeStateBlock({ logs }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);

  // Latest snapshot wins: walk the stream and keep the most recent `state`.
  const snapshot = useMemo(() => {
    let latest = null;
    for (const log of logs) {
      if (log && log.state && typeof log.state === 'object') latest = log.state;
    }
    return latest;
  }, [logs]);

  const entries = snapshot ? Object.entries(snapshot) : [];
  if (!entries.length) return null;
  const total = entries.length;

  return (
    <div className="shrink-0 border-b border-slate-200 dark:border-slate-700">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500"
      >
        <span>{t('flowEditor.runtimeState')} · {t('flowEditor.valueCount', { count: total })}</span>
        {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
      </button>
      {open ? (
        <div className="max-h-[40vh] overflow-y-auto px-3 pb-2">
          <div className="space-y-1.5">
            {entries.map(([key, value]) => (
              <StateEntry key={`k-${key}`} label={key} value={value} />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function FlowEditor() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { flowId } = useParams();
  const { selectedWorkspace, liveUpdates } = useWorkspace();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
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
  const [availableAgents, setAvailableAgents] = useState([]);
  // Registry entities grouped by category, for the canvas palette + inspector.
  const [entitiesByCategory, setEntitiesByCategory] = useState({});
  const [tasks, setTasks] = useState([]);
  const [logs, setLogs] = useState([]);
  // Live execution events for the active chat run, fed directly from the chat
  // stream (FlowChat → onStreamEvent) and translated into the flow-log event
  // shape the editor's derivations already understand. This is what drives the
  // canvas highlight, node statuses, and Logs panel live during a chat run —
  // with no per-event refetch of /logs or /runs. REST stays the source of truth
  // for the History list, manual flow runs, and replay of a selected record.
  const [streamLogs, setStreamLogs] = useState([]);
  const [runs, setRuns] = useState([]);
  const [selectedRunGroup, setSelectedRunGroup] = useState(null); // History record being viewed
  const [dirty, setDirty] = useState(false);
  const [rightTab, setRightTab] = useState('graph'); // graph | logs
  const [leftTab, setLeftTab] = useState('tasks'); // tasks | history
  const [centerTab, setCenterTab] = useState('canvas'); // canvas | chat
  // A fresh, unique token per chat thread. Generated on mount and regenerated by
  // "New chat" so each thread gets its own conversation id. It must be unique
  // (not a 0-based counter): the conversation id derives from it deterministically
  // and resetting to a counter on reload would collide with already-persisted
  // conversations (reusing their record + showing their old logs).
  const newChatNonce = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  const [chatNonce, setChatNonce] = useState(newChatNonce); // bumped to start a fresh chat thread
  // A resumed chat History record: sticky so continuing it keeps appending to
  // the same conversation even after the History selection is cleared (e.g. when
  // the new turn starts executing). { conversationId, messages, key } or null.
  const [resumedChat, setResumedChat] = useState(null);
  const [showCreateModal, setShowCreateModal] = useState(false);

  // The conversation id FlowChat uses for the active chat. It is also the
  // run_group the backend stamps on this chat's flow-log events, so we can
  // scope the live Logs view to just the current chat. A resumed record keeps
  // its own id so continued turns grow that same record.
  const activeConversationId = resumedChat
    ? resumedChat.conversationId
    : chatNonce
    ? `flow-chat-${flowId}-${chatNonce}`
    : `flow-chat-${flowId}`;

  // The slice of `logs` (which holds every conversation's events for this flow)
  // relevant to what's on screen right now. Everything live — canvas node
  // statuses, the "executing" flag, the Logs panel — derives from THIS, never the
  // global `logs` tail. Scoping to the active conversation is what makes "New
  // chat" reset the view: a fresh conversation id has no events here, instead of
  // inheriting the previous chat's trailing flow_start/agent_start lines.
  const scopedLogs = useMemo(() => {
    // A selected History record → only its events.
    if (selectedRunGroup) return logs.filter((l) => l.run_group === selectedRunGroup);
    // Manual Run / Run-node → the most recent live run_group's tail.
    if (running || flow?.running) {
      const liveGroup = logs[logs.length - 1]?.run_group;
      return liveGroup ? logs.filter((l) => l.run_group === liveGroup) : logs;
    }
    // Chat → live events streamed straight from the run (no refetch). streamLogs
    // is reset per conversation, so a fresh chat starts empty just like before.
    return streamLogs;
  }, [logs, streamLogs, selectedRunGroup, running, flow?.running]);

  const handleNewChat = () => {
    setChatNonce(newChatNonce());
    setCenterTab('chat');
    setResumedChat(null); // leave any resumed record; show the new (empty) chat
    setSelectedRunGroup(null);
    setSelectedNodeId(null);
  };

  const patchNodeHandlers = (items) => items.map((node) => normalizeNode(node, handleRunNode));

  const loadFlow = async () => {
    setLoading(true);
    try {
      const workspaceForAgents = selectedWorkspace || null;
      const [flowResponse, agentsResponse, entitiesResponse] = await Promise.all([
        getFlow(flowId), getAgents(workspaceForAgents), listFlowEntities(undefined, workspaceForAgents),
      ]);
      const nextFlow = flowResponse.data;
      setFlow(nextFlow);
      setNodes((nextFlow.nodes || []).map((node) => normalizeNode(node, handleRunNode)));
      setEdges((nextFlow.edges || []).map((edge) => ({
        ...edge,
        animated: false,
        style: { stroke: '#0891b2', strokeWidth: 2 },
      })));
      const EXCLUDED_AGENT_IDS = new Set(['orchestrator', 'agent_creator', 'flow-graph', 'flow-custom-graph', 'test-agent', 'research-remote', 'example-agent']);
      setAvailableAgents(
        (agentsResponse.data || []).filter((agent) => !EXCLUDED_AGENT_IDS.has(agent.id))
      );
      // Build the registry palette map, applying the same agent exclusions.
      const grouped = entitiesResponse.data || {};
      const filteredGrouped = {};
      for (const [cat, list] of Object.entries(grouped)) {
        const items = cat === 'agent'
          ? (list || []).filter((e) => !EXCLUDED_AGENT_IDS.has(e.id))
          : (list || []);
        if (items.length) filteredGrouped[cat] = items;
      }
      setEntitiesByCategory(filteredGrouped);
    } catch (error) {
      console.error('Failed to load flow', error);
      navigate('/flows');
    } finally {
      setLoading(false);
    }
  };

  const loadWorkspaceTasks = async (workspaceName) => {
    if (!workspaceName) return;
    try {
      const response = await getTasks(workspaceName);
      setTasks(response.data || []);
    } catch (error) {
      console.error('Failed to load tasks', error);
    }
  };

  const loadLogs = useCallback(async (workspaceName) => {
    if (!workspaceName) return;
    try {
      const response = await getFlowLogs(flowId, workspaceName);
      setLogs(response.data || []);
    } catch (error) {
      console.error('Failed to load logs', error);
    }
  }, [flowId]);

  const loadRuns = useCallback(async (workspaceName) => {
    try {
      const response = await getFlowRuns(flowId, workspaceName || undefined);
      setRuns(response.data || []);
    } catch (error) {
      console.error('Failed to load runs', error);
    }
  }, [flowId]);

  // node_id → display label, captured from each turn's flow_meta so node_start /
  // node_done lines can show the agent name even though only flow_meta carries
  // the full node list.
  const nodeLabelsRef = useRef({});
  // True between a chat turn's flow_meta and its done, so the SSE flow_runs
  // listener can stand down while the stream is driving the view.
  const chatStreamingRef = useRef(false);

  // Refresh logs + runs once at the end of a chat turn so the History list and
  // the persisted record catch up. Live progress no longer comes from here (see
  // handleStreamEvent) — this is just the closing reconcile.
  const handleChatActivity = useCallback(() => {
    // Turn ended (normally, or via Stop/error where no `done` arrives): clear the
    // streaming guard so the SSE listener resumes, then reconcile once.
    chatStreamingRef.current = false;
    const ws = flow?.workspace || selectedWorkspace;
    if (!ws) return;
    loadLogs(ws);
    loadRuns(ws);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flow?.workspace, selectedWorkspace]);

  // Translate a raw chat-stream event into the flow-log event shape the editor's
  // derivations (isExecuting, activeNodeId, nodeStatuses, FlowLog) consume, and
  // append it to the live stream-log buffer. This is the heart of the
  // stream-driven live view: every status the user sees during a chat run comes
  // from here, not from polling the backend.
  const handleStreamEvent = useCallback((event) => {
    const t = event?.type;
    if (!t) return;
    const ts = new Date().toISOString();
    if (t === 'flow_meta') {
      const labels = {};
      for (const n of event.nodes || []) labels[n.node_id] = n.agent_label || n.agent_id;
      nodeLabelsRef.current = labels;
      chatStreamingRef.current = true;
      setStreamLogs((prev) => [...prev, { type: 'flow_start', timestamp: ts, content: t('flowEditor.flowStarted'), state: event.state, title: event.user_message }]);
    } else if (t === 'node_start') {
      const label = event.agent_label || nodeLabelsRef.current[event.node_id] || event.agent_id || 'Agent';
      setStreamLogs((prev) => [...prev, {
        type: 'agent_start', node_id: event.node_id, agent_id: event.agent_id,
        agent_name: label, tag: 'agent', content: `Running ${label}`, timestamp: ts,
      }]);
    } else if (t === 'node_done') {
      const label = event.agent_label || nodeLabelsRef.current[event.node_id] || event.agent_id || 'Agent';
      setStreamLogs((prev) => [...prev, {
        type: event.ok ? 'agent_finish' : 'agent_error', node_id: event.node_id,
        agent_id: event.agent_id, agent_name: label, tag: 'agent',
        content: event.ok ? `Completed ${label}` : `${label} failed`,
        output: event.response || event.error || '', timestamp: ts,
        // Post-node shared-state snapshot for the live Runtime State block.
        state: event.state,
      }]);
    } else if (t === 'node_skip') {
      setStreamLogs((prev) => [...prev, {
        type: 'node_skip', node_id: event.node_id,
        agent_name: nodeLabelsRef.current[event.node_id] || 'Node', content: 'Skipped', timestamp: ts,
      }]);
    } else if (t === 'done') {
      chatStreamingRef.current = false;
      setStreamLogs((prev) => [...prev, {
        type: event.ok === false ? 'flow_stopped' : 'flow_finish',
        content: event.ok === false ? t('flowEditor.flowStopped') : t('flowEditor.flowFinished'), timestamp: ts,
      }]);
    }
  }, []);

  // A fresh conversation (new chat / resumed record / flow change) starts with an
  // empty live buffer so it doesn't inherit the previous chat's execution trail.
  useEffect(() => {
    setStreamLogs([]);
    nodeLabelsRef.current = {};
    chatStreamingRef.current = false;
  }, [activeConversationId]);

  // Reloads on flow or workspace change only. loadFlow captures handleRunNode,
  // which is re-created every render, so depending on it would reload in a loop.
  useEffect(() => {
    loadFlow();
  }, [flowId, selectedWorkspace]); // eslint-disable-line react-hooks/exhaustive-deps

  const { on } = useStream();
  useEffect(() => {
    const workspaceName = flow?.workspace || selectedWorkspace;
    if (!workspaceName) return;
    loadWorkspaceTasks(workspaceName);
    loadLogs(workspaceName);
    loadRuns(workspaceName);
    if (!liveUpdates) return undefined;
    return on('app', async (ev) => {
      if (ev.type !== 'flow_runs.changed' && ev.type !== 'tasks.changed') return;
      // While a chat run is streaming, the live view is driven entirely by the
      // stream (handleStreamEvent); the backend emits a flow_runs.changed per log
      // event, so refetching here would be exactly the redundant churn we set out
      // to remove. Skip it — FlowChat's turn-end onActivity reconciles the
      // History list once the run finishes. Manual flow runs (flow.running) and
      // task changes still refetch normally.
      if (chatStreamingRef.current) return;
      loadLogs(workspaceName);
      loadRuns(workspaceName);
      if (flow?.running) {
        try {
          const res = await getFlow(flowId);
          setFlow((prev) => ({ ...prev, running: res.data.running }));
        } catch { /* keep the last known running state */ }
      }
    });
  }, [flowId, flow?.workspace, flow?.running, selectedWorkspace, liveUpdates, on, loadLogs, loadRuns]);

  useEffect(() => {
    if (!selectedNodeId) return;
    // When a History record is being viewed, keep the Logs tab open and let
    // `visibleLogs` scope the stream to this node (logs for this node within
    // this record) instead of jumping to the Node inspector. Outside of a
    // selected record, open the inspector so the node's edits are visible.
    setRightTab(selectedRunGroup ? 'logs' : 'graph');
  }, [selectedNodeId]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) || null,
    [nodes, selectedNodeId]
  );

  const agentLabels = useMemo(() => {
    const map = {};
    for (const agent of availableAgents) map[agent.id] = agent.name;
    for (const node of nodes) {
      if (node.data?.agent_id) map[node.data.agent_id] = node.data.label || map[node.data.agent_id];
    }
    return map;
  }, [availableAgents, nodes]);

  const chatWorkspace = flow?.workspace || selectedWorkspace;

  const assignedTask = useMemo(
    () => tasks.find((task) => String(task.id) === String(flow?.task_id)) || null,
    [tasks, flow?.task_id]
  );

  const activeNodeId = useMemo(() => {
    // Derive the running node from the log stream rather than flow.running, so
    // this works for both runFlow() runs and chat-driven runs (which don't flip
    // the flow's running flag). Scoped to the current conversation/run so a fresh
    // chat clears the highlight. If the flow has ended, nothing is active.
    if (scopedLogs.length) {
      const lastFlowEvent = [...scopedLogs].reverse().find(
        (l) => l.type === 'flow_finish' || l.type === 'flow_stopped'
      );
      const lastStart = [...scopedLogs].reverse().find((l) => l.type === 'flow_start');
      const ended = lastFlowEvent && (!lastStart || scopedLogs.indexOf(lastFlowEvent) > scopedLogs.indexOf(lastStart));
      if (ended) return null;
    }
    const lastType = {};
    for (const log of scopedLogs) {
      if (log.node_id && NODE_LIFECYCLE_TYPES.has(log.type)) lastType[log.node_id] = log.type;
    }
    return Object.entries(lastType).find(([, t]) => t === 'agent_start')?.[0] ?? null;
  }, [scopedLogs]);

  // True while a run is in progress — covers runFlow() (flow.running) and
  // chat-driven runs (latest flow-level log event is flow_start, not yet
  // finished/stopped). Used to drive the Logs auto-scroll.
  const isExecuting = useMemo(() => {
    if (running || flow?.running) return true;
    if (!scopedLogs.length) return false;
    for (let i = scopedLogs.length - 1; i >= 0; i -= 1) {
      const t = scopedLogs[i].type;
      if (t === 'flow_finish' || t === 'flow_stopped') return false;
      if (t === 'flow_start') return true;
    }
    return false;
  }, [running, flow?.running, scopedLogs]);

  // When a run starts (rising edge of isExecuting), open the Logs tab so the
  // user sees execution progress. Only fires on the transition, so it won't
  // fight the user if they switch back to Graph mid-run.
  const wasExecutingRef = useRef(false);
  useEffect(() => {
    if (isExecuting && !wasExecutingRef.current) {
      // A fresh run started — drop any historical selection so the live stream shows.
      setSelectedRunGroup(null);
      setRightTab('logs');
    }
    wasExecutingRef.current = isExecuting;
  }, [isExecuting]);

  // A live History record for the active chat run, built from the stream buffer
  // so the run shows up the moment it starts — with a status derived from the
  // latest boundary event (running → completed/stopped) — instead of only after
  // it finishes. The persisted record (loaded at turn end) takes over once it
  // exists, since it carries richer events for resume/replay.
  const liveChatRun = useMemo(() => {
    if (!streamLogs.length) return null;
    let status = 'running';
    for (let i = streamLogs.length - 1; i >= 0; i -= 1) {
      const t = streamLogs[i].type;
      if (t === 'flow_finish') { status = 'completed'; break; }
      if (t === 'flow_stopped') { status = 'stopped'; break; }
      if (t === 'flow_start') { status = 'running'; break; }
    }
    // Title the record by the conversation's first message (truncated), matching
    // the persisted record once it loads. Fall back to a generic label until the
    // first flow_start carries the message.
    const firstMsg = (streamLogs.find((l) => l.type === 'flow_start' && l.title)?.title || '').trim().replace(/\n/g, ' ');
    const title = firstMsg
      ? firstMsg.slice(0, 60) + (firstMsg.length > 60 ? '…' : '')
      : t('flowEditor.flowChat');
    return {
      run_group: activeConversationId,
      kind: 'chat',
      title,
      started_at: streamLogs[0]?.timestamp,
      status,
      events: streamLogs,
    };
  }, [streamLogs, t, activeConversationId]);

  const displayRuns = useMemo(() => {
    if (!liveChatRun) return runs;
    // Once the persisted record exists (post-reconcile), prefer it.
    if (runs.some((r) => r.run_group === liveChatRun.run_group)) return runs;
    return [liveChatRun, ...runs];
  }, [runs, liveChatRun]);

  const selectedRun = useMemo(
    () => displayRuns.find((r) => r.run_group === selectedRunGroup) || null,
    [displayRuns, selectedRunGroup]
  );

  // Session to stream live agent output from. Null while a history record is
  // open — that view is a replay, and mixing a live run into it would be a lie.
  // Falls back to the running record's session so a flow already in flight when
  // the page loaded still streams.
  const liveSessionId = useMemo(() => {
    if (selectedRun) return null;
    if (flowSessionId) return flowSessionId;
    if (!(running || flow?.running)) return null;
    return displayRuns.find((r) => r.status === 'running')?.session_id || null;
  }, [selectedRun, flowSessionId, running, flow?.running, displayRuns]);

  // The log stream the right column renders:
  //  - a selected History record → that record's own events
  //  - otherwise → the run-scoped slice (scopedLogs). For chat this is the live
  //    stream buffer, which PERSISTS after the run ends so the finished run's
  //    logs + node statuses stay on screen until "New chat" clears them. A fresh
  //    chat has an empty buffer, so agents still read as pending when idle.
  const baseLogs = useMemo(() => {
    if (selectedRun) return selectedRun.events || [];
    return scopedLogs;
  }, [selectedRun, scopedLogs]);

  // Reconstruct the conversation bubbles for a selected History record from its
  // flow-log events (see reconstructRunMessages).
  const runMessages = useMemo(() => {
    if (!selectedRun) return [];
    return reconstructRunMessages(selectedRun);
  }, [selectedRun]);

  const visibleLogs = useMemo(() => {
    if (!selectedNode) return baseLogs;
    return baseLogs.filter(
      (log) => log.node_id === selectedNode.id || log.agent_id === selectedNode.data.agent_id
    );
  }, [baseLogs, selectedNode]);

  // Per-node execution status derived from the latest log line for each node:
  //  - done:    finished, skipped, errored, or stopped
  //  - running: agent_start is the most recent event
  //  - pending: a run is in progress but this node hasn't started yet
  //  - ready:   no run in progress (fresh/idle chat) and no event for this node
  const nodeStatuses = useMemo(() => {
    const lastType = {};
    for (const log of baseLogs) {
      if (log.node_id && NODE_LIFECYCLE_TYPES.has(log.type)) lastType[log.node_id] = log.type;
    }
    // Whether the displayed run is still executing. Untouched nodes read as
    // "pending" mid-run (future steps) but "ready" on a fresh/idle chat.
    let runActive = running || flow?.running || false;
    if (!runActive) {
      for (let i = baseLogs.length - 1; i >= 0; i -= 1) {
        const t = baseLogs[i].type;
        if (t === 'flow_finish' || t === 'flow_stopped') break;
        if (t === 'flow_start') { runActive = true; break; }
      }
    }
    const DONE = new Set(['agent_finish', 'node_skip', 'agent_error', 'agent_stopped']);
    return nodes.map((node) => {
      const t = lastType[node.id];
      let status = runActive ? 'pending' : 'ready';
      if (t === 'agent_start') status = 'running';
      else if (DONE.has(t)) status = t === 'agent_error' ? 'error' : 'done';
      return {
        id: node.id,
        label: node.data?.label || node.data?.agent_id || 'Agent',
        status,
      };
    });
  }, [baseLogs, nodes, running, flow?.running]);

  const persistFlow = async (override = {}) => {
    if (!flow) return null;
    setSaving(true);
    try {
      const payload = {
        name: flow.name,
        description: flow.description,
        workspace: flow.workspace || selectedWorkspace || null,
        task_id: flow.task_id || null,
        nodes: nodes.map(serializeNode),
        edges: edges.map(serializeEdge),
        // Flow-level meta (logic). Send only when set so we never clobber
        // existing values with undefined.
        ...(flow.entry_point !== undefined ? { entry_point: flow.entry_point } : {}),
        ...(flow.mutability !== undefined ? { mutability: flow.mutability } : {}),
        ...(flow.recordability !== undefined ? { recordability: flow.recordability } : {}),
        ...(flow.state !== undefined ? { state: flow.state } : {}),
        ...override,
      };
      const response = await updateFlow(flowId, payload);
      setFlow(response.data);
      setNodes((response.data.nodes || []).map((node) => normalizeNode(node, handleRunNode)));
      setEdges((response.data.edges || []).map((edge) => ({
        ...edge,
        animated: false,
        style: { stroke: '#0891b2', strokeWidth: 2 },
      })));
      setDirty(false);
      return response.data;
    } catch (error) {
      alert(`Failed to save flow: ${error.response?.data?.detail || error.message}`);
      return null;
    } finally {
      setSaving(false);
    }
  };

  const updateSelectedNode = (patch) => {
    if (!selectedNodeId) return;
    setNodes((current) =>
      current.map((node) =>
        node.id === selectedNodeId
          ? { ...node, data: { ...node.data, ...patch, onRunNode: handleRunNode } }
          : node
      )
    );
    setDirty(true);
  };

  const handleRunFlow = async () => {
    if (!flow) return;
    const workspaceName = flow.workspace || selectedWorkspace;
    if (!workspaceName) {
      alert(t('flowEditor.selectWorkspaceFirst'));
      return;
    }
    const saved = await persistFlow({ workspace: workspaceName });
    if (!saved) return;
    setRunning(true);
    try {
      const started = await runFlow(flowId, {
        workspace: workspaceName,
        description: flow.description || '',
        task_id: flow.task_id || undefined,
      });
      setFlowSessionId(started?.data?.session_id || null);
      setFlow((prev) => ({ ...prev, running: true }));
      await loadLogs(workspaceName);
      setRightTab('logs');
    } catch (error) {
      alert(`Failed to start flow: ${error.response?.data?.detail || error.message}`);
    } finally {
      setRunning(false);
    }
  };

  const handleExportFlow = async () => {
    if (!flow) return;
    // Export reads the persisted YAML, so flush any unsaved edits first.
    if (dirty) {
      const saved = await persistFlow();
      if (!saved) return;
    }
    try {
      const response = await exportFlow(flowId);
      const blob = new Blob([response.data], { type: 'application/x-yaml' });
      const url = URL.createObjectURL(blob);
      const slug = (flow.name || flowId).replace(/[^a-zA-Z0-9-_]+/g, '-').replace(/^-+|-+$/g, '') || 'flow';
      const link = document.createElement('a');
      link.href = url;
      link.download = `${slug}.yaml`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      alert(`Failed to export flow: ${error.response?.data?.detail || error.message}`);
    }
  };

  const handleStopFlow = async () => {
    if (!flow) return;
    setStopping(true);
    try {
      await stopFlow(flowId);
      const updated = await getFlow(flowId);
      setFlow(updated.data);
    } catch (error) {
      alert(`Failed to stop flow: ${error.response?.data?.detail || error.message}`);
    } finally {
      setStopping(false);
    }
  };

  async function handleRunNode(nodeId) {
    if (!flow) return;
    const workspaceName = flow.workspace || selectedWorkspace;
    if (!workspaceName) {
      alert(t('flowEditor.selectWorkspaceFirst'));
      return;
    }
    const saved = await persistFlow({ workspace: workspaceName });
    if (!saved) return;
    setRunNodeId(nodeId);
    setSelectedNodeId(nodeId);
    try {
      await runFlowNode(flowId, {
        node_id: nodeId,
        workspace: workspaceName,
        description: flow.description || '',
      });
      await loadLogs(workspaceName);
      setRightTab('logs');
    } catch (error) {
      alert(`Failed to run node: ${error.response?.data?.detail || error.message}`);
    } finally {
      setRunNodeId(null);
    }
  }

  // Called by the Create Task modal once a task is created. Attach the new task
  // to this flow (mirroring the old inline form) and refresh the task list.
  const handleTaskCreated = async (task) => {
    const workspaceName = flow?.workspace || selectedWorkspace;
    if (task?.id) {
      const nextDescription = flow?.description?.trim()
        ? flow.description
        : (task.description || '').trim();
      setFlow((current) => ({
        ...current,
        workspace: workspaceName || current.workspace,
        task_id: String(task.id),
        description: nextDescription,
      }));
      setDirty(true);
    }
    await loadWorkspaceTasks(workspaceName);
  };

  if (loading || !flow) {
    return (
      <div className="h-full flex items-center justify-center bg-white">
        <Loader2 className="h-6 w-6 animate-spin text-cyan-600" />
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <AppBar
        icon={Factory}
        title={flow.name}
        subtitle={flow.description}
        backTo="/flows"
        backLabel={t('flowEditor.flows')}
        actions={<>
          <button
            onClick={() => persistFlow()}
            className="flex items-center rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm font-semibold text-gray-700 shadow-sm transition-colors hover:bg-gray-100"
          >
            <Save className="mr-2 h-4 w-4 text-cyan-500" />
            {saving ? t('common.saving') : dirty ? t('flowEditor.saveFlow') : t('common.saved')}
          </button>
          <button
            onClick={handleExportFlow}
            className="flex items-center rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm font-semibold text-gray-700 shadow-sm transition-colors hover:bg-gray-100"
            title={t('flowEditor.exportThisFlowAsA')}
          >
            <Download className="mr-2 h-4 w-4 text-cyan-500" />
            {t('flowEditor.export')}
          </button>
          {(running || flow?.running) && (
            <button
              onClick={handleStopFlow}
              disabled={stopping}
              className="flex items-center rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-sm font-semibold text-rose-600 shadow-sm transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {stopping
                ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                : <Square className="mr-2 h-4 w-4" />}
              {stopping ? 'Stopping…' : 'Stop'}
            </button>
          )}
        </>}
      />

      <section className="flex min-h-0 flex-1 overflow-hidden bg-white">
        {/* Left column — tasks */}
        <div className="hidden w-[340px] shrink-0 flex-col overflow-hidden border-r border-slate-200 lg:flex">
          <div className="flex gap-2 border-b border-slate-200 px-3 pt-3">
            {[
              { id: 'tasks', label: 'Tasks', icon: ClipboardList },
              { id: 'history', label: 'History', icon: History },
            ].map((tab) => {
              const Icon = tab.icon;
              const isActive = leftTab === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setLeftTab(tab.id)}
                  className={`inline-flex flex-1 items-center justify-center gap-2 rounded-t-2xl px-4 py-2.5 text-sm font-semibold transition ${
                    isActive
                      ? 'bg-slate-900 text-white'
                      : 'bg-slate-50 text-slate-500 hover:bg-slate-100 hover:text-slate-900'
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  {tab.label}
                </button>
              );
            })}
          </div>
          <div className={`min-h-0 flex-1 overflow-y-auto p-3 ${leftTab === 'tasks' ? '' : 'hidden'}`}>
            <div className="space-y-5">
              <button
                type="button"
                onClick={handleNewChat}
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
                      onClick={() => {
                        setFlow((current) => ({ ...current, task_id: null }));
                        setDirty(true);
                      }}
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
                onClick={handleRunFlow}
                disabled={running || !!flow?.running}
                className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-cyan-600 px-4 py-2.5 text-sm font-bold text-white shadow-sm transition hover:bg-cyan-700 disabled:cursor-not-allowed disabled:bg-cyan-400"
              >
                {(running || flow?.running)
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <Play className="h-4 w-4" />}
                {(running || flow?.running) ? t('common.running') : assignedTask ? t('flowEditor.runFlowWithTask') : t('flowEditor.runFlow')}
              </button>

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
                    {tasks.map((t) => {
                      const isAttachedTask = String(t.id) === String(flow?.task_id);
                      return (
                        <button
                          key={t.id}
                          type="button"
                          onClick={() => {
                            const nextDescription = flow?.description?.trim() ? flow.description : t.description?.trim() || '';
                            setFlow((current) => ({
                              ...current,
                              task_id: String(t.id),
                              description: nextDescription,
                            }));
                            setDirty(true);
                          }}
                          className={`w-full rounded-2xl border px-4 py-3 text-left transition ${
                            isAttachedTask
                              ? 'border-cyan-300 bg-cyan-50'
                              : 'border-slate-200 bg-slate-50 hover:border-cyan-300 hover:bg-cyan-50'
                          }`}
                        >
                          <div className="text-sm font-semibold text-slate-800">{t.title}</div>
                          {t.description && (
                            <div className="mt-0.5 truncate text-xs text-slate-400">{t.description}</div>
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
              runs={displayRuns}
              selectedRunGroup={selectedRunGroup}
              onSelect={(run) => {
                const group = run.run_group;
                const isReselect = selectedRunGroup === group;
                setSelectedRunGroup(isReselect ? null : group);
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

        {/* Center column — switch between canvas and chat */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {/* Header — canvas / chat switch */}
          <div className="border-b border-slate-200 px-4 pt-3">
            <div className="flex gap-2">
              {[
                { id: 'canvas', label: 'Canvas', icon: Workflow },
                { id: 'chat', label: 'Chat', icon: MessageSquare },
              ].map((tab) => {
                const Icon = tab.icon;
                const isActive = centerTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setCenterTab(tab.id)}
                    className={`inline-flex items-center justify-center gap-2 rounded-t-2xl px-5 py-2.5 text-sm font-semibold transition ${
                      isActive
                        ? 'bg-slate-900 text-white'
                        : 'bg-slate-50 text-slate-500 hover:bg-slate-100 hover:text-slate-900'
                    }`}
                  >
                    <Icon className="h-4 w-4" />
                    {tab.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Canvas — kept mounted so ReactFlow viewport/state survives tab switches.
              Visible only on the Canvas tab; on the Chat tab the chat takes the full pane. */}
          <div className={`relative min-h-0 min-w-0 flex-1 basis-1/2 ${centerTab === 'canvas' ? 'block' : 'hidden'}`}>
            <FlowCanvas
              nodes={nodes}
              edges={edges}
              onNodesChange={(changes) => {
                setNodes((current) => applyNodeChanges(changes, current).map((node) => ({
                  ...node,
                  style: { width: 90, ...(node.style || {}) },
                  data: { ...node.data, onRunNode: handleRunNode },
                })));
                setDirty(true);
              }}
              onEdgesChange={(changes) => {
                setEdges((current) => applyEdgeChanges(changes, current));
                setDirty(true);
              }}
              activeNodeId={activeNodeId}
              onRunNode={handleRunNode}
              setEdges={(value) => {
                setEdges((current) => {
                  const next = typeof value === 'function' ? value(current) : value;
                  setDirty(true);
                  return next;
                });
              }}
              setNodes={(value) => {
                setNodes((current) => {
                  const next = typeof value === 'function' ? value(current) : value;
                  setDirty(true);
                  return patchNodeHandlers(next);
                });
              }}
              setSelectedNodeId={setSelectedNodeId}
            />
          </div>

          {/* Chat — always mounted (thread survives tab switches). On the Canvas tab it's a
              fixed bottom strip; on the Chat tab it grows to fill the whole pane.
              When a chat History record is selected it is resumed in the live chat
              (turns append to the same conversation); a task-run record shows a
              read-only transcript instead. */}
          <div
            className={`flex min-h-0 min-w-0 flex-col overflow-hidden ${
              centerTab === 'chat' ? 'flex-1' : 'flex-1 basis-1/2 border-t border-slate-200'
            }`}
          >
            {selectedRun && selectedRun.kind !== 'chat' ? (
              <FlowRunMessages
                run={selectedRun}
                messages={runMessages}
                onClose={() => setSelectedRunGroup(null)}
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
                onActivity={handleChatActivity}
              />
            )}
          </div>
        </div>

        {/* Right column — graph (nodes / palette) and execution log */}
        <div className="flex w-[340px] shrink-0 flex-col overflow-hidden border-l border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-4 pt-3">
            <div className="flex gap-2">
              {[
                { id: 'graph', label: selectedNode ? 'Node' : 'Graph', icon: SquareTerminal },
                { id: 'logs', label: 'Logs', icon: FileText },
              ].map((tab) => {
                const Icon = tab.icon;
                const isActive = rightTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => {
                      setRightTab(tab.id);
                      // Opening the Graph/Node tab brings the canvas into view so
                      // the selected node and its edits are visible together.
                      if (tab.id === 'graph') setCenterTab('canvas');
                    }}
                    className={`inline-flex flex-1 items-center justify-center gap-2 rounded-t-2xl px-4 py-2.5 text-sm font-semibold transition ${
                      isActive
                        ? 'bg-slate-900 text-white'
                        : 'bg-slate-50 text-slate-500 hover:bg-slate-100 hover:text-slate-900'
                    }`}
                  >
                    <Icon className="h-4 w-4" />
                    {tab.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div
            className={`flex-1 min-h-0 ${
              rightTab === 'logs'
                ? 'overflow-hidden'
                : rightTab === 'graph' && !selectedNode
                ? 'flex flex-col overflow-hidden'
                : 'overflow-y-auto p-3'
            }`}
          >
            {rightTab === 'graph' ? (
              selectedNode ? (
                <NodeInspector
                  key={selectedNode.id}
                  node={selectedNode}
                  isAgent={(selectedNode.data.category || 'agent') === 'agent'}
                  onPatch={updateSelectedNode}
                  onRun={() => handleRunNode(selectedNode.id)}
                  running={runNodeId === selectedNode.id}
                />
              ) : (
                <>
                  {/* Header bar — matches the chat panel header height. */}
                  <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-3 py-2.5">
                    <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-slate-100">
                      <SquareTerminal className="h-4 w-4 text-slate-600" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-bold text-slate-900">{t('flowEditor.graphOverview')}</div>
                      <div className="truncate text-[11px] text-slate-400">
                        {nodes.length} nodes · {edges.length} connections
                      </div>
                    </div>
                  </div>
                  {/* Fixed graph settings — stay put while the registry list below scrolls. */}
                  <div className="shrink-0 p-3">
                    <GraphStateBlock
                      key={flow.id}
                      flow={flow}
                      nodes={nodes}
                      onPatch={(patch) => { setFlow((prev) => ({ ...prev, ...patch })); setDirty(true); }}
                    />
                    <div className="-mx-3 my-3 border-t border-slate-200" />
                    <FlowSettings
                      flow={flow}
                      nodes={nodes}
                      onPatch={(patch) => { setFlow((prev) => ({ ...prev, ...patch })); setDirty(true); }}
                    />
                  </div>
                  {/* Separator between settings and the node registry. */}
                  <div className="shrink-0 border-t border-slate-200" />
                  {/* Scrollable registry list. */}
                  <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
                    <EntityPalette entitiesByCategory={entitiesByCategory} />
                    <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
                      Select a node on the canvas to edit its contract (inputs/outputs/config), node-specific task, and run it.
                    </div>
                  </div>
                </>
              )
            ) : null}

            {rightTab === 'logs' ? (
              <>
              <LiveRunStream sessionId={liveSessionId} title={t('flowEditor.liveNodeOutput')} className="mb-3" />
              <FlowLog
                logs={visibleLogs}
                stateLogs={baseLogs}
                filterLabel={
                  selectedRun && selectedNode
                    ? `${selectedRun.title || t('flowEditor.selectedRun')} · ${selectedNode.data.label}`
                    : selectedRun
                    ? (selectedRun.title || t('flowEditor.selectedRun'))
                    : selectedNode?.data.label
                }
                running={isExecuting && !selectedRun}
                nodeStatuses={nodeStatuses}
              />
              </>
            ) : null}
          </div>
        </div>
      </section>

      {showCreateModal && (
        <CreateTaskModal
          selectedWorkspace={chatWorkspace}
          onClose={() => setShowCreateModal(false)}
          onCreated={handleTaskCreated}
        />
      )}
    </div>
  );
}

function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

function fmtDateTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString([], {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

const RUN_STATUS_STYLES = {
  completed: { dot: 'bg-emerald-500', text: 'text-emerald-600', label: 'Completed' },
  stopped: { dot: 'bg-rose-500', text: 'text-rose-600', label: 'Stopped' },
  running: { dot: 'bg-amber-500 animate-pulse', text: 'text-amber-600', label: 'Running' },
};

// Reconstruct conversation bubbles from a History record's flow-log events.
// Each flow_start begins a turn; the first node's input holds the user message,
// and every node's terminal event (finish/error/stopped) becomes an agent bubble
// carrying that node's output. Bubble shape matches FlowChat's message objects so
// a chat record can be resumed there.
function reconstructRunMessages(run) {
  const events = run?.events || [];
  const bubbles = [];
  let turnHasUser = false;
  const extractUserMessage = (input) => {
    const text = String(input || '');
    const marker = 'Latest user message:';
    const idx = text.lastIndexOf(marker);
    const tail = idx >= 0 ? text.slice(idx + marker.length) : text;
    return tail.split('\n=== Attached files ===')[0].trim();
  };
  for (const ev of events) {
    if (ev.type === 'flow_start') {
      turnHasUser = false;
    } else if (ev.type === 'agent_start') {
      if (!turnHasUser) {
        const userText = extractUserMessage(ev.input);
        if (userText) {
          bubbles.push({ id: `${ev.timestamp}-u`, role: 'user', content: userText });
        }
        turnHasUser = true;
      }
    } else if (ev.type === 'agent_finish' || ev.type === 'agent_error' || ev.type === 'agent_stopped') {
      bubbles.push({
        id: `${ev.timestamp}-${ev.node_id || 'a'}`,
        role: 'agent',
        agent_label: ev.agent_name || ev.agent_id || 'Agent',
        content: ev.output || ev.content || '',
        error: ev.type !== 'agent_finish',
      });
    }
  }
  return bubbles;
}

function FlowRunMessages({ run, messages = [], onClose }) {
  const { t } = useI18n();
  const isChat = run?.kind === 'chat';
  return (
    <div className="flex h-full w-full min-w-0 flex-col overflow-hidden bg-slate-50">
      <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-3 py-2.5">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-slate-100">
          {isChat ? <MessageSquare className="h-4 w-4 text-indigo-600" /> : <Workflow className="h-4 w-4 text-emerald-600" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-bold text-slate-900">
            {run?.title || (isChat ? t('flowEditor.flowChat') : t('flowEditor.flowRun'))}
          </div>
          <div className="truncate text-[11px] text-slate-400">
            {isChat ? t('flowEditor.chat') : t('flowEditor.taskRun')} · {fmtDateTime(run?.started_at)} · {t('flowEditor.readOnlyHistory')}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg px-2.5 py-1 text-xs font-semibold text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
        >
          {t('flowEditor.close')}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3">
        {messages.length === 0 ? (
          <div className="flex h-full min-h-[200px] flex-col items-center justify-center text-center text-sm text-slate-500">
            {t('flowEditor.thisRunProducedNoMessages')}
          </div>
        ) : (
          messages.map((msg) => {
            const isUser = msg.role === 'user';
            return (
              <div key={msg.id} className={`mb-4 flex gap-2.5 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
                <div className="flex shrink-0 flex-col items-center gap-1">
                  <div
                    className={`flex h-7 w-7 items-center justify-center rounded-full text-white ${
                      isUser ? 'bg-indigo-600' : 'bg-gray-800'
                    }`}
                  >
                    {isUser ? <SquareTerminal className="h-3.5 w-3.5" /> : <Workflow className="h-3.5 w-3.5" />}
                  </div>
                  {!isUser && msg.agent_label && (
                    <span className="max-w-[52px] break-words text-center text-[9px] font-medium leading-tight text-gray-400">
                      {msg.agent_label}
                    </span>
                  )}
                </div>
                <div
                  className={`max-w-[78%] whitespace-pre-wrap text-sm leading-relaxed ${
                    isUser
                      ? 'rounded-2xl rounded-tr-sm bg-indigo-600 px-3.5 py-2.5 text-white'
                      : 'rounded-2xl rounded-tl-sm border border-gray-200 bg-white px-3.5 py-2.5 text-gray-800 shadow-sm'
                  } ${msg.error ? 'border-red-300 bg-red-50 text-red-700' : ''}`}
                >
                  {msg.content || (msg.error ? '(no output)' : '')}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function FlowHistory({ runs = [], selectedRunGroup, onSelect }) {
  const { t } = useI18n();
  if (!runs.length) {
    return (
      <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
        {t('flowEditor.noPreviousRunsYetRun')}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {runs.map((run) => {
        const isActive = run.run_group === selectedRunGroup;
        const isChat = run.kind === 'chat';
        const status = RUN_STATUS_STYLES[run.status] || RUN_STATUS_STYLES.running;
        const nodeCount = (run.events || []).filter((e) => e.type === 'agent_start').length;
        return (
          <button
            key={run.run_group}
            type="button"
            onClick={() => onSelect(run)}
            className={`w-full rounded-2xl border px-4 py-3 text-left transition ${
              isActive
                ? 'border-cyan-300 bg-cyan-50'
                : 'border-slate-200 bg-slate-50 hover:border-cyan-300 hover:bg-cyan-50'
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${
                  isChat ? 'bg-indigo-100 text-indigo-700' : 'bg-emerald-100 text-emerald-700'
                }`}
              >
                {isChat ? <MessageSquare className="h-3 w-3" /> : <Workflow className="h-3 w-3" />}
                {isChat ? t('flowEditor.chat') : t('flowEditor.taskRun')}
              </span>
              <span className={`inline-flex items-center gap-1 text-[11px] font-medium ${status.text}`}>
                <span className={`h-1.5 w-1.5 rounded-full ${status.dot}`} />
                {status.label}
              </span>
            </div>
            <div className="mt-1.5 truncate text-sm font-semibold text-slate-900">
              {run.title || (isChat ? t('flowEditor.flowChat') : t('flowEditor.flowRun'))}
            </div>
            <div className="mt-0.5 text-[11px] text-slate-400">
              {fmtDateTime(run.started_at)} · {t('flowEditor.stepCount', { count: nodeCount })}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function logMeta(log) {
  const t = log.type || '';
  if (t === 'flow_start')   return { symbol: '◆', color: '#22d3ee', label: 'Flow started' };
  if (t === 'flow_finish')  return { symbol: '◆', color: '#34d399', label: 'Flow finished' };
  if (t === 'flow_stopped') return { symbol: '◆', color: '#f87171', label: 'Flow stopped' };
  if (t === 'agent_start')  return { symbol: '⟳', color: '#fbbf24', label: log.agent_name || 'Agent', spinning: true };
  if (t === 'agent_finish') return { symbol: '✓', color: '#34d399', label: log.agent_name || 'Agent' };
  if (t === 'agent_error')  return { symbol: '✗', color: '#f87171', label: log.agent_name || 'Agent' };
  if (t === 'agent_stopped') return { symbol: '■', color: '#fb923c', label: log.agent_name || 'Agent' };
  if (t === 'node_skip')    return { symbol: '⊘', color: '#64748b', label: log.agent_name || 'Node' };
  return { symbol: '·', color: '#64748b', label: log.agent_name || t };
}

function TerminalBlock({ label, text, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  if (!text) return null;
  return (
    <div className="mt-1.5 ml-[72px]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 font-mono text-[10px] text-slate-400 hover:text-slate-700 dark:text-slate-500 dark:hover:text-slate-300 transition-colors"
      >
        <span>{open ? '▾' : '▸'}</span>
        <span>{label}</span>
      </button>
      {open && (
        <pre className="mt-1 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-200 bg-slate-100 p-3 font-mono text-[10px] leading-[1.6] text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">
          {text}
        </pre>
      )}
    </div>
  );
}

const DOMAIN_COLORS_DARK = DOMAIN_COLORS;
const DOMAIN_COLORS_LIGHT = {
  management: '#0891b2',
  analysis: '#d97706',
  design: '#7c3aed',
  development: '#059669',
  testing: '#e11d48',
  operations: '#ea580c',
  flow: '#475569',
  general: '#475569',
};

function LogLine({ log, isLast, isDark }) {
  const { t } = useI18n();
  const meta = logMeta(log);
  const palette = isDark ? DOMAIN_COLORS_DARK : DOMAIN_COLORS_LIGHT;
  const nameColor = palette[log.tag] || meta.color;
  const symbolColor = meta.color;

  return (
    <div className="group">
      <div className="flex items-baseline gap-0 font-mono text-[11px] leading-6">
        <span className="w-[52px] shrink-0 text-[10px] text-slate-400 dark:text-slate-500">{fmtTime(log.timestamp)}</span>
        <span className="relative flex w-5 shrink-0 flex-col items-center self-stretch">
          {!isLast && <span className="absolute top-5 bottom-0 left-1/2 w-px -translate-x-1/2 bg-slate-200 dark:bg-slate-700" />}
          <span
            className={`relative z-10 mt-1.5 text-[13px] leading-none${meta.spinning ? ' animate-spin' : ''}`}
            style={{ color: symbolColor }}
          >
            {meta.symbol}
          </span>
        </span>
        <span className="w-[80px] shrink-0 truncate pl-2 font-semibold" style={{ color: nameColor }}>
          {meta.label}
        </span>
        <span className="min-w-0 flex-1 pl-2 break-words text-slate-700 dark:text-slate-300">{log.content}</span>
      </div>
      <TerminalBlock label={t('flowEditor.input')}  text={log.input}  defaultOpen={false} />
      <TerminalBlock label={t('flowEditor.output')} text={log.output} defaultOpen={log.type === 'agent_finish'} />
    </div>
  );
}

function FlowAgentStatusBar({ nodeStatuses = [] }) {
  const { t } = useI18n();
  if (!nodeStatuses.length) return null;
  const STYLES = {
    done: {
      Icon: CheckCircle2, iconClass: 'text-emerald-500',
      row: 'border-emerald-200 bg-emerald-50 dark:border-emerald-900/50 dark:bg-emerald-900/20',
      label: 'text-slate-800 dark:text-slate-100', badge: 'text-emerald-600 dark:text-emerald-400', word: 'Done',
    },
    running: {
      Icon: Loader2, iconClass: 'text-amber-500 animate-spin',
      row: 'border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-900/30',
      label: 'text-amber-900 dark:text-amber-100', badge: 'text-amber-600 dark:text-amber-400', word: 'Processing',
    },
    error: {
      Icon: XCircle, iconClass: 'text-rose-500',
      row: 'border-rose-200 bg-rose-50 dark:border-rose-900/50 dark:bg-rose-900/20',
      label: 'text-rose-800 dark:text-rose-200', badge: 'text-rose-600 dark:text-rose-400', word: 'Error',
    },
    pending: {
      Icon: Circle, iconClass: 'text-slate-300 dark:text-slate-600',
      row: 'border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800/60',
      label: 'text-slate-400 dark:text-slate-500', badge: 'text-slate-400 dark:text-slate-500', word: 'Pending',
    },
    ready: {
      Icon: Circle, iconClass: 'text-cyan-400 dark:text-cyan-500',
      row: 'border-cyan-200 bg-cyan-50/50 dark:border-cyan-900/50 dark:bg-cyan-900/10',
      label: 'text-slate-600 dark:text-slate-300', badge: 'text-cyan-600 dark:text-cyan-400', word: 'Ready',
    },
  };
  return (
    <div className="max-h-[45%] shrink-0 overflow-y-auto border-b border-slate-200 p-2 dark:border-slate-700">
      <div className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
        {t('flowEditor.agents')}
      </div>
      <div className="space-y-1">
        {nodeStatuses.map((node, i) => {
          const s = STYLES[node.status] || STYLES.pending;
          const Icon = s.Icon;
          return (
            <div
              key={node.id}
              className={`flex items-center gap-2.5 rounded-xl border px-3 py-2 ${s.row}`}
            >
              <span className="w-4 shrink-0 text-center text-[11px] font-mono text-slate-400 dark:text-slate-500">{i + 1}</span>
              <Icon className={`h-4 w-4 shrink-0 ${s.iconClass}`} />
              <span className={`min-w-0 flex-1 truncate text-sm font-semibold ${s.label}`}>{node.label}</span>
              <span className={`shrink-0 text-[11px] font-medium ${s.badge}`}>{s.word}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FlowLog({ logs, stateLogs, filterLabel, running, nodeStatuses }) {
  const { t } = useI18n();
  const bottomRef = useRef(null);
  const didInitialScrollRef = useRef(false);
  const { theme } = useTheme();
  const isDark = theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);

  // `flow_state` events only carry a state snapshot for the runtime-state block;
  // they have no log content, so keep them out of the printed log stream.
  const visible = useMemo(() => logs.filter((l) => l.type !== 'flow_state'), [logs]);

  // On mount (or when logs first appear), jump straight to the end with no animation.
  useEffect(() => {
    if (didInitialScrollRef.current) return;
    if (logs.length === 0) return;
    bottomRef.current?.scrollIntoView({ behavior: 'instant', block: 'end' });
    didInitialScrollRef.current = true;
  }, [logs.length]);

  // While the flow is executing, follow new log lines smoothly. When it's not
  // running we leave the scroll position alone so users can read past output.
  useEffect(() => {
    if (!running) return;
    if (!didInitialScrollRef.current) return;
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [logs.length, running]);

  return (
    <div className="flex h-full flex-col bg-slate-50 dark:bg-slate-900">
      <FlowAgentStatusBar nodeStatuses={nodeStatuses} />
      <RuntimeStateBlock logs={stateLogs || logs} />
      {filterLabel && (
        <div className="border-b border-slate-200 px-3 py-1.5 font-mono text-[10px] text-slate-400 dark:border-slate-700 dark:text-slate-500">
          # filtered · {filterLabel}
        </div>
      )}
      <div className="flex-1 overflow-y-auto px-3 py-3">
        {visible.length === 0 ? (
          <span className="font-mono text-[11px] text-slate-400 dark:text-slate-500">{t('flowEditor.waitingForExecution')}</span>
        ) : (
          <div className="space-y-0.5">
            {visible.map((log, i) => (
              <LogLine key={`${log.timestamp}-${i}`} log={log} isLast={i === visible.length - 1} isDark={isDark} />
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

export default FlowEditor;
