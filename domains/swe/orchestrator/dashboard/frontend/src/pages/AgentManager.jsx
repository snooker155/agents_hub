import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Users, Copy, ExternalLink, Play, AlertCircle } from 'lucide-react';
import { getAgents, getTasks, cloneAgent, assignAgent, connectAgent, createCustomAgent, disconnectAgent, getAgentHealth } from '../api';

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
  const [connectData, setConnectData] = useState({ id: '', name: '', agent_url: '', capacity: 1 });
  const [customData, setCustomData] = useState({ id: '', name: '', system_prompt: '', tools: ['read_file', 'write_file'], capacity: 1 });
  const [healthData, setHealthData] = useState({});

  const fetchData = async () => {
    try {
      const [agentsResp, tasksResp] = await Promise.all([getAgents(), getTasks()]);
      setAgents(agentsResp.data);
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
      setCloneData({ original_id: '', new_id: '', new_name: '' });
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
    if (!confirm('Disconnect this agent?')) return;
    try {
      await disconnectAgent(id);
      fetchData();
    } catch (error) {
      alert('Error disconnecting agent: ' + (error.response?.data?.detail || error.message));
    }
  };

  const getAgentStatus = (agent) => {
    const activeTasks = tasks.filter(t => t.assigned_agent_type === agent.id && t.agent_state === 'running');
    const used = activeTasks.length;
    const capacity = agent.capacity || 1;

    if (used >= capacity) {
      return { status: `Full (${used}/${capacity})`, color: 'text-orange-500', task: activeTasks[0], full: true };
    }
    return { status: `Available (${used}/${capacity})`, color: 'text-green-500', task: null, full: false };
  };

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-2xl font-semibold text-gray-800">Agents</h2>
        <div className="flex space-x-3">
            <button
              onClick={() => setShowConnectModal(true)}
              className="bg-emerald-600 text-white px-4 py-2 rounded-md flex items-center hover:bg-emerald-700"
            >
              <ExternalLink className="w-5 h-5 mr-2" />
              Connect Remote
            </button>
            <button
              onClick={() => setShowCreateCustomModal(true)}
              className="bg-blue-600 text-white px-4 py-2 rounded-md flex items-center hover:bg-blue-700"
            >
              <Play className="w-5 h-5 mr-2" />
              Create Custom
            </button>
            <button
            onClick={() => {
                setCloneData({ ...cloneData, original_id: agents[0]?.id || '' });
                setShowCloneModal(true);
            }}
            className="bg-indigo-600 text-white px-4 py-2 rounded-md flex items-center hover:bg-indigo-700"
            >
            <Copy className="w-5 h-5 mr-2" />
            Clone Agent
            </button>
        </div>
      </div>

      {loading ? (
        <div className="text-center py-10">Loading agents...</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {agents.map((agent) => {
            const status = getAgentStatus(agent);
            const health = healthData[agent.id];
            return (
              <div key={agent.id} className="bg-white rounded-lg shadow-md p-6 border-t-4 border-indigo-600 relative">
                <button
                  onClick={() => handleDisconnect(agent.id)}
                  className="absolute top-2 right-2 text-gray-400 hover:text-red-500"
                  title="Disconnect Agent"
                >
                  &times;
                </button>
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-lg font-bold text-gray-900 hover:text-indigo-600 transition-colors">
                      <Link to={`/agents/${agent.id}`}>{agent.name}</Link>
                    </h3>
                    <p className="text-sm text-gray-500 font-mono flex items-center">
                      {agent.id}
                      <Link to={`/agents/${agent.id}`} title="View Details">
                        <ExternalLink className="w-3 h-3 ml-2 text-indigo-500 hover:text-indigo-700" />
                      </Link>
                    </p>
                    {agent.is_remote && (
                        <p className="text-[10px] text-indigo-600 font-mono truncate max-w-[150px]">{agent.agent_url}</p>
                    )}
                  </div>
                  <div className="flex flex-col items-end">
                    <div className={`text-[10px] font-semibold uppercase px-2 py-0.5 rounded mb-1 ${status.color === 'text-green-500' ? 'bg-green-100 text-green-800' : 'bg-orange-100 text-orange-800'}`}>
                        {status.status}
                    </div>
                    {agent.is_remote && health && (
                        <div className={`text-[10px] font-bold uppercase ${health.status === 'up' ? 'text-green-600' : 'text-red-600'}`}>
                            {health.status === 'up' ? '● Online' : '○ Offline'}
                        </div>
                    )}
                  </div>
                </div>

                <div className="space-y-4">
                  <div>
                    <p className="text-xs text-gray-500 uppercase font-bold mb-1">Capabilities</p>
                    <div className="flex flex-wrap gap-2">
                      {agent.capabilities.map(cap => (
                        <span key={cap} className="text-[10px] bg-gray-100 px-2 py-0.5 rounded text-gray-600">
                          {cap}
                        </span>
                      ))}
                    </div>
                  </div>

                  {status.task && (
                    <div className="p-3 bg-indigo-50 rounded-md border border-indigo-100">
                      <p className="text-xs text-indigo-800 font-bold mb-1">Recent Active Task</p>
                      <p className="text-sm text-indigo-900 font-medium truncate">{status.task.title}</p>
                      <Link to={`/tasks/${status.task.id}`} className="text-xs text-indigo-600 hover:underline flex items-center mt-1">
                        View Details <ExternalLink className="w-3 h-3 ml-1" />
                      </Link>
                    </div>
                  )}

                  {!status.full && (
                    <button
                      onClick={() => {
                        setAssignData({ ...assignData, agent_id: agent.id });
                        setShowAssignModal(true);
                      }}
                      className="w-full mt-2 flex items-center justify-center px-4 py-2 border border-indigo-600 text-indigo-600 rounded-md hover:bg-indigo-50 transition-colors"
                    >
                      <Play className="w-4 h-4 mr-2" /> Assign Task
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Clone Agent Modal */}
      {showCloneModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg max-w-md w-full p-6">
            <h3 className="text-xl font-bold mb-4">Clone Agent</h3>
            <form onSubmit={handleClone}>
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-1">Source Agent</label>
                <select
                  className="w-full border border-gray-300 rounded-md px-3 py-2"
                  value={cloneData.original_id}
                  onChange={(e) => setCloneData({ ...cloneData, original_id: e.target.value })}
                >
                  {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </div>
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-1">New ID</label>
                <input
                  type="text"
                  required
                  className="w-full border border-gray-300 rounded-md px-3 py-2"
                  value={cloneData.new_id}
                  onChange={(e) => setCloneData({ ...cloneData, new_id: e.target.value })}
                  placeholder="e.g. swe-agent-2"
                />
              </div>
              <div className="mb-6">
                <label className="block text-sm font-medium text-gray-700 mb-1">New Name</label>
                <input
                  type="text"
                  required
                  className="w-full border border-gray-300 rounded-md px-3 py-2"
                  value={cloneData.new_name}
                  onChange={(e) => setCloneData({ ...cloneData, new_name: e.target.value })}
                  placeholder="e.g. SWE Agent (Secondary)"
                />
              </div>
              <div className="flex justify-end space-x-3">
                <button type="button" onClick={() => setShowCloneModal(false)} className="px-4 py-2 text-gray-700">Cancel</button>
                <button type="submit" className="bg-indigo-600 text-white px-4 py-2 rounded-md hover:bg-indigo-700">Clone</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Connect Remote Agent Modal */}
      {showConnectModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg max-w-md w-full p-6">
            <h3 className="text-xl font-bold mb-4">Connect Remote Agent</h3>
            <form onSubmit={handleConnect}>
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-1">ID</label>
                <input
                  type="text" required className="w-full border rounded px-3 py-2"
                  value={connectData.id}
                  onChange={(e) => setConnectData({ ...connectData, id: e.target.value })}
                  placeholder="remote-agent-1"
                />
              </div>
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                <input
                  type="text" required className="w-full border rounded px-3 py-2"
                  value={connectData.name}
                  onChange={(e) => setConnectData({ ...connectData, name: e.target.value })}
                />
              </div>
              <div className="mb-4">
                <label htmlFor="agent_url" className="block text-sm font-medium text-gray-700 mb-1">Agent URL</label>
                <input
                  id="agent_url"
                  type="url" required className="w-full border rounded px-3 py-2"
                  value={connectData.agent_url}
                  onChange={(e) => setConnectData({ ...connectData, agent_url: e.target.value })}
                  placeholder="http://..."
                />
              </div>
              <div className="mb-6">
                <label className="block text-sm font-medium text-gray-700 mb-1">Capacity</label>
                <input
                  type="number" min="1" required className="w-full border rounded px-3 py-2"
                  value={connectData.capacity}
                  onChange={(e) => setConnectData({ ...connectData, capacity: parseInt(e.target.value) })}
                />
              </div>
              <div className="flex justify-end space-x-3">
                <button type="button" onClick={() => setShowConnectModal(false)} className="px-4 py-2">Cancel</button>
                <button type="submit" className="bg-emerald-600 text-white px-4 py-2 rounded">Connect</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Create Custom Agent Modal */}
      {showCreateCustomModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg max-w-md w-full p-6">
            <h3 className="text-xl font-bold mb-4">Create Custom Agent</h3>
            <form onSubmit={handleCreateCustom}>
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-1">ID</label>
                <input
                  type="text" required className="w-full border rounded px-3 py-2"
                  value={customData.id}
                  onChange={(e) => setCustomData({ ...customData, id: e.target.value })}
                />
              </div>
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                <input
                  type="text" required className="w-full border rounded px-3 py-2"
                  value={customData.name}
                  onChange={(e) => setCustomData({ ...customData, name: e.target.value })}
                />
              </div>
              <div className="mb-4">
                <label htmlFor="system_prompt" className="block text-sm font-medium text-gray-700 mb-1">System Prompt</label>
                <textarea
                  id="system_prompt"
                  required className="w-full border rounded px-3 py-2" rows="4"
                  value={customData.system_prompt}
                  onChange={(e) => setCustomData({ ...customData, system_prompt: e.target.value })}
                />
              </div>
              <div className="mb-6">
                <label className="block text-sm font-medium text-gray-700 mb-1">Capacity</label>
                <input
                  type="number" min="1" required className="w-full border rounded px-3 py-2"
                  value={customData.capacity}
                  onChange={(e) => setCustomData({ ...customData, capacity: parseInt(e.target.value) })}
                />
              </div>
              <div className="flex justify-end space-x-3">
                <button type="button" onClick={() => setShowCreateCustomModal(false)} className="px-4 py-2">Cancel</button>
                <button type="submit" className="bg-blue-600 text-white px-4 py-2 rounded">Create</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Assign Task Modal */}
      {showAssignModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg max-w-md w-full p-6">
            <h3 className="text-xl font-bold mb-4">Directly Assign Task</h3>
            <form onSubmit={handleAssign}>
              <div className="mb-6">
                <label className="block text-sm font-medium text-gray-700 mb-2">Select Task</label>
                <select
                  className="w-full border border-gray-300 rounded-md px-3 py-2"
                  value={assignData.task_id}
                  onChange={(e) => setAssignData({ ...assignData, task_id: e.target.value })}
                  required
                >
                  <option value="">-- Choose Task --</option>
                  {tasks.filter(t => t.agent_state !== 'running').map(t => (
                    <option key={t.id} value={t.id}>{t.title}</option>
                  ))}
                </select>
              </div>
              <div className="flex justify-end space-x-3">
                <button type="button" onClick={() => setShowAssignModal(false)} className="px-4 py-2 text-gray-700">Cancel</button>
                <button type="submit" disabled={!assignData.task_id} className="bg-indigo-600 text-white px-4 py-2 rounded-md hover:bg-indigo-700 disabled:opacity-50">Assign & Start</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default AgentManager;
