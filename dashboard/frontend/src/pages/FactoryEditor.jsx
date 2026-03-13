import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, ClipboardList, FileText, Loader2, Play, Save, SquareTerminal } from 'lucide-react';
import { createTask, getAgents, getFlow, getFlowLogs, getTasks, runFlow, runFlowNode, updateFlow } from '../api';
import { useWorkspace } from '../components/WorkspaceContext';
import FactoryCanvas, { applyEdgeChanges, applyNodeChanges } from '../components/factory/FactoryCanvas';

const LOG_TAG_STYLES = {
  management: 'bg-cyan-50 text-cyan-700 border-cyan-200',
  analysis: 'bg-amber-50 text-amber-700 border-amber-200',
  design: 'bg-violet-50 text-violet-700 border-violet-200',
  development: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  testing: 'bg-rose-50 text-rose-700 border-rose-200',
  operations: 'bg-orange-50 text-orange-700 border-orange-200',
  factory: 'bg-slate-100 text-slate-700 border-slate-200',
};

function normalizeNode(node, onRunNode) {
  return {
    id: node.id,
    type: 'factoryNode',
    position: node.position || { x: 100, y: 100 },
    style: { width: 90, ...(node.style || {}) },
    data: {
      node_id: node.id,
      label: node.data?.label || node.label || 'Factory Agent',
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

function FactoryEditor() {
  const navigate = useNavigate();
  const { factoryId } = useParams();
  const { selectedWorkspace, liveUpdates } = useWorkspace();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
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

  const loadFactory = async () => {
    setLoading(true);
    try {
      const [flowResponse, agentsResponse] = await Promise.all([getFlow(factoryId), getAgents()]);
      const nextFlow = flowResponse.data;
      setFlow(nextFlow);
      setNodes((nextFlow.nodes || []).map((node) => normalizeNode(node, handleRunNode)));
      setEdges((nextFlow.edges || []).map((edge) => ({
        ...edge,
        animated: true,
        style: { stroke: '#0891b2', strokeWidth: 2 },
      })));
      setAvailableAgents(
        (agentsResponse.data || []).filter(
          (agent) => agent.id.startsWith('factory-') && !agent.id.includes('graph')
        )
      );
    } catch (error) {
      console.error('Failed to load factory', error);
      navigate('/factory');
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
      const response = await getFlowLogs(factoryId, workspaceName);
      setLogs(response.data || []);
    } catch (error) {
      console.error('Failed to load logs', error);
    }
  };

  useEffect(() => {
    loadFactory();
  }, [factoryId]);

  useEffect(() => {
    const workspaceName = flow?.workspace || selectedWorkspace;
    if (!workspaceName) return;
    loadWorkspaceTasks(workspaceName);
    loadLogs(workspaceName);
    if (!liveUpdates) return;
    const interval = setInterval(() => loadLogs(workspaceName), 4000);
    return () => clearInterval(interval);
  }, [factoryId, flow?.workspace, selectedWorkspace, liveUpdates]);

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
      const response = await updateFlow(factoryId, payload);
      setFlow(response.data);
      setNodes((response.data.nodes || []).map((node) => normalizeNode(node, handleRunNode)));
      setEdges((response.data.edges || []).map((edge) => ({
        ...edge,
        animated: true,
        style: { stroke: '#0891b2', strokeWidth: 2 },
      })));
      setDirty(false);
      return response.data;
    } catch (error) {
      alert(`Failed to save factory: ${error.response?.data?.detail || error.message}`);
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

  const handleRunFactory = async () => {
    if (!flow) return;
    const workspaceName = flow.workspace || selectedWorkspace;
    if (!workspaceName) {
      alert('Select a workspace for this factory first.');
      return;
    }
    const saved = await persistFlow({ workspace: workspaceName });
    if (!saved) return;
    setRunning(true);
    try {
      await runFlow(factoryId, {
        workspace: workspaceName,
        description: flow.description || assignedTask?.description || '',
      });
      await loadLogs(workspaceName);
      setActiveTab('logs');
    } catch (error) {
      alert(`Failed to start factory: ${error.response?.data?.detail || error.message}`);
    } finally {
      setRunning(false);
    }
  };

  async function handleRunNode(nodeId) {
    if (!flow) return;
    const workspaceName = flow.workspace || selectedWorkspace;
    if (!workspaceName) {
      alert('Select a workspace for this factory first.');
      return;
    }
    const saved = await persistFlow({ workspace: workspaceName });
    if (!saved) return;
    setRunNodeId(nodeId);
    setSelectedNodeId(nodeId);
    try {
      await runFlowNode(factoryId, {
        node_id: nodeId,
        workspace: workspaceName,
        description: flow.description || assignedTask?.description || '',
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
            to="/factory"
            className="mb-1 inline-flex items-center gap-1.5 text-xs font-semibold text-gray-400 transition hover:text-cyan-600"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to factories
          </Link>
          <h2 className="text-2xl font-bold text-gray-800">{flow.name}</h2>
          <p className="text-sm text-gray-500">
            {flow.description || 'No factory brief set. Open the Tasks tab to add context.'}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-3">
          <button
            onClick={() => persistFlow()}
            className="flex items-center rounded-lg border border-gray-200 bg-gray-50 px-4 py-2 text-sm font-semibold text-gray-700 shadow-sm transition-colors hover:bg-gray-100"
          >
            <Save className="mr-2 h-4 w-4 text-cyan-500" />
            {saving ? 'Saving…' : dirty ? 'Save factory' : 'Saved'}
          </button>
          <button
            onClick={handleRunFactory}
            disabled={running}
            className="flex items-center rounded-lg bg-cyan-600 px-4 py-2 text-sm font-bold text-white shadow-md transition-all hover:bg-cyan-700 disabled:cursor-not-allowed disabled:bg-cyan-300"
          >
            <Play className="mr-2 h-4 w-4" />
            {running ? 'Running…' : 'Run factory'}
          </button>
        </div>
      </div>

      <section className="relative min-h-0 flex-1">
        <FactoryCanvas
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

          <div className="overflow-y-auto p-5">
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
                    <div className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-700">Current task</div>
                    <div className="mt-2 text-sm font-bold text-slate-900">{assignedTask.title}</div>
                    <div className="mt-1 text-sm leading-6 text-slate-600">{assignedTask.description || 'No task description.'}</div>
                  </div>
                ) : (
                  <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
                    No task is currently attached to this factory.
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
              <div className="space-y-4">
                {selectedNode ? (
                  <div className="rounded-[18px] border border-slate-200 bg-slate-50 px-4 py-3 text-xs font-medium text-slate-500">
                    Showing logs only for {selectedNode.data.label}
                  </div>
                ) : null}
                <div className="max-h-[620px] space-y-3 overflow-auto pr-1">
                  {visibleLogs.length === 0 ? (
                    <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-5 text-sm text-slate-500">
                      {selectedNode ? 'No logs yet for this node.' : 'No execution logs yet for this factory.'}
                    </div>
                  ) : (
                    visibleLogs
                      .slice()
                      .reverse()
                      .map((log, index) => {
                        const tagKey = log.tag || 'factory';
                        const tagStyle = LOG_TAG_STYLES[tagKey] || LOG_TAG_STYLES.factory;
                        return (
                          <div key={`${log.timestamp}-${index}`} className="rounded-[20px] border border-slate-200 bg-slate-50 p-4">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className={`rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] ${tagStyle}`}>
                                {log.agent_name || log.type}
                              </span>
                              {log.status ? (
                                <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                                  {log.status}
                                </span>
                              ) : null}
                              <span className="text-[11px] font-medium text-slate-400">
                                {new Date(log.timestamp).toLocaleString()}
                              </span>
                            </div>
                            <div className="mt-2 text-sm leading-6 text-slate-700">{log.content}</div>
                          </div>
                        );
                      })
                  )}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}

export default FactoryEditor;
