import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  Users,
  Copy,
  ExternalLink,
  Play,
  AlertCircle,
  Box,
  Shield,
  Server,
  Trash2,
  RefreshCw,
  FileCode
} from 'lucide-react';
import {
  getAgents,
  getTasks,
  cloneAgent,
  assignAgent,
  connectAgent,
  createCustomAgent,
  disconnectAgent,
  getAgentHealth
} from '../api';

const AgentManager = () => {
  const [agents, setAgents] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCloneModal, setShowCloneModal] = useState(false);
  const [showAssignModal, setShowAssignModal] = useState(false);
  const [showConnectModal, setShowConnectModal] = useState(false);
  const [showCreateCustomModal, setShowCreateCustomModal] = useState(false);
  const [cloneData, setCloneData] = useState({ original_id: '', new_id: '', new_name: '' });
  const [assignData, setAssignData] = useState({ task_id: '', agent_id: '' });
  const [connectData, setConnectData] = useState({ id: '', name: '', description: '', domain: 'general', agent_url: '', capacity: 1 });
  const [customData, setCustomData] = useState({ id: '', name: '', description: '', domain: 'general', system_prompt: '', tools: ['read_file', 'write_file'], capacity: 1 });
  const [healthData, setHealthData] = useState({});

  const fetchData = async () => {
    try {
      const [agentsResp, tasksResp] = await Promise.all([getAgents(), getTasks()]);
      // Filter out the orchestrator agent from the general list
      const filteredAgents = agentsResp.data.filter(a => a.id !== 'orchestrator' && a.id !== 'decomposer');
      setAgents(filteredAgents);
      setTasks(tasksResp.data);
      setLoading(false);

      // Fetch health for remote agents
      agentsResp.data.forEach(async (a) => {
          if (a.is_remote) {
              try {
                  const h = await getAgentHealth(a.id);
                  setHealthData(prev => ({ ...prev, [a.id]: h.data }));
              } catch (e) {
                setHealthData(prev => ({ ...prev, [a.id]: { status: 'error' } }));
              }
          }
      });
    } catch (error) {
      console.error('Error fetching data:', error);
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, []);

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

  const handleCreateCustom = async (e) => {
    e.preventDefault();
    try {
      await createCustomAgent(customData);
      setShowCreateCustomModal(false);
      fetchData();
    } catch (error) {
      alert('Error creating custom agent: ' + (error.response?.data?.detail || error.message));
    }
  };

  const handleDisconnect = async (id) => {
    if (!confirm('Disconnect this agent node?')) return;
    try {
      await disconnectAgent(id);
      fetchData();
    } catch (error) {
      alert('Error disconnecting agent: ' + (error.response?.data?.detail || error.message));
    }
  };

  const getAgentMetrics = (agent) => {
    const activeTasks = tasks.filter(t => t.assigned_agent_type === agent.id && t.agent_state === 'running');
    const used = activeTasks.length;
    const capacity = agent.capacity || 1;
    const loadFactor = (used / capacity) * 100;

    return {
        used,
        capacity,
        loadFactor,
        isFull: used >= capacity,
        activeTask: activeTasks[0]
    };
  };

  return (
    <div className="space-y-8">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold text-gray-800">Agent Nodes</h2>
          <p className="text-gray-500 text-sm">Manage the lifecycle and connectivity of your AI agents.</p>
        </div>
        <div className="flex space-x-3">
            <Link
              to="/manifest"
              className="bg-gray-800 text-white px-4 py-2 rounded-md flex items-center hover:bg-gray-900 shadow-sm"
            >
              <FileCode className="w-4 h-4 mr-2" />
              Apply YAML
            </Link>
            <button
              onClick={() => setShowConnectModal(true)}
              className="bg-emerald-600 text-white px-4 py-2 rounded-md flex items-center hover:bg-emerald-700 shadow-sm"
            >
              <Server className="w-4 h-4 mr-2" />
              Connect Remote
            </button>
            <button
              onClick={() => setShowCreateCustomModal(true)}
              className="bg-blue-600 text-white px-4 py-2 rounded-md flex items-center hover:bg-blue-700 shadow-sm"
            >
              <Box className="w-4 h-4 mr-2" />
              Create Custom
            </button>
        </div>
      </div>

      {loading ? (
        <div className="flex flex-col items-center justify-center py-20 bg-white rounded-xl border border-dashed border-gray-200">
           <RefreshCw className="w-8 h-8 text-indigo-400 animate-spin mb-4" />
           <p className="text-gray-500 font-medium">Scanning cluster for agent nodes...</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          {agents.map((agent) => {
            const metrics = getAgentMetrics(agent);
            const health = healthData[agent.id];
            const isHealthy = !agent.is_remote || (health && health.status === 'up');

            return (
              <div key={agent.id} className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden hover:shadow-md transition-shadow group">
                <div className="p-6">
                  <div className="flex justify-between items-start mb-6">
                    <div className="flex items-start space-x-4">
                      <div className={`p-3 rounded-lg ${isHealthy ? 'bg-indigo-50 text-indigo-600' : 'bg-red-50 text-red-600'}`}>
                        <Shield className="w-6 h-6" />
                      </div>
                      <div>
                        <h3 className="text-lg font-bold text-gray-900 group-hover:text-indigo-600 transition-colors">
                          <Link to={`/agents/${agent.id}`}>{agent.name}</Link>
                        </h3>
                        <div className="flex items-center mt-1 space-x-3 text-xs">
                          <span className="font-mono text-gray-400">node/{agent.id}</span>
                          <span className="bg-gray-100 text-gray-600 px-2 py-0.5 rounded uppercase font-bold tracking-tighter">
                            {agent.domain}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-col items-end space-y-2">
                      <div className={`text-[10px] font-bold uppercase px-2 py-1 rounded flex items-center ${
                        metrics.isFull ? 'bg-orange-100 text-orange-800' : 'bg-green-100 text-green-800'
                      }`}>
                        <div className={`w-1.5 h-1.5 rounded-full mr-2 ${metrics.isFull ? 'bg-orange-500' : 'bg-green-500'} animate-pulse`}></div>
                        {metrics.isFull ? 'At Capacity' : 'Available'}
                      </div>
                      {agent.is_remote && (
                        <div className={`text-[10px] font-bold uppercase ${isHealthy ? 'text-green-600' : 'text-red-600'}`}>
                          {isHealthy ? '● Online' : '○ Offline'}
                        </div>
                      )}
                    </div>
                  </div>

                  <p className="text-sm text-gray-500 mb-6 line-clamp-2 min-h-[40px]">
                    {agent.description || "No description provided for this agent node."}
                  </p>

                  <div className="grid grid-cols-2 gap-4 mb-6">
                    <div className="bg-gray-50 p-3 rounded-lg">
                      <div className="text-[10px] text-gray-400 uppercase font-bold mb-1">Utilization</div>
                      <div className="flex items-end justify-between">
                        <span className="text-xl font-bold text-gray-700">{metrics.used}<span className="text-sm text-gray-400 font-normal">/{metrics.capacity}</span></span>
                        <span className="text-xs text-gray-500">{Math.round(metrics.loadFactor)}%</span>
                      </div>
                      <div className="w-full bg-gray-200 rounded-full h-1.5 mt-2">
                        <div
                          className={`h-1.5 rounded-full transition-all duration-1000 ${metrics.loadFactor > 80 ? 'bg-orange-500' : 'bg-indigo-500'}`}
                          style={{ width: `${metrics.loadFactor}%` }}
                        ></div>
                      </div>
                    </div>
                    <div className="bg-gray-50 p-3 rounded-lg">
                      <div className="text-[10px] text-gray-400 uppercase font-bold mb-1">Capabilities</div>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {agent.capabilities.slice(0, 3).map(cap => (
                          <span key={cap} className="text-[9px] bg-white border border-gray-200 px-1.5 py-0.5 rounded text-gray-600">
                            {cap}
                          </span>
                        ))}
                        {agent.capabilities.length > 3 && (
                          <span className="text-[9px] text-gray-400">+{agent.capabilities.length - 3} more</span>
                        )}
                      </div>
                    </div>
                  </div>

                  {metrics.activeTask && (
                    <div className="mb-6 p-3 bg-indigo-50 rounded-lg border border-indigo-100 flex justify-between items-center">
                      <div>
                        <div className="text-[10px] text-indigo-400 font-bold uppercase">Active Processing</div>
                        <div className="text-sm font-semibold text-indigo-900 truncate max-w-[200px]">{metrics.activeTask.title}</div>
                      </div>
                      <Link to={`/tasks/${metrics.activeTask.id}`} className="p-2 hover:bg-indigo-100 rounded-full transition-colors text-indigo-600">
                        <ExternalLink className="w-4 h-4" />
                      </Link>
                    </div>
                  )}

                  <div className="flex space-x-3">
                    {!metrics.isFull && (
                      <button
                        onClick={() => {
                          setAssignData({ ...assignData, agent_id: agent.id });
                          setShowAssignModal(true);
                        }}
                        className="flex-1 flex items-center justify-center px-4 py-2 bg-indigo-600 text-white text-sm font-bold rounded-lg hover:bg-indigo-700 transition-colors shadow-sm"
                      >
                        <Play className="w-4 h-4 mr-2" /> Assign Task
                      </button>
                    )}
                    <button
                      onClick={() => {
                        setCloneData({ ...cloneData, original_id: agent.id, new_id: `${agent.id}-clone`, new_name: `${agent.name} (Clone)` });
                        setShowCloneModal(true);
                      }}
                      className="p-2 border border-gray-200 text-gray-500 rounded-lg hover:bg-gray-50 hover:text-indigo-600 transition-all"
                      title="Clone Node"
                    >
                      <Copy className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDisconnect(agent.id)}
                      className="p-2 border border-gray-200 text-gray-500 rounded-lg hover:bg-red-50 hover:text-red-600 transition-all"
                      title="Disconnect Node"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
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
                    type="url" required className="w-full border rounded px-3 py-2 text-sm font-mono"
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

      {/* Create Custom Agent Modal */}
      {showCreateCustomModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50 backdrop-blur-sm">
          <div className="bg-white rounded-xl max-w-md w-full p-6 shadow-2xl border border-gray-100">
            <h3 className="text-xl font-bold mb-4 flex items-center">
                <Box className="w-5 h-5 mr-2 text-blue-600" />
                Provision Custom Agent
            </h3>
            <form onSubmit={handleCreateCustom}>
              <div className="space-y-4 mb-6 overflow-y-auto max-h-[60vh] pr-2">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">ID</label>
                    <input
                      type="text" required className="w-full border rounded px-3 py-2 text-sm"
                      value={customData.id}
                      onChange={(e) => setCustomData({ ...customData, id: e.target.value })}
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Domain</label>
                    <input
                      type="text" required className="w-full border rounded px-3 py-2 text-sm"
                      value={customData.domain}
                      onChange={(e) => setCustomData({ ...customData, domain: e.target.value })}
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Display Name</label>
                  <input
                    type="text" required className="w-full border rounded px-3 py-2 text-sm"
                    value={customData.name}
                    onChange={(e) => setCustomData({ ...customData, name: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Description</label>
                  <textarea
                    className="w-full border rounded px-3 py-2 text-sm" rows="2"
                    value={customData.description}
                    onChange={(e) => setCustomData({ ...customData, description: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase mb-1">System Instructions (Prompt)</label>
                  <textarea
                    required className="w-full border rounded px-3 py-2 text-sm font-sans" rows="4"
                    value={customData.system_prompt}
                    onChange={(e) => setCustomData({ ...customData, system_prompt: e.target.value })}
                    placeholder="You are a specialized agent for..."
                  />
                </div>
                <div>
                  <label className="block text-xs font-bold text-gray-500 uppercase mb-1">Provisioned Slots</label>
                  <input
                    type="number" min="1" required className="w-full border rounded px-3 py-2 text-sm"
                    value={customData.capacity}
                    onChange={(e) => setCustomData({ ...customData, capacity: parseInt(e.target.value) })}
                  />
                </div>
              </div>
              <div className="flex justify-end space-x-3 border-t pt-4">
                <button type="button" onClick={() => setShowCreateCustomModal(false)} className="px-4 py-2 text-sm text-gray-500">Cancel</button>
                <button type="submit" className="bg-blue-600 text-white px-6 py-2 rounded-md font-bold shadow-md">Deploy Node</button>
              </div>
            </form>
          </div>
        </div>
      )}

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
