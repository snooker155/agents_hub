import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ChevronLeft, Activity, History, Server, Wrench, Cpu, Terminal, ExternalLink, CheckCircle, AlertCircle, Clock, Database, Save, Trash2 } from 'lucide-react';
import { getAgent, getAgentHistory, getAgentHealth, getLogs, updateAgentMemory, eraseAgentMemory } from '../api';

const AgentDetails = () => {
  const { id } = useParams();
  const [agent, setAgent] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [health, setHealth] = useState(null);
  const [selectedLog, setSelectedLog] = useState(null);
  const [logs, setLogs] = useState('');

  const [memoryType, setMemoryType] = useState('none');
  const [memoryData, setMemoryData] = useState('');
  const [isUpdatingMemory, setIsUpdatingMemory] = useState(false);

  const fetchData = async () => {
    try {
      const [agentResp, historyResp] = await Promise.all([
        getAgent(id),
        getAgentHistory(id)
      ]);
      setAgent(agentResp.data);
      setHistory(historyResp.data);
      setMemoryType(agentResp.data.memory_type || 'none');
      setMemoryData(typeof agentResp.data.memory_data === 'string' ? agentResp.data.memory_data : JSON.stringify(agentResp.data.memory_data || '', null, 2));

      if (agentResp.data.is_remote) {
        try {
          const healthResp = await getAgentHealth(id);
          setHealth(healthResp.data);
        } catch (e) {
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
      } catch (e) {
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
    } catch (error) {
      setLogs('Could not fetch logs for this run.');
    }
  };

  if (loading) return <div className="text-center py-10">Loading agent details...</div>;
  if (!agent) return <div className="text-center py-10">Agent not found</div>;

  const activeTask = history.find(r => r.status === 'running');

  return (
    <div>
      <Link to="/agents" className="flex items-center text-indigo-600 hover:text-indigo-900 mb-6">
        <ChevronLeft className="w-4 h-4 mr-1" /> Back to Agents
      </Link>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left Column: Agent Info */}
        <div className="lg:col-span-1 space-y-6">
          <div className="bg-white p-6 shadow-md rounded-lg border-t-4 border-indigo-600">
            <h2 className="text-2xl font-bold text-gray-900 mb-2">{agent.name}</h2>
            <p className="text-sm text-gray-500 font-mono mb-4">{agent.id}</p>

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

              <div className="py-2">
                <span className="text-gray-500 flex items-center mb-2"><Wrench className="w-4 h-4 mr-2" /> Capabilities</span>
                <div className="flex flex-wrap gap-2">
                  {agent.capabilities.map(cap => (
                    <span key={cap} className="text-xs bg-gray-100 px-2 py-1 rounded text-gray-600">{cap}</span>
                  ))}
                </div>
              </div>

              {agent.default_params?.model && (
                <div className="flex items-center justify-between py-2 border-t border-gray-50 mt-2">
                  <span className="text-gray-500 flex items-center"><Cpu className="w-4 h-4 mr-2" /> Model</span>
                  <span className="font-mono text-sm">{agent.default_params.model}</span>
                </div>
              )}
            </div>
          </div>

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
                  rows={4}
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

        {/* Right Column: History & Logs */}
        <div className="lg:col-span-2 space-y-6">
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
                          {run.task_id.slice(0, 8)}...
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
      </div>
    </div>
  );
};

export default AgentDetails;
