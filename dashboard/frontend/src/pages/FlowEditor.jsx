import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, ChevronDown, ChevronUp, ClipboardList, FileText, Loader2, Play, Save, Square, SquareTerminal } from 'lucide-react';
import { createTask, getAgents, getFlow, getFlowLogs, getTasks, runFlow, stopFlow, runFlowNode, updateFlow } from '../api';
import { useWorkspace } from '../components/WorkspaceContext';
import { useTheme } from '../components/ThemeContext';
import FlowCanvas, { applyEdgeChanges, applyNodeChanges } from '../components/flow/FlowCanvas';

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
  return {
    id: node.id,
    type: 'flowNode',
    position: node.position || { x: 100, y: 100 },
    style: { width: 90, ...(node.style || {}) },
    data: {
      node_id: node.id,
      label: node.data?.label || node.label || 'Flow Agent',
      description: node.data?.description || node.description || '',
      agent_id: node.data?.agent_id || node.agent_id || '',
      domain: node.data?.domain || node.domain || 'general',
      nodeTask: node.data?.nodeTask || node.nodeTask || '',
      onRunNode,
    },
  };
}

function serializeNode(node) {
  return {
    id: node.id,
    position: node.position,
    type: node.type,
    style: { width: 90 },
    data: {
      label: node.data?.label || '',
      description: node.data?.description || '',
      agent_id: node.data?.agent_id || '',
      domain: node.data?.domain || 'general',
      nodeTask: node.data?.nodeTask || '',
    },
  };
}

function serializeEdge(edge) {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: edge.type,
  };
}

function FlowEditor() {
  const navigate = useNavigate();
  const { flowId } = useParams();
  const { selectedWorkspace, liveUpdates } = useWorkspace();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [runNodeId, setRunNodeId] = useState(null);
  const [flow, setFlow] = useState(null);
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [availableAgents, setAvailableAgents] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [logs, setLogs] = useState([]);
  const [dirty, setDirty] = useState(false);
  const [activeTab, setActiveTab] = useState('graph');
  const [quickTask, setQuickTask] = useState({ title: '', description: '' });

  const patchNodeHandlers = (items) => items.map((node) => normalizeNode(node, handleRunNode));

  const loadFlow = async () => {
    setLoading(true);
    try {
      const workspaceForAgents = selectedWorkspace || null;
      const [flowResponse, agentsResponse] = await Promise.all([getFlow(flowId), getAgents(workspaceForAgents)]);
      const nextFlow = flowResponse.data;
      setFlow(nextFlow);
      setNodes((nextFlow.nodes || []).map((node) => normalizeNode(node, handleRunNode)));
      setEdges((nextFlow.edges || []).map((edge) => ({
        ...edge,
        animated: false,
        style: { stroke: '#0891b2', strokeWidth: 2 },
      })));
      const EXCLUDED_AGENT_IDS = new Set(['orchestrator', 'agent_flows', 'flow-graph', 'flow-custom-graph', 'test-agent', 'research-remote', 'example-agent']);
      setAvailableAgents(
        (agentsResponse.data || []).filter((agent) => !EXCLUDED_AGENT_IDS.has(agent.id))
      );
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

  const loadLogs = async (workspaceName) => {
    if (!workspaceName) return;
    try {
      const response = await getFlowLogs(flowId, workspaceName);
      setLogs(response.data || []);
    } catch (error) {
      console.error('Failed to load logs', error);
    }
  };

  useEffect(() => {
    loadFlow();
  }, [flowId, selectedWorkspace]);

  useEffect(() => {
    const workspaceName = flow?.workspace || selectedWorkspace;
    if (!workspaceName) return;
    loadWorkspaceTasks(workspaceName);
    loadLogs(workspaceName);
    if (!liveUpdates) return;
    const interval = setInterval(async () => {
      loadLogs(workspaceName);
      if (flow?.running) {
        try {
          const res = await getFlow(flowId);
          setFlow((prev) => ({ ...prev, running: res.data.running }));
        } catch {}
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [flowId, flow?.workspace, flow?.running, selectedWorkspace, liveUpdates]);

  useEffect(() => {
    if (selectedNodeId) {
      setActiveTab('graph');
    }
  }, [selectedNodeId]);

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) || null,
    [nodes, selectedNodeId]
  );

  const assignedTask = useMemo(
    () => tasks.find((task) => String(task.id) === String(flow?.task_id)) || null,
    [tasks, flow?.task_id]
  );

  const activeNodeId = useMemo(() => {
    if (!flow?.running) return null;
    const lastType = {};
    for (const log of logs) {
      if (log.node_id) lastType[log.node_id] = log.type;
    }
    return Object.entries(lastType).find(([, t]) => t === 'agent_start')?.[0] ?? null;
  }, [logs, flow?.running]);

  const visibleLogs = useMemo(() => {
    if (!selectedNode) return logs;
    return logs.filter(
      (log) => log.node_id === selectedNode.id || log.agent_id === selectedNode.data.agent_id
    );
  }, [logs, selectedNode]);

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
      alert('Select a workspace for this flow first.');
      return;
    }
    const saved = await persistFlow({ workspace: workspaceName });
    if (!saved) return;
    setRunning(true);
    try {
      await runFlow(flowId, {
        workspace: workspaceName,
        description: flow.description || '',
        task_id: flow.task_id || undefined,
      });
      setFlow((prev) => ({ ...prev, running: true }));
      await loadLogs(workspaceName);
      setActiveTab('logs');
    } catch (error) {
      alert(`Failed to start flow: ${error.response?.data?.detail || error.message}`);
    } finally {
      setRunning(false);
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
      alert('Select a workspace for this flow first.');
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
      setActiveTab('logs');
    } catch (error) {
      alert(`Failed to run node: ${error.response?.data?.detail || error.message}`);
    } finally {
      setRunNodeId(null);
    }
  }

  const handleCreateTask = async (event) => {
    event.preventDefault();
    const workspaceName = flow?.workspace || selectedWorkspace;
    if (!workspaceName) {
      alert('Select a workspace first.');
      return;
    }
    if (!quickTask.title.trim()) return;

    try {
      const response = await createTask({
        title: quickTask.title.trim(),
        description: quickTask.description.trim(),
        workspace: workspaceName,
      });
      const taskId = String(response.data.id);
      const nextDescription = flow?.description?.trim() ? flow.description : quickTask.description.trim();
      setFlow((current) => ({
        ...current,
        workspace: workspaceName,
        task_id: taskId,
        description: nextDescription,
      }));
      setQuickTask({ title: '', description: '' });
      await loadWorkspaceTasks(workspaceName);
      setDirty(true);
      setActiveTab('tasks');
    } catch (error) {
      alert(`Failed to create task: ${error.response?.data?.detail || error.message}`);
    }
  };

  if (loading || !flow) {
    return (
      <div className="flex min-h-[640px] items-center justify-center rounded-[28px] border border-slate-200 bg-white">
        <Loader2 className="h-6 w-6 animate-spin text-cyan-600" />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-6">
      <div className="flex shrink-0 flex-col gap-4 rounded-xl border border-gray-100 bg-white p-6 shadow-sm md:flex-row md:items-center">
        <div className="flex-1">
          <Link
            to="/flows"
            className="mb-1 inline-flex items-center gap-1.5 text-xs font-semibold text-gray-400 transition hover:text-cyan-600"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to flows
          </Link>
          <h2 className="text-2xl font-bold text-gray-800">{flow.name}</h2>
          <p className="text-sm text-gray-500">
            {flow.description || 'No flow brief set. Open the Tasks tab to add context.'}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-3">
          <button
            onClick={() => persistFlow()}
            className="flex items-center rounded-lg border border-gray-200 bg-gray-50 px-4 py-2 text-sm font-semibold text-gray-700 shadow-sm transition-colors hover:bg-gray-100"
          >
            <Save className="mr-2 h-4 w-4 text-cyan-500" />
            {saving ? 'Saving…' : dirty ? 'Save flow' : 'Saved'}
          </button>
          {(running || flow?.running) && (
            <button
              onClick={handleStopFlow}
              disabled={stopping}
              className="flex items-center rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm font-semibold text-rose-600 shadow-sm transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {stopping
                ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                : <Square className="mr-2 h-4 w-4" />}
              {stopping ? 'Stopping…' : 'Stop'}
            </button>
          )}
          <button
            onClick={handleRunFlow}
            disabled={running || !!flow?.running}
            className="flex items-center rounded-lg bg-cyan-600 px-4 py-2 text-sm font-bold text-white shadow-md transition-all hover:bg-cyan-700 disabled:cursor-not-allowed disabled:bg-cyan-400"
          >
            {(running || flow?.running)
              ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              : <Play className="mr-2 h-4 w-4" />}
            {(running || flow?.running) ? 'Running…' : 'Run flow'}
          </button>
        </div>
      </div>

      <section className="relative min-h-0 flex-1">
        <FlowCanvas
          availableAgents={availableAgents}
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

        <div className="absolute bottom-0 right-0 top-0 z-50 flex w-[340px] flex-col overflow-hidden rounded-r-[28px] border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-200 px-4 pt-4">
            <div className="flex gap-2">
              {[
                { id: 'graph', label: selectedNode ? 'Node' : 'Graph', icon: SquareTerminal },
                { id: 'tasks', label: 'Tasks', icon: ClipboardList },
                { id: 'logs', label: 'Logs', icon: FileText },
              ].map((tab) => {
                const Icon = tab.icon;
                const isActive = activeTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setActiveTab(tab.id)}
                    className={`inline-flex w-[90px] items-center justify-center gap-2 rounded-t-2xl px-4 py-3 text-sm font-semibold transition ${
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

          <div className={`flex-1 min-h-0 ${activeTab === 'logs' ? 'overflow-hidden' : 'overflow-y-auto p-5'}`}>
            {activeTab === 'graph' ? (
              selectedNode ? (
                <div className="space-y-4">
                  <div className="rounded-[20px] border border-slate-200 bg-slate-50 p-4">
                    <div className="text-sm font-bold text-slate-900">{selectedNode.data.label}</div>
                    <div className="mt-1 text-xs font-medium text-slate-400">{selectedNode.data.agent_id}</div>
                  </div>
                  <input
                    value={selectedNode.data.label}
                    onChange={(event) => updateSelectedNode({ label: event.target.value })}
                    className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white"
                  />
                  <textarea
                    value={selectedNode.data.description || ''}
                    onChange={(event) => updateSelectedNode({ description: event.target.value })}
                    rows={5}
                    placeholder="Node description"
                    className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white"
                  />
                  <textarea
                    value={selectedNode.data.nodeTask || ''}
                    onChange={(event) => updateSelectedNode({ nodeTask: event.target.value })}
                    rows={4}
                    placeholder="Optional node-specific task"
                    className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white"
                  />
                  <button
                    onClick={() => handleRunNode(selectedNode.id)}
                    disabled={runNodeId === selectedNode.id}
                    className="inline-flex items-center gap-2 rounded-2xl border border-cyan-200 bg-cyan-50 px-4 py-2 text-sm font-semibold text-cyan-700 transition hover:bg-cyan-100 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <Play className="h-4 w-4" />
                    {runNodeId === selectedNode.id ? 'Running node...' : 'Run selected node'}
                  </button>
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="rounded-[20px] border border-slate-200 bg-slate-50 p-4">
                    <div className="text-sm font-bold text-slate-900">Graph overview</div>
                    <div className="mt-2 text-sm text-slate-500">
                      {nodes.length} nodes, {edges.length} connections
                    </div>
                  </div>
                  <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
                    Select a node on the canvas to edit its description, node-specific task, and run only that agent.
                  </div>
                </div>
              )
            ) : null}

            {activeTab === 'tasks' ? (
              <div className="space-y-5">
                {assignedTask ? (
                  <div className="rounded-[20px] border border-cyan-100 bg-cyan-50/70 p-4">
                    <div className="flex items-center justify-between">
                      <div className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-700">Current task</div>
                      <button
                        type="button"
                        onClick={() => {
                          setFlow((current) => ({ ...current, task_id: null }));
                          setDirty(true);
                        }}
                        className="text-xs font-medium text-slate-400 transition hover:text-rose-500"
                      >
                        Detach
                      </button>
                    </div>
                    <div className="mt-2 text-sm font-bold text-slate-900">{assignedTask.title}</div>
                    <div className="mt-1 text-sm leading-6 text-slate-600">{assignedTask.description || 'No task description.'}</div>
                  </div>
                ) : (
                  <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
                    No task is currently attached to this flow.
                  </div>
                )}

                {tasks.filter((t) => String(t.id) !== String(flow?.task_id)).length > 0 && (
                  <div className="space-y-2">
                    <div className="text-sm font-bold text-slate-900">Select existing task</div>
                    <div className="max-h-48 space-y-2 overflow-y-auto pr-1">
                      {tasks
                        .filter((t) => String(t.id) !== String(flow?.task_id))
                        .map((t) => (
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
                              setActiveTab('tasks');
                            }}
                            className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-left transition hover:border-cyan-300 hover:bg-cyan-50"
                          >
                            <div className="text-sm font-semibold text-slate-800">{t.title}</div>
                            {t.description && (
                              <div className="mt-0.5 truncate text-xs text-slate-400">{t.description}</div>
                            )}
                          </button>
                        ))}
                    </div>
                  </div>
                )}

                <form onSubmit={handleCreateTask} className="space-y-3">
                  <div className="text-sm font-bold text-slate-900">Create task</div>
                  <input
                    value={quickTask.title}
                    onChange={(event) => setQuickTask((current) => ({ ...current, title: event.target.value }))}
                    placeholder="Task title"
                    className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white"
                  />
                  <textarea
                    value={quickTask.description}
                    onChange={(event) => setQuickTask((current) => ({ ...current, description: event.target.value }))}
                    rows={4}
                    placeholder="Task description"
                    className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white"
                  />
                  <button
                    type="submit"
                    className="inline-flex items-center gap-2 rounded-2xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white transition hover:bg-cyan-700"
                  >
                    Create and attach task
                  </button>
                </form>
              </div>
            ) : null}

            {activeTab === 'logs' ? (
              <FlowLog logs={visibleLogs} filterLabel={selectedNode?.data.label} />
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}

function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
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
      <TerminalBlock label="input"  text={log.input}  defaultOpen={false} />
      <TerminalBlock label="output" text={log.output} defaultOpen={log.type === 'agent_finish'} />
    </div>
  );
}

function FlowLog({ logs, filterLabel }) {
  const bottomRef = useRef(null);
  const { theme } = useTheme();
  const isDark = theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs.length]);

  return (
    <div className="flex h-full flex-col bg-slate-50 dark:bg-slate-900">
      {filterLabel && (
        <div className="border-b border-slate-200 px-3 py-1.5 font-mono text-[10px] text-slate-400 dark:border-slate-700 dark:text-slate-500">
          # filtered · {filterLabel}
        </div>
      )}
      <div className="flex-1 overflow-y-auto px-3 py-3">
        {logs.length === 0 ? (
          <span className="font-mono text-[11px] text-slate-400 dark:text-slate-500">$ waiting for execution…</span>
        ) : (
          <div className="space-y-0.5">
            {logs.map((log, i) => (
              <LogLine key={`${log.timestamp}-${i}`} log={log} isLast={i === logs.length - 1} isDark={isDark} />
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

export default FlowEditor;
