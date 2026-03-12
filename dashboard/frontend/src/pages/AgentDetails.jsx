import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ChevronLeft, Activity, History, Server, Wrench, Cpu, Terminal, ExternalLink, CheckCircle, AlertCircle, Clock, Database, Save, Trash2, FileCode, Play, Square, Loader, X, FileText } from 'lucide-react';
import { getAgent, getAgentHistory, getAgentHealth, getLogs, updateAgentMemory, eraseAgentMemory, getNodes, getAgentDefinition, getTasks, startNode, stopNode, deleteNode, getWorkspaces, getNodeLogs } from '../api';

const NODE_STATUS = {
  running: { dot: 'bg-green-500 animate-pulse', badge: 'bg-green-100 text-green-800', label: 'Running' },
  starting: { dot: 'bg-yellow-400 animate-pulse', badge: 'bg-yellow-100 text-yellow-800', label: 'Starting' },
  stopping: { dot: 'bg-orange-400 animate-pulse', badge: 'bg-orange-100 text-orange-800', label: 'Stopping' },
  stopped: { dot: 'bg-gray-400', badge: 'bg-gray-100 text-gray-600', label: 'Stopped' },
  failed: { dot: 'bg-red-500', badge: 'bg-red-100 text-red-700', label: 'Failed' },
  completed: { dot: 'bg-blue-400', badge: 'bg-blue-100 text-blue-700', label: 'Completed' },
};

function NodeStatusBadge({ status }) {
  const s = NODE_STATUS[status] || NODE_STATUS.stopped;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold ${s.badge}`}>
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${s.dot}`} />
      {s.label}
    </span>
  );
}

function fmtNodeDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function nodeUptime(startedAt, finishedAt) {
  if (!startedAt) return '—';
  const end = finishedAt ? new Date(finishedAt) : new Date();
  const secs = Math.max(0, Math.floor((end - new Date(startedAt)) / 1000));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

const AgentDetails = () => {
  const { id } = useParams();
  const [agent, setAgent] = useState(null);
  const [history, setHistory] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [nodes, setNodes] = useState([]);
  const [workspaces, setWorkspaces] = useState([]);
  const [loading, setLoading] = useState(true);
  const [health, setHealth] = useState(null);
  const [selectedLog, setSelectedLog] = useState(null);
  const [logs, setLogs] = useState('');
  const [agentDefinition, setAgentDefinition] = useState({ system_prompt: '', yaml: '', source: '', yaml_path: '' });

  const [memoryType, setMemoryType] = useState('none');
  const [memoryData, setMemoryData] = useState('');
  const [isUpdatingMemory, setIsUpdatingMemory] = useState(false);
  const [activeTab, setActiveTab] = useState('overview');
  const [showStartNodeModal, setShowStartNodeModal] = useState(false);
  const [startWorkspace, setStartWorkspace] = useState('');
  const [startLabel, setStartLabel] = useState('');
  const [startingNode, setStartingNode] = useState(false);
  const [nodeBusy, setNodeBusy] = useState({});
  const [logsNode, setLogsNode] = useState(null);
  const [logsNodeText, setLogsNodeText] = useState('');
  const [logsNodeLoading, setLogsNodeLoading] = useState(false);

  const fetchData = async () => {
    try {
      const [agentResp, historyResp, tasksResp, workspacesResp] = await Promise.all([
        getAgent(id),
        getAgentHistory(id),
        getTasks(),
        getWorkspaces(),
      ]);
      setAgent(agentResp.data);
      setHistory(historyResp.data);
      setTasks(tasksResp.data || []);
      setWorkspaces(workspacesResp.data || []);
      setMemoryType(agentResp.data.memory_type || 'none');
      setMemoryData(typeof agentResp.data.memory_data === 'string' ? agentResp.data.memory_data : JSON.stringify(agentResp.data.memory_data || '', null, 2));
      try {
        const nodesResp = await getNodes();
        setNodes((nodesResp.data || []).filter((n) => n.agent_id === id));
      } catch {
        setNodes([]);
      }
      try {
        const definitionResp = await getAgentDefinition(id);
        setAgentDefinition(definitionResp.data || { system_prompt: '', yaml: '', source: '', yaml_path: '' });
      } catch {
        setAgentDefinition({ system_prompt: '', yaml: '', source: '', yaml_path: '' });
      }

      if (agentResp.data.is_remote) {
        try {
          const healthResp = await getAgentHealth(id);
          setHealth(healthResp.data);
        } catch {
          setHealth({ status: 'offline' });
        }
      }
      setLoading(false);
    } catch (error) {
      console.error('Error fetching agent details:', error);
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [id]);

  const handleUpdateMemory = async () => {
    setIsUpdatingMemory(true);
    try {
      let data = memoryData;
      try {
        data = JSON.parse(memoryData);
      } catch {
        // keep as string
      }
      await updateAgentMemory(id, { memory_type: memoryType, memory_data: data });
      fetchData();
    } catch (error) {
      console.error('Error updating memory:', error);
    } finally {
      setIsUpdatingMemory(false);
    }
  };

  const handleEraseMemory = async () => {
    if (!window.confirm('Are you sure you want to erase agent memory?')) return;
    setIsUpdatingMemory(true);
    try {
      await eraseAgentMemory(id);
      fetchData();
    } catch (error) {
      console.error('Error erasing memory:', error);
    } finally {
      setIsUpdatingMemory(false);
    }
  };

  const viewLogs = async (runId) => {
    try {
      setSelectedLog(runId);
      setLogs('Loading logs...');
      const resp = await getLogs(runId);
      setLogs(resp.data.logs);
    } catch {
      setLogs('Could not fetch logs for this run.');
    }
  };

  const handleStartNode = async () => {
    if (agent?.is_remote) return;
    setStartingNode(true);
    try {
      await startNode({ agent_id: id, workspace: startWorkspace || null, label: startLabel || null });
      setShowStartNodeModal(false);
      setStartWorkspace('');
      setStartLabel('');
      fetchData();
    } catch (error) {
      alert(error.response?.data?.detail || error.message || 'Failed to start node');
    } finally {
      setStartingNode(false);
    }
  };

  const handleStopNode = async (nodeId) => {
    setNodeBusy((prev) => ({ ...prev, [nodeId]: 'stopping' }));
    try {
      await stopNode(nodeId);
      fetchData();
    } catch (error) {
      alert(error.response?.data?.detail || error.message || 'Failed to stop node');
    } finally {
      setNodeBusy((prev) => {
        const next = { ...prev };
        delete next[nodeId];
        return next;
      });
    }
  };

  const handleDeleteNode = async (nodeId) => {
    if (!window.confirm('Remove this stopped/failed node record?')) return;
    setNodeBusy((prev) => ({ ...prev, [nodeId]: 'deleting' }));
    try {
      await deleteNode(nodeId);
      fetchData();
    } catch (error) {
      alert(error.response?.data?.detail || error.message || 'Failed to remove node');
    } finally {
      setNodeBusy((prev) => {
        const next = { ...prev };
        delete next[nodeId];
        return next;
      });
    }
  };

  const openNodeLogs = async (node) => {
    const nodeId = node.node_id || node.id;
    setLogsNode(node);
    setLogsNodeLoading(true);
    setLogsNodeText('');
    try {
      const resp = await getNodeLogs(nodeId);
      setLogsNodeText(resp.data?.logs || '(empty)');
    } catch (error) {
      setLogsNodeText(error.response?.data?.detail || 'Failed to load node logs.');
    } finally {
      setLogsNodeLoading(false);
    }
  };

  if (loading) return <div className="text-center py-10">Loading agent details...</div>;
  if (!agent) return <div className="text-center py-10">Agent not found</div>;

  const activeTask = history.find(r => r.status === 'running');
  const configuredTools = Array.isArray(agent?.default_params?.tools) ? agent.default_params.tools : [];
  const capabilities = Array.isArray(agent?.capabilities) ? agent.capabilities : [];
  const mergedTools = [...new Set([...configuredTools, ...capabilities])];
  const runningNodesCount = nodes.filter((n) => n.status === 'running' || n.status === 'starting').length;
  const agentTasks = (tasks || []).filter((t) => t.assigned_agent_type === id);
  const runningTasks = agentTasks.filter((t) => t.agent_state === 'running');

  return (
    <div>
      <Link to="/agents" className="flex items-center text-indigo-600 hover:text-indigo-900 mb-6">
        <ChevronLeft className="w-4 h-4 mr-1" /> Back to Agents
      </Link>

      <div className="bg-white p-6 shadow-md rounded-lg border-t-4 border-indigo-600 mb-6">
        <h2 className="text-2xl font-bold text-gray-900 mb-2">{agent.name}</h2>
        <p className="text-sm text-gray-500 font-mono mb-4">{agent.id}</p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-indigo-500 font-semibold">Current Capacity</div>
            <div className="text-sm font-semibold text-indigo-900">{runningTasks.length} / {agent.capacity || 1}</div>
          </div>
          <div className="bg-green-50 border border-green-100 rounded-lg px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-green-500 font-semibold">Running Nodes</div>
            <div className="text-sm font-semibold text-green-900">{runningNodesCount}</div>
          </div>
          <div className="bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-amber-500 font-semibold">Running Tasks</div>
            <div className="text-sm font-semibold text-amber-900">{runningTasks.length}</div>
          </div>
        </div>
      </div>

      <div className="mb-6 border-b border-gray-200">
        <nav className="flex flex-wrap gap-2 -mb-px">
          {[
            { id: 'overview', label: 'Overview', icon: Activity },
            { id: 'history', label: 'History', icon: History },
            { id: 'memory', label: 'Memory', icon: Database },
            { id: 'tools', label: 'Tools', icon: Wrench },
            { id: 'nodes', label: 'Nodes', icon: Server },
            { id: 'tasks', label: 'Tasks', icon: Clock },
            { id: 'config', label: 'Config', icon: FileCode },
          ].map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`inline-flex items-center px-4 py-2 text-sm font-semibold border-b-2 transition-colors ${
                  isActive
                    ? 'border-indigo-600 text-indigo-700'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
              >
                <Icon className="w-4 h-4 mr-2" />
                {tab.label}
              </button>
            );
          })}
        </nav>
      </div>

      {activeTab === 'overview' && (
        <div className="space-y-6">
          <div className="bg-white p-6 shadow-md rounded-lg">
            <div className="space-y-4">
              <div className="flex items-center justify-between py-2 border-b border-gray-50">
                <span className="text-gray-500 flex items-center"><Server className="w-4 h-4 mr-2" /> Type</span>
                <span className="font-medium capitalize">{agent.is_remote ? 'Remote' : 'Local'}</span>
              </div>

              {agent.is_remote && (
                <div className="py-2 border-b border-gray-50">
                  <span className="text-gray-500 flex items-center mb-1"><ExternalLink className="w-4 h-4 mr-2" /> URL</span>
                  <p className="text-xs font-mono text-indigo-600 break-all">{agent.agent_url}</p>
                </div>
              )}

              <div className="flex items-center justify-between py-2 border-b border-gray-50">
                <span className="text-gray-500 flex items-center"><Activity className="w-4 h-4 mr-2" /> Capacity</span>
                <span className="font-medium">{agent.capacity} concurrent runs</span>
              </div>

              {agent.original_id && (
                <div className="flex items-center justify-between py-2 border-b border-gray-50">
                  <span className="text-gray-500 flex items-center"><History className="w-4 h-4 mr-2" /> Cloned From</span>
                  <Link to={`/agents/${agent.original_id}`} className="text-indigo-600 hover:underline font-medium">{agent.original_id}</Link>
                </div>
              )}

              {agent.default_params?.model && (
                <div className="flex items-center justify-between py-2 border-b border-gray-50">
                  <span className="text-gray-500 flex items-center"><Cpu className="w-4 h-4 mr-2" /> Model</span>
                  <span className="font-mono text-sm">{agent.default_params.model}</span>
                </div>
              )}

              <div className="py-2">
                <span className="text-gray-500 flex items-center mb-2"><Wrench className="w-4 h-4 mr-2" /> Capabilities</span>
                <div className="flex flex-wrap gap-2">
                  {capabilities.map(cap => (
                    <span key={cap} className="text-xs bg-gray-100 px-2 py-1 rounded text-gray-600">{cap}</span>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {agent.is_remote && health && (
            <div className={`p-4 rounded-lg shadow-sm border ${health.status === 'up' ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
              <h3 className={`font-bold flex items-center ${health.status === 'up' ? 'text-green-800' : 'text-red-800'}`}>
                <Activity className="w-4 h-4 mr-2" /> Health Status: {health.status.toUpperCase()}
              </h3>
              {health.details && <pre className="text-[10px] mt-2 overflow-auto max-h-20">{JSON.stringify(health.details, null, 2)}</pre>}
              {health.error && <p className="text-xs text-red-600 mt-1">{health.error}</p>}
            </div>
          )}

          {activeTask && (
            <div className="bg-indigo-50 p-4 rounded-lg border border-indigo-100">
              <h3 className="text-indigo-800 font-bold flex items-center mb-2">
                <Clock className="w-4 h-4 mr-2" /> Currently Active
              </h3>
              <p className="text-sm text-indigo-900 font-medium truncate mb-2">Task ID: {activeTask.task_id}</p>
              <Link to={`/tasks/${activeTask.task_id}`} className="text-xs bg-indigo-600 text-white px-3 py-1 rounded hover:bg-indigo-700 inline-block">
                View Task Details
              </Link>
            </div>
          )}
        </div>
      )}

      {activeTab === 'history' && (
        <div className="space-y-6">
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <History className="w-5 h-5 mr-2" /> Execution History
            </h3>

            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Task ID</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Started At</th>
                    <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {history.length === 0 ? (
                    <tr>
                      <td colSpan="4" className="px-4 py-8 text-center text-gray-500 italic">No execution history found for this agent.</td>
                    </tr>
                  ) : (
                    history.map((run) => (
                      <tr key={run.run_id} className="hover:bg-gray-50">
                        <td className="px-4 py-2 whitespace-nowrap">
                          <div className="flex items-center">
                            {run.status === 'completed' && <CheckCircle className="w-4 h-4 text-green-500 mr-2" />}
                            {run.status === 'failed' && <AlertCircle className="w-4 h-4 text-red-500 mr-2" />}
                            {run.status === 'running' && <Clock className="w-4 h-4 text-blue-500 mr-2 animate-spin" />}
                            <span className="text-sm capitalize">{run.status}</span>
                          </div>
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-sm font-mono text-gray-600">
                          {String(run.task_id || '').slice(0, 8)}...
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-xs text-gray-500">
                          {new Date(run.started_at).toLocaleString()}
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-right">
                          <button
                            onClick={() => viewLogs(run.run_id)}
                            className="text-indigo-600 hover:text-indigo-900 text-xs font-medium flex items-center justify-end"
                          >
                            <Terminal className="w-3 h-3 mr-1" /> Logs
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {selectedLog && (
            <div className="bg-gray-900 rounded-lg shadow-md overflow-hidden flex flex-col h-[500px]">
              <div className="bg-gray-800 px-4 py-2 flex items-center justify-between border-b border-gray-700">
                <div className="flex items-center text-gray-300 text-sm font-medium">
                  <Terminal className="w-4 h-4 mr-2" /> Run Logs: {selectedLog.slice(0, 8)}
                </div>
                <button onClick={() => setSelectedLog(null)} className="text-gray-400 hover:text-white">&times;</button>
              </div>
              <div className="p-4 flex-1 overflow-auto font-mono text-xs text-green-400 bg-black">
                <pre className="whitespace-pre-wrap">{logs}</pre>
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === 'memory' && (
        <div className="bg-white p-6 shadow-md rounded-lg border-t-4 border-amber-500">
          <h3 className="text-lg font-bold text-gray-900 mb-4 flex items-center">
            <Database className="w-5 h-5 mr-2 text-amber-500" /> Memory Management
          </h3>

          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Memory Type</label>
              <select
                value={memoryType}
                onChange={(e) => setMemoryType(e.target.value)}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                <option value="none">None</option>
                <option value="local">Local (Agent-specific)</option>
                <option value="shared">Shared Memory</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Memory Data / ID</label>
              <textarea
                value={memoryData}
                onChange={(e) => setMemoryData(e.target.value)}
                placeholder={memoryType === 'shared' ? 'Enter Shared Memory ID' : 'Enter memory content or configuration'}
                rows={6}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div className="flex space-x-2">
              <button
                onClick={handleUpdateMemory}
                disabled={isUpdatingMemory}
                className="flex-1 bg-indigo-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-indigo-700 flex items-center justify-center disabled:opacity-50"
              >
                <Save className="w-4 h-4 mr-2" /> {isUpdatingMemory ? 'Updating...' : 'Update'}
              </button>
              <button
                onClick={handleEraseMemory}
                disabled={isUpdatingMemory || agent.memory_type === 'none'}
                className="bg-red-50 text-red-600 px-4 py-2 rounded-md text-sm font-medium hover:bg-red-100 flex items-center justify-center disabled:opacity-50 border border-red-200"
              >
                <Trash2 className="w-4 h-4 mr-2" /> Erase
              </button>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'tools' && (
        <div className="space-y-6">
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <Wrench className="w-5 h-5 mr-2 text-indigo-600" />
              Tools
            </h3>
            {mergedTools.length ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {mergedTools.map((tool) => (
                  <div key={tool} className="p-3 border border-gray-100 rounded-lg bg-gray-50">
                    <div className="text-sm font-semibold text-gray-800">{tool}</div>
                    <div className="text-xs text-gray-500 mt-1 font-mono">source: {configuredTools.includes(tool) ? 'default_params.tools' : 'capabilities'}</div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-500 italic">No tools configured for this agent.</p>
            )}
          </div>

          {agent.default_params && Object.keys(agent.default_params).length > 0 && (
            <div className="bg-white p-6 shadow-md rounded-lg">
              <h4 className="text-md font-bold mb-3 text-gray-800">Default Params</h4>
              <pre className="text-xs bg-gray-900 text-green-300 p-4 rounded-lg overflow-auto">
                {JSON.stringify(agent.default_params, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}

      {activeTab === 'nodes' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-bold flex items-center">
              <Server className="w-5 h-5 mr-2 text-indigo-600" />
              Agent Nodes
            </h3>
            {!agent.is_remote && (
              <button
                type="button"
                onClick={() => setShowStartNodeModal(true)}
                className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700"
              >
                <Play className="w-3.5 h-3.5 mr-1" />
                Start Node
              </button>
            )}
          </div>
          {agent.is_remote && (
            <p className="text-xs text-gray-500 mb-3">Remote agents do not support local node start/stop controls.</p>
          )}
          {nodes.length === 0 ? (
            <p className="text-sm text-gray-500 italic">No nodes found for this agent.</p>
          ) : (
            <div className="overflow-x-auto border border-gray-100 rounded-lg">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50">
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Status</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Node ID</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Label</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Workspace</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Started</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Uptime</th>
                    <th className="text-right px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {nodes.map((node) => {
                    const nodeId = node.node_id || node.id;
                    const isActive = node.status === 'running' || node.status === 'starting';
                    const busy = nodeBusy[nodeId];
                    return (
                      <tr key={nodeId} className="hover:bg-gray-50 transition-colors">
                        <td className="px-5 py-3">
                          <NodeStatusBadge status={node.status} />
                        </td>
                        <td className="px-5 py-3">
                          <span className="font-mono text-xs text-gray-600">
                            {String(nodeId).slice(0, 8)}
                            <span className="text-gray-400">…</span>
                          </span>
                        </td>
                        <td className="px-5 py-3 text-gray-600 text-xs">{node.label || '—'}</td>
                        <td className="px-5 py-3">
                          {node.workspace
                            ? <span className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded font-mono">{node.workspace}</span>
                            : <span className="text-gray-400 text-xs">—</span>}
                        </td>
                        <td className="px-5 py-3 text-gray-500 text-xs whitespace-nowrap">{fmtNodeDate(node.started_at)}</td>
                        <td className="px-5 py-3 text-gray-600 text-xs font-mono whitespace-nowrap">
                          {nodeUptime(node.started_at, node.finished_at)}
                        </td>
                        <td className="px-5 py-3">
                          <div className="flex items-center justify-end gap-1.5">
                            <button
                              type="button"
                              onClick={() => openNodeLogs(node)}
                              title="View logs"
                              className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
                            >
                              <FileText className="w-4 h-4" />
                            </button>
                            {isActive ? (
                              <button
                                type="button"
                                onClick={() => handleStopNode(nodeId)}
                                disabled={!!busy}
                                title="Stop node"
                                className="p-1.5 rounded text-gray-400 hover:text-orange-600 hover:bg-orange-50 transition-colors disabled:opacity-40"
                              >
                                {busy === 'stopping' ? <Loader className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4" />}
                              </button>
                            ) : (
                              <button
                                type="button"
                                onClick={() => handleDeleteNode(nodeId)}
                                disabled={!!busy}
                                title="Remove record"
                                className="p-1.5 rounded text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors disabled:opacity-40"
                              >
                                {busy === 'deleting' ? <Loader className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {activeTab === 'tasks' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-lg font-bold mb-4 flex items-center">
            <Clock className="w-5 h-5 mr-2 text-indigo-600" />
            Tasks Assigned To This Agent
          </h3>
          {agentTasks.length === 0 ? (
            <p className="text-sm text-gray-500 italic">No tasks assigned to this agent yet.</p>
          ) : (
            <div className="space-y-3">
              {agentTasks
                .slice()
                .sort((a, b) => {
                  const aRunning = a.agent_state === 'running' ? 1 : 0;
                  const bRunning = b.agent_state === 'running' ? 1 : 0;
                  if (aRunning !== bRunning) return bRunning - aRunning;
                  const aTs = new Date(a.created_at || 0).getTime() || 0;
                  const bTs = new Date(b.created_at || 0).getTime() || 0;
                  return bTs - aTs;
                })
                .map((task) => (
                  <div key={task.id} className="border border-gray-100 rounded-lg p-3 bg-gray-50">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <Link to={`/tasks/${task.id}`} className="text-sm font-semibold text-indigo-700 hover:underline truncate block">
                          {task.title || task.id}
                        </Link>
                        <div className="text-xs text-gray-500 font-mono mt-1 truncate">{task.id}</div>
                        <div className="text-xs text-gray-500 mt-1">Workspace: <span className="font-mono">{task.workspace || '—'}</span></div>
                      </div>
                      <div className="flex flex-col items-end gap-1">
                        <span className={`text-xs px-2 py-0.5 rounded font-semibold capitalize ${
                          task.agent_state === 'running'
                            ? 'bg-blue-100 text-blue-700'
                            : 'bg-gray-200 text-gray-700'
                        }`}>
                          agent: {task.agent_state || 'unknown'}
                        </span>
                        <span className={`text-xs px-2 py-0.5 rounded font-semibold capitalize ${
                          task.status === 'done'
                            ? 'bg-green-100 text-green-700'
                            : task.status === 'blocked'
                              ? 'bg-red-100 text-red-700'
                              : 'bg-gray-200 text-gray-700'
                        }`}>
                          task: {task.status || 'unknown'}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
            </div>
          )}
        </div>
      )}

      {activeTab === 'config' && (
        <div className="space-y-6">
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <Activity className="w-5 h-5 mr-2 text-indigo-600" />
              Agent Metadata
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
              <div><span className="text-gray-500">ID:</span> <span className="font-mono">{agent.id}</span></div>
              <div><span className="text-gray-500">Name:</span> <span className="font-medium">{agent.name}</span></div>
              <div><span className="text-gray-500">Type:</span> <span className="font-mono">{agent.type}</span></div>
              <div><span className="text-gray-500">Domain:</span> <span className="font-mono">{agent.domain || 'general'}</span></div>
              <div><span className="text-gray-500">Entrypoint:</span> <span className="font-mono break-all">{agent.entrypoint}</span></div>
              <div><span className="text-gray-500">Capacity:</span> <span className="font-mono">{agent.capacity}</span></div>
              <div><span className="text-gray-500">Memory Type:</span> <span className="font-mono">{agent.memory_type || 'none'}</span></div>
              <div><span className="text-gray-500">Definition Source:</span> <span className="font-mono">{agentDefinition.source || '—'}</span></div>
            </div>
            {agentDefinition.yaml_path && (
              <p className="text-xs text-gray-500 mt-3 font-mono break-all">YAML path: {agentDefinition.yaml_path}</p>
            )}
          </div>

          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <Terminal className="w-5 h-5 mr-2 text-indigo-600" />
              System Prompt
            </h3>
            {agentDefinition.system_prompt ? (
              <pre className="text-xs bg-gray-900 text-green-300 p-4 rounded-lg overflow-auto whitespace-pre-wrap">
                {agentDefinition.system_prompt}
              </pre>
            ) : (
              <p className="text-sm text-gray-500 italic">No system prompt available for this agent.</p>
            )}
          </div>

          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <FileCode className="w-5 h-5 mr-2 text-indigo-600" />
              YAML Definition
            </h3>
            {agentDefinition.yaml ? (
              <pre className="text-xs bg-gray-900 text-green-300 p-4 rounded-lg overflow-auto whitespace-pre-wrap">
                {agentDefinition.yaml}
              </pre>
            ) : (
              <p className="text-sm text-gray-500 italic">No YAML definition available.</p>
            )}
          </div>
        </div>
      )}

      {showStartNodeModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-md">
            <div className="flex items-center justify-between p-5 border-b">
              <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
                <Play className="w-4 h-4 text-indigo-600" />
                Start Node
              </h2>
              <button onClick={() => setShowStartNodeModal(false)} className="text-gray-400 hover:text-gray-600">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-5 space-y-4">
              <div>
                <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
                  Workspace
                </label>
                <select
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  value={startWorkspace}
                  onChange={(e) => setStartWorkspace(e.target.value)}
                >
                  <option value="">— None —</option>
                  {workspaces.map((ws) => (
                    <option key={ws.name} value={ws.name}>{ws.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
                  Label <span className="text-gray-400 font-normal normal-case">(optional)</span>
                </label>
                <input
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  placeholder="e.g. dev-worker"
                  value={startLabel}
                  onChange={(e) => setStartLabel(e.target.value)}
                />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowStartNodeModal(false)}
                  className="px-4 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200"
                  disabled={startingNode}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={startingNode}
                  onClick={handleStartNode}
                  className="flex items-center gap-2 px-5 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                >
                  {startingNode ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                  {startingNode ? 'Starting…' : 'Start Node'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {logsNode && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-950 rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col border border-gray-800">
            <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
              <div className="flex items-center gap-3">
                <NodeStatusBadge status={logsNode.status} />
                <span className="text-gray-200 font-mono text-sm font-semibold">
                  {logsNode.agent_name || logsNode.agent_id || agent?.name || id}
                </span>
                <span className="text-gray-500 font-mono text-xs">
                  {(logsNode.node_id || logsNode.id || '').toString().slice(0, 12)}…
                </span>
              </div>
              <button onClick={() => setLogsNode(null)} className="text-gray-500 hover:text-gray-300 transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 overflow-auto p-5">
              {logsNodeLoading ? (
                <div className="flex justify-center py-12">
                  <Loader className="w-5 h-5 animate-spin text-indigo-400" />
                </div>
              ) : (
                <pre className="text-xs font-mono text-green-400 whitespace-pre-wrap break-words leading-5">{logsNodeText}</pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default AgentDetails;
