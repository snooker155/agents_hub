import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/WorkspaceContext';
import {
  Copy,
  Play,
  Box,
  Shield,
  Server,
  Trash2,
  RefreshCw,
  FileCode,
  Activity,
  Wand2,
  ChevronRight,
  ChevronLeft,
  Check,
  Globe,
  GitBranch,
  Sparkles,
  GripVertical,
} from 'lucide-react';
import {
  getAgents,
  getAgentTools,
  getTasks,
  getNodes,
  cloneAgent,
  assignAgent,
  connectAgent,
  createCustomAgent,
  disconnectAgent,
  getAgentHealth,
  startNode,
  getAgentWorkspaceCapacities,
  getAgentModel,
  removeAgentFromWorkspace,
  addAgentToWorkspace,
} from '../api';

const AgentManager = () => {
  const navigate = useNavigate();
  const { selectedWorkspace, workspaceFilter, liveUpdates } = useWorkspace();
  const [agents, setAgents] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [nodes, setNodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCloneModal, setShowCloneModal] = useState(false);
  const [showAssignModal, setShowAssignModal] = useState(false);
  const [showConnectModal, setShowConnectModal] = useState(false);
  const [showWizard, setShowWizard] = useState(false);
  const [wizardStep, setWizardStep] = useState(1);
  const [wizardType, setWizardType] = useState('custom'); // 'custom' | 'remote' | 'clone'
  const [wizardData, setWizardData] = useState({
    id: '', name: '', description: '', domain: 'general',
    system_prompt: '', tools: ['read_file', 'write_file', 'list_files'], capacity: 1,
    agent_url: '', original_id: '',
  });
  const [availableTools, setAvailableTools] = useState([]);
  const [cloneData, setCloneData] = useState({ original_id: '', new_id: '', new_name: '' });
  const [assignData, setAssignData] = useState({ task_id: '', agent_id: '' });
  const [connectData, setConnectData] = useState({ id: '', name: '', description: '', domain: 'general', agent_url: '', capacity: 1 });
  const [healthData, setHealthData] = useState({});
  const [startingNode, setStartingNode] = useState(null);
  const [wsCapacitiesPerAgent, setWsCapacitiesPerAgent] = useState({});
  const [agentOrder, setAgentOrder] = useState([]);
  const [dragIdx, setDragIdx] = useState(null);
  const [dragOverIdx, setDragOverIdx] = useState(null);

  // Creator chat state
  const [creatorInput, setCreatorInput] = useState('');
  const [creatorStreaming, setCreatorStreaming] = useState(false);
  const [creatorOutput, setCreatorOutput] = useState('');
  const [creatorDone, setCreatorDone] = useState(false);
  const [creatorError, setCreatorError] = useState('');
  const [creatorModelInfo, setCreatorModelInfo] = useState(null);

  const fetchTools = async () => {
    try {
      const res = await getAgentTools();
      setAvailableTools(res.data?.all || []);
    } catch {
      // ignore
    }
  };

  const fetchData = async () => {
    try {
      const [agentsResp, tasksResp, nodesResp] = await Promise.all([getAgents(workspaceFilter), getTasks(workspaceFilter), getNodes(workspaceFilter)]);
      // Show all agents including orchestrator and decomposer
      setAgents(agentsResp.data);
      setTasks(tasksResp.data);
      setNodes(nodesResp.data);
      setLoading(false);

      // Fetch health for remote agents and workspace capacities for all agents
      agentsResp.data.forEach(async (a) => {
          if (a.is_remote) {
              try {
                  const h = await getAgentHealth(a.id);
                  setHealthData(prev => ({ ...prev, [a.id]: h.data }));
              } catch {
                setHealthData(prev => ({ ...prev, [a.id]: { status: 'error' } }));
              }
          }
          try {
            const cap = await getAgentWorkspaceCapacities(a.id);
            setWsCapacitiesPerAgent(prev => ({ ...prev, [a.id]: cap.data || {} }));
          } catch {
            // ignore — will fall back to agent.capacity
          }
      });
    } catch (error) {
      console.error('Error fetching data:', error);
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    fetchTools();
    if (!liveUpdates) return;
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [selectedWorkspace, liveUpdates]);

  // Sync agent order with fetched agents, restoring saved order from localStorage
  useEffect(() => {
    if (agents.length === 0) return;
    const key = `agent_order_${selectedWorkspace || 'default'}`;
    const saved = localStorage.getItem(key);
    let order = agents.map(a => a.id);
    if (saved) {
      try {
        const savedOrder = JSON.parse(saved);
        const knownIds = new Set(order);
        const filtered = savedOrder.filter(id => knownIds.has(id));
        const newIds = order.filter(id => !filtered.includes(id));
        order = [...filtered, ...newIds];
      } catch { /* ignore */ }
    }
    setAgentOrder(order);
  }, [agents, selectedWorkspace]);

  const handleDragStart = (idx) => setDragIdx(idx);

  const handleDragOver = (e, idx) => {
    e.preventDefault();
    setDragOverIdx(idx);
  };

  const handleDrop = (idx) => {
    if (dragIdx === null || dragIdx === idx) {
      setDragIdx(null);
      setDragOverIdx(null);
      return;
    }
    const newOrder = [...agentOrder];
    const [moved] = newOrder.splice(dragIdx, 1);
    newOrder.splice(idx, 0, moved);
    setAgentOrder(newOrder);
    const key = `agent_order_${selectedWorkspace || 'default'}`;
    localStorage.setItem(key, JSON.stringify(newOrder));
    setDragIdx(null);
    setDragOverIdx(null);
  };

  const handleDragEnd = () => {
    setDragIdx(null);
    setDragOverIdx(null);
  };

  const openWizard = () => {
    setWizardStep(1);
    setWizardType('creator');
    setWizardData({ id: '', name: '', description: '', domain: 'general', system_prompt: '', tools: ['read_file', 'write_file', 'list_files'], capacity: 1, agent_url: '', original_id: '' });
    setCreatorInput('');
    setCreatorStreaming(false);
    setCreatorOutput('');
    setCreatorDone(false);
    setCreatorError('');
    setCreatorModelInfo(null);
    getAgentModel('agent_flows')
      .then(r => setCreatorModelInfo(r.data))
      .catch(() => {});
    setShowWizard(true);
  };

  const closeWizard = () => {
    setShowWizard(false);
    setCreatorStreaming(false);
  };

  const wizardNext = () => setWizardStep(s => s + 1);
  const wizardBack = () => setWizardStep(s => s - 1);

  const handleCreatorSubmit = async () => {
    if (!creatorInput.trim()) return;
    setCreatorStreaming(true);
    setCreatorOutput('');
    setCreatorError('');
    setCreatorDone(false);

    try {
      // Ensure agent_flows node is running
      const nodesResp = await getNodes();
      const running = (nodesResp.data || []).filter(
        n => n.agent_id === 'agent_flows' && (n.status === 'running' || n.status === 'starting'),
      );
      if (running.length === 0) {
        await startNode({ agent_id: 'agent_flows', workspace: null });
        // Poll until node is running (max ~6 s)
        for (let i = 0; i < 6; i++) {
          await new Promise(r => setTimeout(r, 1000));
          const poll = await getNodes();
          const up = (poll.data || []).some(
            n => n.agent_id === 'agent_flows' && (n.status === 'running' || n.status === 'starting'),
          );
          if (up) break;
        }
      }

      const response = await fetch('http://localhost:8000/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: 'agent_flows',
          message: creatorInput.trim(),
          workspace: null,
          history: [],
          conversation_id: null,
          conversation_title: null,
          attachments: [],
        }),
      });

      if (!response.ok || !response.body) {
        const detail = await response.text();
        throw new Error(detail || 'Failed to contact Agent Creator');
      }

      const decoder = new TextDecoder();
      const reader = response.body.getReader();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split('\n\n');
        buffer = chunks.pop() || '';
        for (const chunk of chunks) {
          const line = chunk.split('\n').map(l => l.trim()).find(l => l.startsWith('data: '));
          if (!line) continue;
          let event = null;
          try { event = JSON.parse(line.slice(6)); } catch { continue; }
          if (!event?.type) continue;
          if (event.type === 'token' && event.token) {
            setCreatorOutput(prev => prev + event.token);
          }
        }
      }

      setCreatorDone(true);
      fetchData();
    } catch (err) {
      setCreatorError(err.message || 'Failed to create agent');
    } finally {
      setCreatorStreaming(false);
    }
  };

  const handleWizardSubmit = async () => {
    try {
      if (wizardType === 'creator') {
        if (creatorDone) { closeWizard(); return; }
        await handleCreatorSubmit();
        return;
      }
      let newAgentId;
      if (wizardType === 'custom') {
        await createCustomAgent({
          id: wizardData.id,
          name: wizardData.name,
          description: wizardData.description,
          domain: wizardData.domain,
          system_prompt: wizardData.system_prompt,
          tools: wizardData.tools,
          capacity: wizardData.capacity,
        });
        newAgentId = wizardData.id;
      } else if (wizardType === 'remote') {
        await connectAgent({
          id: wizardData.id,
          name: wizardData.name,
          description: wizardData.description,
          domain: wizardData.domain,
          agent_url: wizardData.agent_url,
          capacity: wizardData.capacity,
        });
        newAgentId = wizardData.id;
      } else if (wizardType === 'clone') {
        await cloneAgent({ original_id: wizardData.original_id, new_id: wizardData.id, new_name: wizardData.name });
        newAgentId = wizardData.id;
      }
      if (newAgentId && workspaceFilter) {
        await addAgentToWorkspace(selectedWorkspace, newAgentId);
      }
      closeWizard();
      fetchData();
    } catch (error) {
      alert('Error: ' + (error.response?.data?.detail || error.message));
    }
  };

  const toggleWizardTool = (toolName) => {
    setWizardData(prev => ({
      ...prev,
      tools: prev.tools.includes(toolName)
        ? prev.tools.filter(t => t !== toolName)
        : [...prev.tools, toolName],
    }));
  };

  const handleClone = async (e) => {
    e.preventDefault();
    try {
      await cloneAgent(cloneData);
      setShowCloneModal(false);
      fetchData();
    } catch (error) {
      alert('Error cloning agent: ' + (error.response?.data?.detail || error.message));
    }
  };

  const handleAssign = async (e) => {
    e.preventDefault();
    try {
      await assignAgent(assignData.task_id, { agent_id: assignData.agent_id });
      setShowAssignModal(false);
      fetchData();
    } catch (error) {
      alert('Error assigning task: ' + (error.response?.data?.detail || error.message));
    }
  };

  const handleConnect = async (e) => {
    e.preventDefault();
    try {
      await connectAgent(connectData);
      setShowConnectModal(false);
      fetchData();
    } catch (error) {
      alert('Error connecting agent: ' + (error.response?.data?.detail || error.message));
    }
  };

  const handleDisconnect = async (id) => {
    const isDefault = !selectedWorkspace || selectedWorkspace === 'default';
    const confirmMsg = isDefault
      ? 'Delete this agent from the system?'
      : 'Remove this agent from the current workspace?';
    if (!confirm(confirmMsg)) return;
    try {
      if (isDefault) {
        await disconnectAgent(id);
      } else {
        await removeAgentFromWorkspace(selectedWorkspace, id);
      }
      fetchData();
    } catch (error) {
      alert('Error: ' + (error.response?.data?.detail || error.message));
    }
  };

  const getAgentMetrics = (agent) => {
    const assignedTasks = tasks.filter(t => t.assigned_agent_type === agent.id);
    const activeTasks = assignedTasks.filter(t => t.agent_state === 'running');
    const used = activeTasks.length;
    const capacity = agent.capacity || 1;
    const loadFactor = (used / capacity) * 100;

    return {
        used,
        capacity,
        loadFactor,
        isFull: used >= capacity,
        activeTask: activeTasks[0],
        runningTasks: activeTasks.length,
        assignedTasks: assignedTasks.length,
    };
  };

  const getRunningNodeCount = (agentId) =>
    nodes.filter(n => n.agent_id === agentId && (n.status === 'running' || n.status === 'starting')).length;

  const handleStartNode = async (agentId) => {
    setStartingNode(agentId);
    try {
      const ws = selectedWorkspace && selectedWorkspace !== 'default' ? selectedWorkspace : null;
      await startNode({ agent_id: agentId, workspace: ws });
      navigate('/nodes');
    } catch (err) {
      alert('Failed to start node: ' + (err.response?.data?.detail || err.message));
    } finally {
      setStartingNode(null);
    }
  };

  return (
    <div className="space-y-8">
      {/* Header / Sub-Navbar */}
      <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 flex flex-col md:flex-row justify-between items-center gap-4">
        <div>
          <h2 className="text-2xl font-bold text-gray-800 flex items-center">
            <Shield className="w-6 h-6 mr-2 text-indigo-600" />
            Agent Cluster Manager
          </h2>
          <p className="text-gray-500 text-sm">Orchestrate your fleet of specialized AI nodes.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
            <Link
              to="/manifest"
              className="bg-gray-50 text-gray-700 border border-gray-200 px-4 py-2 rounded-lg flex items-center hover:bg-gray-100 transition-colors shadow-sm text-sm font-semibold"
            >
              <FileCode className="w-4 h-4 mr-2 text-indigo-500" />
              Apply YAML
            </Link>
            <button
              onClick={() => setShowConnectModal(true)}
              className="bg-gray-50 text-gray-700 border border-gray-200 px-4 py-2 rounded-lg flex items-center hover:bg-gray-100 transition-colors shadow-sm text-sm font-semibold"
            >
              <Server className="w-4 h-4 mr-2 text-emerald-500" />
              Connect Remote
            </button>
            <button
              onClick={openWizard}
              className="bg-indigo-600 text-white px-4 py-2 rounded-lg flex items-center hover:bg-indigo-700 transition-all shadow-md text-sm font-bold"
            >
              <Wand2 className="w-4 h-4 mr-2" />
              Create Agent
            </button>
        </div>
      </div>

      {loading ? (
        <div className="flex flex-col items-center justify-center py-20 bg-white rounded-xl border border-dashed border-gray-200">
           <RefreshCw className="w-8 h-8 text-indigo-400 animate-spin mb-4" />
           <p className="text-gray-500 font-medium">Scanning cluster for agent nodes...</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-4">
          {(agentOrder.length > 0 ? agentOrder.map(id => agents.find(a => a.id === id)).filter(Boolean) : agents).map((agent, idx) => {
            const metrics = getAgentMetrics(agent);
            const health = healthData[agent.id];
            const isHealthy = !agent.is_remote || (health && health.status === 'up');
            const nodeCount = getRunningNodeCount(agent.id);
            const isDefaultWs = !selectedWorkspace || selectedWorkspace === 'default';
            const agentWsCaps = wsCapacitiesPerAgent[agent.id] || {};
            const wsSessionCap = isDefaultWs ? Infinity : (agentWsCaps[selectedWorkspace] ?? 1);
            const nodesAtCap = !isDefaultWs && nodeCount >= wsSessionCap;
            const isDragging = dragIdx === idx;
            const isDragOver = dragOverIdx === idx && dragIdx !== idx;

            return (
              <div
                key={agent.id}
                draggable
                onDragStart={() => handleDragStart(idx)}
                onDragOver={(e) => handleDragOver(e, idx)}
                onDrop={() => handleDrop(idx)}
                onDragEnd={handleDragEnd}
                className={`bg-white rounded-lg border p-4 shadow-sm transition-all ${isDragging ? 'opacity-40 scale-95' : 'hover:shadow-md'} ${isDragOver ? 'border-indigo-400 shadow-md ring-2 ring-indigo-200' : 'border-gray-100'}`}
              >
                <div className="flex items-start justify-between gap-2 mb-3">
                  <GripVertical className="w-4 h-4 text-gray-300 cursor-grab flex-shrink-0 mt-0.5" />
                  <div className="flex items-start gap-2 min-w-0 flex-1">
                    <div className={`p-1.5 rounded ${isHealthy ? 'bg-indigo-50 text-indigo-600' : 'bg-red-50 text-red-600'}`}>
                      <Shield className="w-4 h-4" />
                    </div>
                    <div className="min-w-0">
                      <Link to={`/agents/${agent.id}`} className="text-sm font-semibold text-gray-900 hover:text-indigo-600 truncate block">
                        {agent.name}
                      </Link>
                      <div className="text-[11px] text-gray-400 truncate">{agent.id}</div>
                      <div className="text-[11px] text-gray-500 truncate mt-1">
                        {agent.description || 'No description provided'}
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <span className="text-[10px] bg-gray-100 text-gray-600 px-2 py-0.5 rounded uppercase font-semibold">
                      {agent.domain}
                    </span>
                    {agent.is_remote ? (
                      <span className={`text-[11px] font-semibold ${isHealthy ? 'text-green-700' : 'text-red-700'}`}>
                        {isHealthy ? 'Online' : 'Offline'}
                      </span>
                    ) : (
                      <Link to="/nodes" className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded ${
                        nodeCount > 0 ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                      }`}>
                        <Activity className={`w-3 h-3 ${nodeCount > 0 ? 'animate-pulse' : ''}`} />
                        {nodeCount > 0 ? `${nodeCount} running` : 'No nodes'}
                      </Link>
                    )}
                  </div>
                </div>

                {(() => {
                  const totalCap = wsSessionCap === Infinity
                    ? nodeCount * (agent.capacity || 1)
                    : Math.min(nodeCount * (agent.capacity || 1), wsSessionCap);
                  const sessionLoad = totalCap > 0 ? Math.min(100, Math.round((metrics.used / totalCap) * 100)) : 0;
                  const atCap = nodeCount > 0 && metrics.used >= totalCap;
                  return (
                    <div className="mb-3">
                      <div className="grid grid-cols-3 gap-2">
                        {/* Nodes card */}
                        <div className={`border rounded-lg px-2 py-2 ${nodesAtCap ? 'bg-orange-50 border-orange-300' : nodeCount > 0 ? 'bg-indigo-50 border-indigo-100' : 'bg-gray-50 border-gray-200'}`}>
                          <div className="flex items-center justify-between">
                            <div className={`text-[10px] uppercase tracking-wider font-semibold ${nodesAtCap ? 'text-orange-500' : nodeCount > 0 ? 'text-indigo-500' : 'text-gray-400'}`}>Nodes</div>
                            {nodesAtCap && <span className="text-[9px] font-bold text-white bg-orange-500 px-1 py-0.5 rounded leading-none animate-pulse">FULL</span>}
                          </div>
                          <div className={`text-sm font-semibold ${nodesAtCap ? 'text-orange-900' : nodeCount > 0 ? 'text-indigo-900' : 'text-gray-500'}`}>{nodeCount}/{isDefaultWs ? '∞' : wsSessionCap}</div>
                          <div className={`text-[10px] mt-0.5 ${nodesAtCap ? 'text-orange-600' : nodeCount > 0 ? 'text-indigo-600' : 'text-gray-400'}`}>running</div>
                        </div>

                        {/* Sessions card */}
                        <div className={`border rounded-lg px-2 py-2 ${atCap ? 'bg-red-50 border-red-300' : 'bg-green-50 border-green-100'}`}>
                          <div className="flex items-center justify-between">
                            <div className={`text-[10px] uppercase tracking-wider font-semibold ${atCap ? 'text-red-500' : 'text-green-500'}`}>Sessions</div>
                            {atCap && <span className="text-[9px] font-bold text-white bg-red-500 px-1 py-0.5 rounded leading-none animate-pulse">FULL</span>}
                          </div>
                          <div className={`text-sm font-semibold ${atCap ? 'text-red-900' : 'text-green-900'}`}>
                            {metrics.used}/{nodeCount > 0 ? totalCap : '—'}
                          </div>
                          <div className={`text-[10px] mt-0.5 ${atCap ? 'text-red-600 font-semibold' : 'text-green-700'}`}>
                            {nodeCount === 0 ? 'no nodes' : atCap ? 'At capacity' : `Load ${sessionLoad}%`}
                          </div>
                        </div>

                        {/* Running tasks card */}
                        <div className="bg-amber-50 border border-amber-100 rounded-lg px-2 py-2">
                          <div className="text-[10px] uppercase tracking-wider text-amber-500 font-semibold">Tasks</div>
                          <div className="text-sm font-semibold text-amber-900">{metrics.runningTasks}</div>
                          <div className="text-[10px] text-amber-600 mt-0.5">{metrics.assignedTasks} assigned</div>
                        </div>
                      </div>

                      {/* Progress bar */}
                      <div className="w-full bg-gray-200 rounded-full h-1.5 mt-2">
                        <div
                          className={`h-1.5 rounded-full ${atCap ? 'bg-red-500' : sessionLoad > 80 ? 'bg-orange-500' : 'bg-indigo-500'}`}
                          style={{ width: `${nodeCount > 0 ? sessionLoad : 0}%` }}
                        />
                      </div>
                      <div className="flex items-center justify-between text-[10px] text-gray-500 mt-1">
                        <span>{nodeCount > 0 ? `Load ${sessionLoad}%` : 'No nodes running'}</span>
                        <span>{metrics.runningTasks} running / {metrics.assignedTasks} assigned</span>
                      </div>
                    </div>
                  );
                })()}

                <div className="flex flex-wrap gap-1 mb-3 min-h-[22px]">
                  {(agent.tools || []).slice(0, 3).map((tool) => (
                    <span key={tool} className="text-[10px] bg-white border border-gray-200 px-1.5 py-0.5 rounded text-gray-600">
                      {tool}
                    </span>
                  ))}
                  {(agent.tools || []).length > 3 && (
                    <span className="text-[10px] text-gray-400">+{(agent.tools || []).length - 3}</span>
                  )}
                </div>

                <div className="flex items-center gap-2">
                  {!agent.is_remote && (
                    <button
                      onClick={() => handleStartNode(agent.id)}
                      disabled={startingNode === agent.id || nodesAtCap}
                      title={nodesAtCap ? 'Node limit reached for this workspace' : undefined}
                      className="flex-1 inline-flex items-center justify-center px-2 py-1.5 text-xs font-semibold bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {startingNode === agent.id
                        ? <RefreshCw className="w-3.5 h-3.5 mr-1 animate-spin" />
                        : <Play className="w-3.5 h-3.5 mr-1" />}
                      Start
                    </button>
                  )}
                  <button
                    onClick={() => {
                      setCloneData({ ...cloneData, original_id: agent.id, new_id: `${agent.id}-clone`, new_name: `${agent.name} (Clone)` });
                      setShowCloneModal(true);
                    }}
                    className="p-1.5 border border-gray-200 text-gray-500 rounded hover:bg-gray-50 hover:text-indigo-600 transition-colors"
                    title="Clone Node"
                  >
                    <Copy className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => handleDisconnect(agent.id)}
                    className="p-1.5 border border-gray-200 text-gray-500 rounded hover:bg-red-50 hover:text-red-600 transition-colors"
                    title={!selectedWorkspace || selectedWorkspace === 'default' ? 'Delete Agent' : 'Remove from Workspace'}
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Modals remain similarly structured but with updated fields for domain/description */}

      {/* Clone Agent Modal */}
      {showCloneModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50 backdrop-blur-sm">
          <div className="bg-white rounded-xl max-w-md w-full p-6 shadow-2xl border border-gray-100">
            <h3 className="text-xl font-bold mb-4 flex items-center">
                <Copy className="w-5 h-5 mr-2 text-indigo-600" />
                Clone Agent Node
            </h3>
            <form onSubmit={handleClone}>
              <div className="space-y-4 mb-6">
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Source Agent</label>
                  <select
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                    value={cloneData.original_id}
                    onChange={(e) => setCloneData({ ...cloneData, original_id: e.target.value })}
                  >
                    {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase mb-1">New Node ID</label>
                  <input
                    type="text" required
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                    value={cloneData.new_id}
                    onChange={(e) => setCloneData({ ...cloneData, new_id: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Display Name</label>
                  <input
                    type="text" required
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                    value={cloneData.new_name}
                    onChange={(e) => setCloneData({ ...cloneData, new_name: e.target.value })}
                  />
                </div>
              </div>
              <div className="flex justify-end space-x-3">
                <button type="button" onClick={() => setShowCloneModal(false)} className="px-4 py-2 text-gray-500 text-sm font-medium">Cancel</button>
                <button type="submit" className="bg-indigo-600 text-white px-6 py-2 rounded-md hover:bg-indigo-700 font-bold shadow-md">Deploy Clone</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Connect Remote Agent Modal */}
      {showConnectModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50 backdrop-blur-sm">
          <div className="bg-white rounded-xl max-w-md w-full p-6 shadow-2xl">
            <h3 className="text-xl font-bold mb-4 flex items-center">
                <Server className="w-5 h-5 mr-2 text-emerald-600" />
                Connect External Node
            </h3>
            <form onSubmit={handleConnect}>
              <div className="space-y-4 mb-6">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">ID</label>
                    <input
                      type="text" required className="w-full border rounded px-3 py-2 text-sm"
                      value={connectData.id}
                      onChange={(e) => setConnectData({ ...connectData, id: e.target.value })}
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Domain</label>
                    <input
                      type="text" required className="w-full border rounded px-3 py-2 text-sm"
                      value={connectData.domain}
                      onChange={(e) => setConnectData({ ...connectData, domain: e.target.value })}
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Display Name</label>
                  <input
                    type="text" required className="w-full border rounded px-3 py-2 text-sm"
                    value={connectData.name}
                    onChange={(e) => setConnectData({ ...connectData, name: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Description</label>
                  <textarea
                    className="w-full border rounded px-3 py-2 text-sm" rows="2"
                    value={connectData.description}
                    onChange={(e) => setConnectData({ ...connectData, description: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Agent Endpoint URL</label>
                  <input
                    type="url" required className="w-full border rounded px-3 py-2 text-sm"
                    value={connectData.agent_url}
                    onChange={(e) => setConnectData({ ...connectData, agent_url: e.target.value })}
                    placeholder="https://agent-service.internal/api"
                  />
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Capacity Slots</label>
                  <input
                    type="number" min="1" required className="w-full border rounded px-3 py-2 text-sm"
                    value={connectData.capacity}
                    onChange={(e) => setConnectData({ ...connectData, capacity: parseInt(e.target.value) })}
                  />
                </div>
              </div>
              <div className="flex justify-end space-x-3">
                <button type="button" onClick={() => setShowConnectModal(false)} className="px-4 py-2 text-sm text-gray-500">Cancel</button>
                <button type="submit" className="bg-emerald-600 text-white px-6 py-2 rounded-md font-bold shadow-md">Establish Connection</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Agent Creation Wizard */}
      {showWizard && (() => {
        const STEPS =
          wizardType === 'creator' ? ['Type', 'Launch'] :
          wizardType === 'clone'   ? ['Type', 'Source', 'Identity', 'Review'] :
                                     ['Type', 'Identity', 'Capabilities', 'Review'];
        const totalSteps = STEPS.length;
        const isLastStep = wizardStep === totalSteps;

        const domainOptions = ['general', 'development', 'orchestration', 'testing', 'data', 'research', 'devops'];

        const toolList = availableTools.filter((t, i, arr) => arr.findIndex(x => x.name === t.name) === i);

        return (
          <div className="fixed inset-0 bg-black bg-opacity-60 flex items-center justify-center p-4 z-50 backdrop-blur-sm">
            <div className="bg-white rounded-2xl w-full max-w-lg shadow-2xl border border-gray-100 flex flex-col max-h-[90vh]">
              {/* Header */}
              <div className="p-6 pb-4 border-b border-gray-100">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-xl font-bold flex items-center text-gray-900">
                    <Wand2 className="w-5 h-5 mr-2 text-indigo-600" />
                    Create Agent
                  </h3>
                  <button onClick={closeWizard} className="text-gray-400 hover:text-gray-600 text-lg leading-none">&times;</button>
                </div>
                {/* Step indicators */}
                <div className="flex items-center gap-1">
                  {STEPS.map((label, i) => {
                    const stepNum = i + 1;
                    const done = wizardStep > stepNum;
                    const active = wizardStep === stepNum;
                    return (
                      <div key={label} className="flex items-center gap-1 flex-1">
                        <div className={`flex items-center gap-1.5 ${active ? 'text-indigo-600' : done ? 'text-green-600' : 'text-gray-400'}`}>
                          <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold border-2 transition-colors ${
                            active ? 'border-indigo-600 bg-indigo-50 text-indigo-600' :
                            done ? 'border-green-500 bg-green-50 text-green-600' :
                            'border-gray-300 bg-white text-gray-400'
                          }`}>
                            {done ? <Check className="w-3 h-3" /> : stepNum}
                          </div>
                          <span className="text-[11px] font-semibold hidden sm:block">{label}</span>
                        </div>
                        {i < STEPS.length - 1 && <div className={`flex-1 h-0.5 mx-1 rounded ${done ? 'bg-green-400' : 'bg-gray-200'}`} />}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Body */}
              <div className="flex-1 overflow-y-auto p-6 space-y-4">

                {/* Step 1 — Type selection */}
                {wizardStep === 1 && (
                  <div className="space-y-3">
                    <p className="text-sm text-gray-500">Choose how you want to create the agent:</p>
                    {[
                      { value: 'creator', icon: Sparkles, label: 'Use Agent Creator', desc: 'Let the built-in Agent Creator AI guide you through designing and provisioning an agent via chat.' },
                      { value: 'custom', icon: Box, label: 'Custom Agent', desc: 'Manually define a system prompt and tools. Runs locally using the LangChain runtime.' },
                      { value: 'remote', icon: Globe, label: 'Remote HTTP Agent', desc: 'Connect an external agent accessible via an HTTP endpoint.' },
                      { value: 'clone', icon: GitBranch, label: 'Clone Existing', desc: 'Duplicate an existing agent as a starting point.' },
                    ].map(({ value, icon: Icon, label, desc }) => (
                      <button
                        key={value}
                        type="button"
                        onClick={() => setWizardType(value)}
                        className={`w-full text-left p-4 rounded-xl border-2 transition-all flex items-start gap-3 ${
                          wizardType === value ? 'border-indigo-500 bg-indigo-50' : 'border-gray-200 hover:border-gray-300 bg-white'
                        }`}
                      >
                        <Icon className={`w-5 h-5 mt-0.5 flex-shrink-0 ${wizardType === value ? 'text-indigo-600' : 'text-gray-400'}`} />
                        <div>
                          <div className={`text-sm font-semibold ${wizardType === value ? 'text-indigo-700' : 'text-gray-700'}`}>{label}</div>
                          <div className="text-xs text-gray-500 mt-0.5">{desc}</div>
                        </div>
                        {wizardType === value && <Check className="w-4 h-4 text-indigo-600 ml-auto mt-0.5" />}
                      </button>
                    ))}
                  </div>
                )}

                {/* Step 2 (creator) — inline chat */}
                {wizardStep === 2 && wizardType === 'creator' && (
                  <div className="space-y-4">
                    {/* Model info — always visible */}
                    <div className="flex items-center gap-3 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
                      <Sparkles className="w-4 h-4 text-indigo-400 flex-shrink-0" />
                      <div className="min-w-0">
                        <div className="text-xs font-bold uppercase tracking-wide text-gray-400 mb-0.5">Model</div>
                        <div className="text-sm text-gray-700 font-medium truncate">
                          {creatorModelInfo?.provider && creatorModelInfo.provider !== 'inherit'
                            ? `${creatorModelInfo.provider}${creatorModelInfo.model ? ` / ${creatorModelInfo.model}` : ''}`
                            : 'Inherits global settings'}
                        </div>
                      </div>
                    </div>

                    {/* Form + behaviour hint — hidden once submitted */}
                    {!creatorStreaming && !creatorDone && (
                      <>
                        <div>
                          <label className="block text-xs font-bold text-gray-500 uppercase mb-1">
                            Describe the agent you need <span className="text-red-500">*</span>
                          </label>
                          <textarea
                            rows={5}
                            placeholder="e.g. A code reviewer that reads Python files, checks for bugs and style issues, and writes a summary report."
                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 resize-none"
                            value={creatorInput}
                            onChange={e => setCreatorInput(e.target.value)}
                          />
                        </div>

                        <div className="rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3 space-y-1.5">
                          <p className="text-xs font-semibold text-indigo-700">What the Agent Creator can do:</p>
                          <ul className="list-disc list-inside text-xs text-indigo-600 space-y-0.5">
                            <li>Design a system prompt tailored to your requirements</li>
                            <li>Select the right tools from the available set</li>
                            <li>Register the new agent directly in the system</li>
                            <li>List or inspect existing agents on request</li>
                          </ul>
                        </div>
                      </>
                    )}

                    {/* Streaming / result output */}
                    {(creatorStreaming || creatorOutput) && (
                      <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                        <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-gray-400 mb-2">
                          <span>Agent Creator</span>
                          {creatorStreaming && <RefreshCw className="w-3 h-3 animate-spin text-indigo-400" />}
                          {creatorDone && <Check className="w-3 h-3 text-green-500" />}
                        </div>
                        <p className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed max-h-48 overflow-y-auto">
                          {creatorOutput || '…'}
                        </p>
                      </div>
                    )}

                    {/* Error */}
                    {creatorError && (
                      <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                        {creatorError}
                      </div>
                    )}

                    {/* Success */}
                    {creatorDone && (
                      <div className="rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-sm font-semibold text-green-700">
                        Done — check the agent list for your new agent.
                      </div>
                    )}

                    <p className="text-xs text-gray-400">Agent Creator runs in the default workspace.</p>
                  </div>
                )}

                {/* Step 2 (clone) — Source agent */}
                {wizardStep === 2 && wizardType === 'clone' && (
                  <div className="space-y-4">
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Source Agent</label>
                      <select
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                        value={wizardData.original_id}
                        onChange={e => setWizardData(d => ({ ...d, original_id: e.target.value }))}
                      >
                        <option value="">-- Select agent to clone --</option>
                        {agents.map(a => <option key={a.id} value={a.id}>{a.name} ({a.id})</option>)}
                      </select>
                    </div>
                  </div>
                )}

                {/* Step 2 (non-clone, non-creator) / Step 3 (clone) — Identity */}
                {((wizardStep === 2 && wizardType !== 'clone' && wizardType !== 'creator') || (wizardStep === 3 && wizardType === 'clone')) && (
                  <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Agent ID <span className="text-red-500">*</span></label>
                        <input
                          type="text" required placeholder="e.g. code_reviewer"
                          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                          value={wizardData.id}
                          onChange={e => setWizardData(d => ({ ...d, id: e.target.value.replace(/\s/g, '_').toLowerCase() }))}
                        />
                        <p className="text-[10px] text-gray-400 mt-1">Lowercase, no spaces</p>
                      </div>
                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Domain</label>
                        <select
                          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                          value={wizardData.domain}
                          onChange={e => setWizardData(d => ({ ...d, domain: e.target.value }))}
                        >
                          {domainOptions.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                        </select>
                      </div>
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Display Name <span className="text-red-500">*</span></label>
                      <input
                        type="text" required placeholder="e.g. Code Reviewer"
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                        value={wizardData.name}
                        onChange={e => setWizardData(d => ({ ...d, name: e.target.value }))}
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Description</label>
                      <textarea
                        rows="2" placeholder="What does this agent do?"
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                        value={wizardData.description}
                        onChange={e => setWizardData(d => ({ ...d, description: e.target.value }))}
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Capacity Slots</label>
                      <input
                        type="number" min="1"
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                        value={wizardData.capacity}
                        onChange={e => setWizardData(d => ({ ...d, capacity: parseInt(e.target.value) || 1 }))}
                      />
                    </div>
                  </div>
                )}

                {/* Step 3 (non-clone) — Capabilities */}
                {wizardStep === 3 && wizardType !== 'clone' && (
                  <div className="space-y-4">
                    {wizardType === 'custom' && (
                      <>
                        <div>
                          <label className="block text-xs font-bold text-gray-500 uppercase mb-1">System Prompt <span className="text-red-500">*</span></label>
                          <textarea
                            rows="5" placeholder="You are a specialized agent for..."
                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                            value={wizardData.system_prompt}
                            onChange={e => setWizardData(d => ({ ...d, system_prompt: e.target.value }))}
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-bold text-gray-500 uppercase mb-2">Tools</label>
                          <select
                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 bg-white"
                            value=""
                            onChange={e => { if (e.target.value) toggleWizardTool(e.target.value); }}
                          >
                            <option value="">— Add a tool —</option>
                            {toolList.filter(t => !wizardData.tools.includes(t.name)).map(t => (
                              <option key={t.name} value={t.name}>{t.name}{t.description ? ` — ${t.description}` : ''}</option>
                            ))}
                          </select>
                          {wizardData.tools.length > 0 && (
                            <div className="flex flex-wrap gap-1.5 mt-2">
                              {wizardData.tools.map(name => (
                                <span key={name} className="inline-flex items-center gap-1 bg-indigo-50 border border-indigo-200 text-indigo-700 text-xs px-2 py-1 rounded-full">
                                  {name}
                                  <button type="button" onClick={() => toggleWizardTool(name)} className="text-indigo-400 hover:text-indigo-700 leading-none">×</button>
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      </>
                    )}
                    {wizardType === 'remote' && (
                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Agent Endpoint URL <span className="text-red-500">*</span></label>
                        <input
                          type="url" required placeholder="https://agent-service.internal/api"
                          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                          value={wizardData.agent_url}
                          onChange={e => setWizardData(d => ({ ...d, agent_url: e.target.value }))}
                        />
                      </div>
                    )}
                  </div>
                )}

                {/* Last step — Review (not shown for creator, which has its own output panel) */}
                {isLastStep && wizardType !== 'creator' && (
                  <div className="space-y-3">
                    <p className="text-sm text-gray-500">Review your agent before creating it:</p>
                    <div className="bg-gray-50 rounded-xl p-4 space-y-2 text-sm">
                      <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">Type</span><span className="font-semibold capitalize text-gray-800">{wizardType}</span></div>
                      <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">ID</span><span className=" text-gray-800">{wizardData.id || wizardData.original_id}</span></div>
                      <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">Name</span><span className="text-gray-800">{wizardData.name}</span></div>
                      {wizardData.description && <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">Description</span><span className="text-gray-700">{wizardData.description}</span></div>}
                      <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">Domain</span><span className="text-gray-800">{wizardData.domain}</span></div>
                      <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">Capacity</span><span className="text-gray-800">{wizardData.capacity} slot{wizardData.capacity !== 1 ? 's' : ''}</span></div>
                      {wizardType === 'custom' && wizardData.tools.length > 0 && (
                        <div className="flex gap-2">
                          <span className="text-gray-400 w-24 flex-shrink-0">Tools</span>
                          <div className="flex flex-wrap gap-1">
                            {wizardData.tools.map(t => <span key={t} className="text-[11px] bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded">{t}</span>)}
                          </div>
                        </div>
                      )}
                      {wizardType === 'remote' && <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">URL</span><span className=" text-gray-700 break-all">{wizardData.agent_url}</span></div>}
                      {wizardType === 'clone' && <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">Source</span><span className=" text-gray-800">{wizardData.original_id}</span></div>}
                      {wizardType === 'custom' && wizardData.system_prompt && (
                        <div className="flex gap-2 flex-col">
                          <span className="text-gray-400">System Prompt</span>
                          <p className="text-gray-700 text-xs bg-white border border-gray-200 rounded-lg p-2 whitespace-pre-wrap max-h-24 overflow-y-auto">{wizardData.system_prompt}</p>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>

              {/* Footer */}
              <div className="p-6 pt-4 border-t border-gray-100 flex justify-between items-center">
                <button
                  type="button"
                  onClick={wizardStep === 1 ? closeWizard : wizardBack}
                  className="flex items-center gap-1.5 px-4 py-2 text-sm text-gray-500 hover:text-gray-700 font-medium"
                >
                  {wizardStep === 1 ? 'Cancel' : <><ChevronLeft className="w-4 h-4" />Back</>}
                </button>
                <button
                  type="button"
                  onClick={isLastStep ? handleWizardSubmit : wizardNext}
                  disabled={
                    creatorStreaming ||
                    (wizardStep === 2 && wizardType === 'creator' && !creatorDone && !creatorInput.trim()) ||
                    (wizardStep === 2 && wizardType === 'clone' && !wizardData.original_id) ||
                    (wizardStep === 2 && wizardType !== 'clone' && wizardType !== 'creator' && (!wizardData.id || !wizardData.name)) ||
                    (wizardStep === 3 && wizardType === 'clone' && (!wizardData.id || !wizardData.name)) ||
                    (wizardStep === 3 && wizardType === 'custom' && !wizardData.system_prompt) ||
                    (wizardStep === 3 && wizardType === 'remote' && !wizardData.agent_url)
                  }
                  className="flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm font-bold bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shadow-sm"
                >
                  {isLastStep
                    ? wizardType === 'creator'
                      ? creatorDone
                        ? 'Close'
                        : creatorStreaming
                          ? <><RefreshCw className="w-4 h-4 animate-spin" />Creating…</>
                          : <><Sparkles className="w-4 h-4" />Create Agent</>
                      : 'Create Agent'
                    : <>Next<ChevronRight className="w-4 h-4" /></>
                  }
                </button>
              </div>
            </div>
          </div>
        );
      })()}

      {/* Assign Task Modal */}
      {showAssignModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50 backdrop-blur-sm">
          <div className="bg-white rounded-xl max-w-md w-full p-6 shadow-2xl">
            <h3 className="text-xl font-bold mb-4 flex items-center">
                <Play className="w-5 h-5 mr-2 text-indigo-600" />
                Schedule Task on Node
            </h3>
            <form onSubmit={handleAssign}>
              <div className="mb-6">
                <label className="block text-xs font-bold text-gray-500 uppercase mb-2">Select Target Task</label>
                <select
                  className="w-full border border-gray-300 rounded-md px-3 py-3 text-sm focus:ring-2 focus:ring-indigo-500"
                  value={assignData.task_id}
                  onChange={(e) => setAssignData({ ...assignData, task_id: e.target.value })}
                  required
                >
                  <option value="">-- Choose Task --</option>
                  {tasks.filter(t => t.agent_state !== 'running').map(t => (
                    <option key={t.id} value={t.id}>{t.title} ({t.id.slice(0,8)})</option>
                  ))}
                </select>
              </div>
              <div className="flex justify-end space-x-3">
                <button type="button" onClick={() => setShowAssignModal(false)} className="px-4 py-2 text-sm text-gray-500">Cancel</button>
                <button type="submit" disabled={!assignData.task_id} className="bg-indigo-600 text-white px-6 py-2 rounded-md font-bold shadow-md disabled:opacity-50">Start Execution</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default AgentManager;
