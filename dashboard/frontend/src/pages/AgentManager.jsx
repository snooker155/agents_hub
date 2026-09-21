import { useState, useEffect, useCallback, useMemo } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch } from '../components/stream';
import {
  Copy,
  Play,
  Radio,
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
  Lock,
  Download,
  AlertTriangle,
  CheckCircle2,
  Users,
  Share2,
} from 'lucide-react';
import {
  getAgents,
  getAgentTools,
  getTasks,
  getNodes,
  assignAgent,
  createCustomAgent,
  disconnectAgent,
  startNode,
  getAgentWorkspaceCapacities,
  getAgentModel,
  removeAgentFromWorkspace,
  addAgentToWorkspace,
  getInstancesSummary,
} from '../api';
import ImportAgentModal from '../components/ImportAgentModal';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
const AgentManager = () => {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { selectedWorkspace, workspaceFilter, liveUpdates } = useWorkspace();
  const [agents, setAgents] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [nodes, setNodes] = useState([]);
  // Live copies per agent — a node is a carrier that *can* run this agent; an
  // instance is a copy that actually is. The card shows both.
  const [instanceCounts, setInstanceCounts] = useState({});
  const [loading, setLoading] = useState(true);
  const [showAssignModal, setShowAssignModal] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  const [showWizard, setShowWizard] = useState(false);
  const [wizardStep, setWizardStep] = useState(1);
  const [wizardType, setWizardType] = useState('custom'); // 'custom' | 'remote' | 'clone'
  const [wizardData, setWizardData] = useState({
    id: '', name: '', description: '', domain: 'general',
    system_prompt: '', tools: ['read_file', 'write_file', 'list_files'], capacity: 1,
    agent_url: '', original_id: '',
  });
  const [availableTools, setAvailableTools] = useState([]);
  const [assignData, setAssignData] = useState({ task_id: '', agent_id: '' });
  const [startingNode, setStartingNode] = useState(null);
  const [wsCapacitiesPerAgent, setWsCapacitiesPerAgent] = useState({});
  const [agentOrder, setAgentOrder] = useState([]);
  const [dragId, setDragId] = useState(null);
  const [dragOverId, setDragOverId] = useState(null);
  // The system agents are the hub's own staff — the orchestrator, the
  // builders, the Visualizer. They are in every workspace and cannot be
  // deleted, so once a fleet has a few agents of its own they are mostly
  // noise on this page. The choice sticks, because it is about how someone
  // works rather than about what they are looking at right now.
  const [showSystem, setShowSystem] = useState(
    () => localStorage.getItem('agents_show_system') !== 'false',
  );

  const toggleSystem = (next) => {
    setShowSystem(next);
    localStorage.setItem('agents_show_system', next ? 'true' : 'false');
  };

  // Creator chat state
  const [creatorInput, setCreatorInput] = useState('');
  const [creatorStreaming, setCreatorStreaming] = useState(false);
  const [creatorOutput, setCreatorOutput] = useState('');
  const [creatorDone, setCreatorDone] = useState(false);
  const [creatorError, setCreatorError] = useState('');
  const [creatorModelInfo, setCreatorModelInfo] = useState(null);

  const fetchTools = useCallback(async () => {
    try {
      const res = await getAgentTools();
      setAvailableTools(res.data?.all || []);
    } catch {
      /* the tool list stays as it was */
    }
  }, []);

  const fetchData = useCallback(async () => {
    try {
      const [agentsResp, tasksResp, nodesResp] = await Promise.all([getAgents(workspaceFilter), getTasks(workspaceFilter), getNodes(workspaceFilter)]);
      // Show all agents including orchestrator and decomposer
      setAgents(agentsResp.data);
      setTasks(tasksResp.data);
      setNodes(nodesResp.data);
      setLoading(false);

      // Fetch workspace capacities for all agents
      agentsResp.data.forEach(async (a) => {
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
  }, [workspaceFilter]);

  const fetchInstanceCounts = useCallback(() => {
    getInstancesSummary({ workspace: workspaceFilter })
      .then(r => setInstanceCounts(r.data?.by_agent || {}))
      .catch(() => setInstanceCounts({}));
  }, [workspaceFilter]);

  useEffect(() => {
    fetchData();
    fetchTools();
    fetchInstanceCounts();
  }, [selectedWorkspace, liveUpdates, fetchData, fetchTools, fetchInstanceCounts]);
  useLiveRefetch(fetchData, { type: 'agents.changed', enabled: liveUpdates });
  // One grouped count query per change, not one per agent card.
  useLiveRefetch(fetchInstanceCounts, { type: 'instances.delta', enabled: liveUpdates });
  useLiveRefetch(fetchInstanceCounts, { type: 'instances.changed', enabled: liveUpdates });

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
      } catch { /* a corrupt saved order falls back to the default */ }
    }
    setAgentOrder(order);
  }, [agents, selectedWorkspace]);

  const handleDragStart = (id) => setDragId(id);

  const handleDragOver = (e, id) => {
    e.preventDefault();
    setDragOverId(id);
  };

  // By id rather than by position: the grid can be showing a subset of the
  // stored order (system agents hidden), so a card's index in it is not its
  // index in the order being rewritten.
  const handleDrop = (targetId) => {
    if (dragId === null || dragId === targetId) {
      setDragId(null);
      setDragOverId(null);
      return;
    }
    const newOrder = agentOrder.filter((id) => id !== dragId);
    const at = newOrder.indexOf(targetId);
    newOrder.splice(at === -1 ? newOrder.length : at, 0, dragId);
    setAgentOrder(newOrder);
    const key = `agent_order_${selectedWorkspace || 'default'}`;
    localStorage.setItem(key, JSON.stringify(newOrder));
    setDragId(null);
    setDragOverId(null);
  };

  const handleDragEnd = () => {
    setDragId(null);
    setDragOverId(null);
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
    getAgentModel('agent_creator')
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
      const response = await fetch('http://localhost:8000/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: 'agent_creator',
          message: creatorInput.trim(),
          workspace: selectedWorkspace || null,
          history: [],
          conversation_id: null,
          conversation_title: null,
          attachments: [],
        }),
      });

      if (!response.ok || !response.body) {
        const detail = await response.text();
        throw new Error(detail || t('agentManager.creatorFailed'));
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
      setCreatorError(err.message || t('agentManager.createFailed'));
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
      await createCustomAgent({
        id: wizardData.id,
        name: wizardData.name,
        description: wizardData.description,
        domain: wizardData.domain,
        system_prompt: wizardData.system_prompt,
        tools: wizardData.tools,
        capacity: wizardData.capacity,
        workspace: selectedWorkspace || null,
      });
      const newAgentId = wizardData.id;
      // The backend already registers the agent in its owning workspace's
      // allowed_agents when a non-default workspace is passed above; this call
      // is a no-op safety net for the default workspace.
      if (newAgentId && workspaceFilter) {
        await addAgentToWorkspace(selectedWorkspace, newAgentId);
      }
      closeWizard();
      fetchData();
    } catch (error) {
      alert(`${t('common.error')}: ` + (error.response?.data?.detail || error.message));
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

  const handleAssign = async (e) => {
    e.preventDefault();
    try {
      await assignAgent(assignData.task_id, { agent_id: assignData.agent_id });
      setShowAssignModal(false);
      fetchData();
    } catch (error) {
      alert(`${t('agentManager.assignTaskError')}: ` + (error.response?.data?.detail || error.message));
    }
  };

  const handleDisconnect = async (id) => {
    const isDefault = !selectedWorkspace || selectedWorkspace === 'default';
    const confirmMsg = isDefault
      ? t('agentManager.confirmDeleteSystem')
      : t('agentManager.confirmRemoveWorkspace');
    if (!confirm(confirmMsg)) return;
    try {
      if (isDefault) {
        await disconnectAgent(id);
      } else {
        await removeAgentFromWorkspace(selectedWorkspace, id);
      }
      fetchData();
    } catch (error) {
      alert(`${t('common.error')}: ` + (error.response?.data?.detail || error.message));
    }
  };

  // The grid's contents: the saved order, minus what the filter hides.
  const orderedAgents = useMemo(() => (
    agentOrder.length > 0
      ? agentOrder.map((id) => agents.find((a) => a.id === id)).filter(Boolean)
      : agents
  ), [agentOrder, agents]);
  const visibleAgents = useMemo(
    () => (showSystem ? orderedAgents : orderedAgents.filter((a) => !a.system)),
    [orderedAgents, showSystem],
  );
  const systemCount = useMemo(() => agents.filter((a) => a.system).length, [agents]);

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

  const getLiveInstanceCount = (agentId) => instanceCounts[agentId]?.live || 0;

  const handleStartNode = async (agentId) => {
    setStartingNode(agentId);
    try {
      const ws = selectedWorkspace && selectedWorkspace !== 'default' ? selectedWorkspace : null;
      await startNode({ agent_id: agentId, workspace: ws });
      navigate('/nodes');
    } catch (err) {
      alert(`${t('agentManager.startNodeError')}: ` + (err.response?.data?.detail || err.message));
    } finally {
      setStartingNode(null);
    }
  };

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={Users}
        title={t('agentManager.agents')}
        description={t('agentManager.orchestrateYourFleetOfSpecialized')}
        actions={<>
          <label
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 cursor-pointer select-none"
            title={t('agentManager.showSystemAgentsHint')}
          >
            <input
              type="checkbox"
              checked={showSystem}
              onChange={(e) => toggleSystem(e.target.checked)}
              className="w-3.5 h-3.5 accent-indigo-600 cursor-pointer"
            />
            {t('agentManager.showSystemAgents')}
            {systemCount > 0 && <span className="text-gray-400">({systemCount})</span>}
          </label>
          <button
            onClick={() => setShowImportModal(true)}
            className="flex items-center gap-2 px-4 py-2 border border-indigo-200 text-indigo-700 rounded-lg hover:bg-indigo-50 text-sm font-medium transition-colors"
            title={t('agentManager.importAnAgentThatAlready')}
          >
            <Download className="w-4 h-4" /> {t('agentManager.importFromRepo')}
          </button>
          {/* The other direction, and it belongs here because this is where
              someone stands when they think "the agent I want is not in this
              list, and it is not going to move into it either". */}
          <Link
            to="/connections"
            className="flex items-center gap-2 px-4 py-2 border border-indigo-200 text-indigo-700 rounded-lg hover:bg-indigo-50 text-sm font-medium transition-colors"
            title={t('agentManager.connectExternalHint')}
          >
            <Share2 className="w-4 h-4" /> {t('agentManager.connectExternal')}
          </Link>
          <button
            onClick={openWizard}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 text-sm font-medium transition-colors"
          >
            <Wand2 className="w-4 h-4" /> {t('agentManager.createAgent')}
          </button>
        </>}
      />

      {loading ? (
        <div className="flex flex-col items-center justify-center py-20 bg-white rounded-xl border border-dashed border-gray-200">
           <RefreshCw className="w-8 h-8 text-indigo-400 animate-spin mb-4" />
           <p className="text-gray-500 font-medium">{t('agentManager.scanningClusterForAgentNodes')}</p>
        </div>
      ) : visibleAgents.length === 0 && orderedAgents.length > 0 ? (
        // Every agent here is a system one and the filter is off: say so, or
        // the page reads as an empty fleet.
        <div className="py-16 text-center bg-white rounded-xl border border-dashed border-gray-200">
          <p className="text-sm text-gray-500">{t('agentManager.allHiddenBySystemFilter')}</p>
          <button
            onClick={() => toggleSystem(true)}
            className="mt-2 text-sm font-medium text-indigo-600 hover:text-indigo-700"
          >
            {t('agentManager.showSystemAgents')}
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-4">
          {visibleAgents.map((agent) => {
            const metrics = getAgentMetrics(agent);
            const isHealthy = true;
            const nodeCount = getRunningNodeCount(agent.id);
            const liveInstances = getLiveInstanceCount(agent.id);
            const isDefaultWs = !selectedWorkspace || selectedWorkspace === 'default';
            const agentWsCaps = wsCapacitiesPerAgent[agent.id] || {};
            const wsSessionCap = isDefaultWs ? Infinity : (agentWsCaps[selectedWorkspace] ?? 1);
            const nodesAtCap = !isDefaultWs && nodeCount >= wsSessionCap;
            const isDragging = dragId === agent.id;
            const isDragOver = dragOverId === agent.id && dragId !== agent.id;

            return (
              <div
                key={agent.id}
                draggable
                onDragStart={() => handleDragStart(agent.id)}
                onDragOver={(e) => handleDragOver(e, agent.id)}
                onDrop={() => handleDrop(agent.id)}
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
                      <div className="text-[11px] text-gray-400 truncate flex items-center gap-1">
                        {agent.id}
                        {agent.definition_id && agent.definition_id !== agent.id && (
                          <span
                            className="inline-flex items-center text-[9px] bg-indigo-50 text-indigo-500 px-1 py-0.5 rounded uppercase font-semibold"
                            title={`Shares the "${agent.definition_id}" definition (prompt/tools)`}
                          >
                            <Copy className="w-2.5 h-2.5 mr-0.5" />
                            {agent.definition_id}
                          </span>
                        )}
                      </div>
                      <div className="text-[11px] text-gray-500 truncate mt-1">
                        {agent.description || t('agentManager.noDescription')}
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    {agent.system && (
                      <span
                        className="inline-flex items-center gap-1 text-[10px] bg-indigo-50 text-indigo-700 border border-indigo-200 px-2 py-0.5 rounded uppercase font-semibold"
                        title={t('agentManager.systemBadgeHint')}
                      >
                        <Lock className="w-2.5 h-2.5" />
                        {t('agentManager.systemBadge')}
                      </span>
                    )}
                    <span className="text-[10px] bg-gray-100 text-gray-600 px-2 py-0.5 rounded uppercase font-semibold">
                      {agent.domain}
                    </span>
                    <Link to="/nodes" className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded ${
                      nodeCount > 0 ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                    }`}>
                      <Activity className={`w-3 h-3 ${nodeCount > 0 ? 'animate-pulse' : ''}`} />
                      {nodeCount > 0 ? t('agentManager.runningCount', { count: nodeCount }) : t('agentManager.noNodes')}
                    </Link>
                    <Link to="/instances" className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded ${
                      liveInstances > 0 ? 'bg-indigo-100 text-indigo-700' : 'bg-gray-100 text-gray-500'
                    }`} title={t('agentManager.instancesHint')}>
                      <Radio className={`w-3 h-3 ${liveInstances > 0 ? 'animate-pulse' : ''}`} />
                      {t('agentManager.instancesCount', { count: liveInstances })}
                    </Link>
                  </div>
                </div>

                <ImportedAgentStatus agent={agent} />

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
                            <div className={`text-[10px] uppercase tracking-wider font-semibold ${nodesAtCap ? 'text-orange-500' : nodeCount > 0 ? 'text-indigo-500' : 'text-gray-400'}`}>{t('agentManager.nodes')}</div>
                            {nodesAtCap && <span className="text-[9px] font-bold text-white bg-orange-500 px-1 py-0.5 rounded leading-none animate-pulse">{t('agentManager.full')}</span>}
                          </div>
                          <div className={`text-sm font-semibold ${nodesAtCap ? 'text-orange-900' : nodeCount > 0 ? 'text-indigo-900' : 'text-gray-500'}`}>{nodeCount}/{isDefaultWs ? '∞' : wsSessionCap}</div>
                          <div className={`text-[10px] mt-0.5 ${nodesAtCap ? 'text-orange-600' : nodeCount > 0 ? 'text-indigo-600' : 'text-gray-400'}`}>{t('agentManager.running')}</div>
                        </div>

                        {/* Sessions card */}
                        <div className={`border rounded-lg px-2 py-2 ${atCap ? 'bg-red-50 border-red-300' : 'bg-green-50 border-green-100'}`}>
                          <div className="flex items-center justify-between">
                            <div className={`text-[10px] uppercase tracking-wider font-semibold ${atCap ? 'text-red-500' : 'text-green-500'}`}>{t('agentManager.sessions')}</div>
                            {atCap && <span className="text-[9px] font-bold text-white bg-red-500 px-1 py-0.5 rounded leading-none animate-pulse">{t('agentManager.full')}</span>}
                          </div>
                          <div className={`text-sm font-semibold ${atCap ? 'text-red-900' : 'text-green-900'}`}>
                            {metrics.used}/{nodeCount > 0 ? totalCap : '—'}
                          </div>
                          <div className={`text-[10px] mt-0.5 ${atCap ? 'text-red-600 font-semibold' : 'text-green-700'}`}>
                            {nodeCount === 0 ? t('agentManager.noNodesLower') : atCap ? t('agentManager.atCapacity') : t('agentManager.load', { pct: sessionLoad })}
                          </div>
                        </div>

                        {/* Running tasks card */}
                        <div className="bg-amber-50 border border-amber-100 rounded-lg px-2 py-2">
                          <div className="text-[10px] uppercase tracking-wider text-amber-500 font-semibold">{t('agentManager.tasks')}</div>
                          <div className="text-sm font-semibold text-amber-900">{metrics.runningTasks}</div>
                          <div className="text-[10px] text-amber-600 mt-0.5">{t('agentManager.assignedCount', { count: metrics.assignedTasks })}</div>
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
                        <span>{nodeCount > 0 ? t('agentManager.load', { pct: sessionLoad }) : t('agentManager.noNodesRunning')}</span>
                        <span>{t('agentManager.runningAssigned', { running: metrics.runningTasks, assigned: metrics.assignedTasks })}</span>
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
                  <button
                    onClick={() => handleStartNode(agent.id)}
                    disabled={startingNode === agent.id || nodesAtCap}
                    title={nodesAtCap ? t('agentManager.nodeLimitReached') : undefined}
                    className="flex-1 inline-flex items-center justify-center px-2 py-1.5 text-xs font-semibold bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {startingNode === agent.id
                      ? <RefreshCw className="w-3.5 h-3.5 mr-1 animate-spin" />
                      : <Play className="w-3.5 h-3.5 mr-1" />}
                    {t('common.start')}
                  </button>
                  {agent.system ? (
                    <span
                      className="p-1.5 border border-gray-100 text-gray-300 rounded cursor-not-allowed bg-gray-50"
                      title={t('agentManager.systemAgentRequiredInEvery')}
                    >
                      <Lock className="w-4 h-4" />
                    </span>
                  ) : (
                    <button
                      onClick={() => handleDisconnect(agent.id)}
                      className="p-1.5 border border-gray-200 text-gray-500 rounded hover:bg-red-50 hover:text-red-600 transition-colors"
                      title={!selectedWorkspace || selectedWorkspace === 'default' ? t('agentManager.deleteAgent') : t('agentManager.removeFromWorkspace')}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Agent Creation Wizard */}
      {showWizard && (() => {
        const STEPS =
          wizardType === 'creator' ? [t('agentManager.steps.type'), t('agentManager.steps.launch')] :
          wizardType === 'clone'   ? [t('agentManager.steps.type'), t('agentManager.steps.source'), t('agentManager.steps.identity'), t('agentManager.steps.review')] :
                                     [t('agentManager.steps.type'), t('agentManager.steps.identity'), t('agentManager.steps.capabilities'), t('agentManager.steps.review')];
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
                    {t('agentManager.createAgent')}
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
                    <p className="text-sm text-gray-500">{t('agentManager.chooseHowYouWantTo')}</p>
                    {[
                      { value: 'creator', icon: Sparkles, label: t('agentManager.useAgentCreator'), desc: t('agentManager.useAgentCreatorDesc') },
                      { value: 'custom', icon: Box, label: t('agentManager.customAgent'), desc: t('agentManager.customAgentDesc') },
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
                        <div className="text-xs font-bold uppercase tracking-wide text-gray-400 mb-0.5">{t('agentManager.model')}</div>
                        <div className="text-sm text-gray-700 font-medium truncate">
                          {creatorModelInfo?.provider && creatorModelInfo.provider !== 'inherit'
                            ? `${creatorModelInfo.provider}${creatorModelInfo.model ? ` / ${creatorModelInfo.model}` : ''}`
                            : t('agentManager.inheritsGlobal')}
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
                            placeholder={t('agentManager.eGACodeReviewer')}
                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 resize-none"
                            value={creatorInput}
                            onChange={e => setCreatorInput(e.target.value)}
                          />
                        </div>

                        <div className="rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3 space-y-1.5">
                          <p className="text-xs font-semibold text-indigo-700">{t('agentManager.whatTheAgentCreatorCan')}</p>
                          <ul className="list-disc list-inside text-xs text-indigo-600 space-y-0.5">
                            <li>{t('agentManager.designASystemPromptTailored')}</li>
                            <li>{t('agentManager.selectTheRightToolsFrom')}</li>
                            <li>{t('agentManager.registerTheNewAgentDirectly')}</li>
                            <li>{t('agentManager.listOrInspectExistingAgents')}</li>
                          </ul>
                        </div>
                      </>
                    )}

                    {/* Streaming / result output */}
                    {(creatorStreaming || creatorOutput) && (
                      <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                        <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-gray-400 mb-2">
                          <span>{t('agentManager.agentCreator')}</span>
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
                        {t('agentManager.doneCheckTheAgentList')}
                      </div>
                    )}

                    <p className="text-xs text-gray-400">
                      {t('agentManager.creatorRunsIn')} {selectedWorkspace && selectedWorkspace !== 'default'
                        ? <span className="font-semibold text-gray-500">{selectedWorkspace}</span>
                        : t('agentManager.theDefaultWorkspace')}.
                    </p>
                  </div>
                )}

                {/* Step 2 (clone) — Source agent */}
                {wizardStep === 2 && wizardType === 'clone' && (
                  <div className="space-y-4">
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase mb-1">{t('agentManager.sourceAgent')}</label>
                      <select
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                        value={wizardData.original_id}
                        onChange={e => setWizardData(d => ({ ...d, original_id: e.target.value }))}
                      >
                        <option value="">{t('agentManager.selectAgentToClone')}</option>
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
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">{t('agentManager.agentId')} <span className="text-red-500">*</span></label>
                        <input
                          type="text" required placeholder={t('agentManager.eGCodeReviewer')}
                          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                          value={wizardData.id}
                          onChange={e => setWizardData(d => ({ ...d, id: e.target.value.replace(/\s/g, '_').toLowerCase() }))}
                        />
                        <p className="text-[10px] text-gray-400 mt-1">{t('agentManager.lowercaseNoSpaces')}</p>
                      </div>
                      <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">{t('agentManager.domain')}</label>
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
                      <label className="block text-xs font-bold text-gray-500 uppercase mb-1">{t('agentManager.displayName')} <span className="text-red-500">*</span></label>
                      <input
                        type="text" required placeholder={t('agentManager.eGCodeReviewer2')}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                        value={wizardData.name}
                        onChange={e => setWizardData(d => ({ ...d, name: e.target.value }))}
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase mb-1">{t('agentManager.description')}</label>
                      <textarea
                        rows="2" placeholder={t('agentManager.whatDoesThisAgentDo')}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                        value={wizardData.description}
                        onChange={e => setWizardData(d => ({ ...d, description: e.target.value }))}
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase mb-1">{t('agentManager.capacitySlots')}</label>
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
                          <label className="block text-xs font-bold text-gray-500 uppercase mb-1">{t('agentManager.systemPrompt')} <span className="text-red-500">*</span></label>
                          <textarea
                            rows="5" placeholder={t('agentManager.youAreASpecializedAgent')}
                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500"
                            value={wizardData.system_prompt}
                            onChange={e => setWizardData(d => ({ ...d, system_prompt: e.target.value }))}
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-bold text-gray-500 uppercase mb-2">{t('agentManager.tools')}</label>
                          <select
                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 bg-white"
                            value=""
                            onChange={e => { if (e.target.value) toggleWizardTool(e.target.value); }}
                          >
                            <option value="">{t('agentManager.addATool')}</option>
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
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-1">{t('agentManager.agentEndpointUrl')} <span className="text-red-500">*</span></label>
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
                    <p className="text-sm text-gray-500">{t('agentManager.reviewYourAgentBeforeCreating')}</p>
                    <div className="bg-gray-50 rounded-xl p-4 space-y-2 text-sm">
                      <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">{t('agentManager.type')}</span><span className="font-semibold capitalize text-gray-800">{wizardType}</span></div>
                      <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">ID</span><span className=" text-gray-800">{wizardData.id || wizardData.original_id}</span></div>
                      <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">{t('agentManager.name')}</span><span className="text-gray-800">{wizardData.name}</span></div>
                      {wizardData.description && <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">{t('agentManager.description')}</span><span className="text-gray-700">{wizardData.description}</span></div>}
                      <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">{t('agentManager.domain')}</span><span className="text-gray-800">{wizardData.domain}</span></div>
                      <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">{t('agentManager.capacity')}</span><span className="text-gray-800">{t('agentManager.slotCount', { count: wizardData.capacity })}</span></div>
                      {wizardType === 'custom' && wizardData.tools.length > 0 && (
                        <div className="flex gap-2">
                          <span className="text-gray-400 w-24 flex-shrink-0">{t('agentManager.tools')}</span>
                          <div className="flex flex-wrap gap-1">
                            {wizardData.tools.map(t => <span key={t} className="text-[11px] bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded">{t}</span>)}
                          </div>
                        </div>
                      )}
                      {wizardType === 'remote' && <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">URL</span><span className=" text-gray-700 break-all">{wizardData.agent_url}</span></div>}
                      {wizardType === 'clone' && <div className="flex gap-2"><span className="text-gray-400 w-24 flex-shrink-0">{t('agentManager.source')}</span><span className=" text-gray-800">{wizardData.original_id}</span></div>}
                      {wizardType === 'custom' && wizardData.system_prompt && (
                        <div className="flex gap-2 flex-col">
                          <span className="text-gray-400">{t('agentManager.systemPrompt')}</span>
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
                  {wizardStep === 1 ? 'Cancel' : <><ChevronLeft className="w-4 h-4" />{t('agentManager.back')}</>}
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
                        ? t('common.close')
                        : creatorStreaming
                          ? <><RefreshCw className="w-4 h-4 animate-spin" />{t('agentManager.creating')}</>
                          : <><Sparkles className="w-4 h-4" />{t('agentManager.createAgent')}</>
                      : t('agentManager.createAgent')
                    : <>{t('common.next')}<ChevronRight className="w-4 h-4" /></>
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
                {t('agentManager.scheduleTaskOnNode')}
            </h3>
            <form onSubmit={handleAssign}>
              <div className="mb-6">
                <label className="block text-xs font-bold text-gray-500 uppercase mb-2">{t('agentManager.selectTargetTask')}</label>
                <select
                  className="w-full border border-gray-300 rounded-md px-3 py-3 text-sm focus:ring-2 focus:ring-indigo-500"
                  value={assignData.task_id}
                  onChange={(e) => setAssignData({ ...assignData, task_id: e.target.value })}
                  required
                >
                  <option value="">{t('agentManager.chooseTask')}</option>
                  {tasks.filter(t => t.agent_state !== 'running').map(t => (
                    <option key={t.id} value={t.id}>{t.title} ({t.id.slice(0,8)})</option>
                  ))}
                </select>
              </div>
              <div className="flex justify-end space-x-3">
                <button type="button" onClick={() => setShowAssignModal(false)} className="px-4 py-2 text-sm text-gray-500">{t('agentManager.cancel')}</button>
                <button type="submit" disabled={!assignData.task_id} className="bg-indigo-600 text-white px-6 py-2 rounded-md font-bold shadow-md disabled:opacity-50">{t('agentManager.startExecution')}</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Import an agent that already lives in its own repository */}
      {showImportModal && (
        <ImportAgentModal
          workspace={selectedWorkspace}
          onClose={() => setShowImportModal(false)}
          onDone={() => fetchData()}
        />
      )}
    </PageContainer>
  );
};

/**
 * Readiness strip for an agent imported from a repository.
 *
 * Renders nothing for ordinary agents. For an imported one it states, on the
 * card itself, whether the agent can run — and when it cannot, exactly what is
 * missing. That is the whole point of registering an unready agent instead of
 * refusing the import: the to-do list has to live where the agent is.
 */
const ImportedAgentStatus = ({ agent }) => {
  const { t } = useI18n();
  if (!agent.remote) return null;
  const readiness = agent.remote.readiness || {};
  const blocking = readiness.blocking || [];
  const ready = !!readiness.runnable;

  return (
    <div
      className={`mb-3 rounded-lg border px-2.5 py-2 ${
        ready ? 'bg-emerald-50 border-emerald-100' : 'bg-amber-50 border-amber-200'
      }`}
    >
      <div className="flex items-center gap-1.5">
        {ready ? (
          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
        ) : (
          <AlertTriangle className="w-3.5 h-3.5 text-amber-600 shrink-0" />
        )}
        <span
          className={`text-[10px] uppercase tracking-wider font-bold ${
            ready ? 'text-emerald-700' : 'text-amber-700'
          }`}
        >
          {t('agentManager.importedAgent')} · {ready ? t('agentManager.ready') : t('agentManager.needsSetup')}
        </span>
      </div>
      {!ready && blocking.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {blocking.map((c) => (
            <li key={c.id} className="text-[11px] text-amber-900 leading-snug">
              <span className="font-semibold">{c.label}:</span> {c.detail}
            </li>
          ))}
        </ul>
      )}
      {agent.remote.url && (
        <div className="text-[10px] text-gray-500 truncate mt-1" title={agent.remote.url}>
          {agent.remote.url}
        </div>
      )}
    </div>
  );
};

export default AgentManager;
