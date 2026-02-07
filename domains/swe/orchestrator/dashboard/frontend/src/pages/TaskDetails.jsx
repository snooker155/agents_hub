import React, { useState, useEffect } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { ChevronLeft, CheckCircle, Clock, AlertCircle, StopCircle, Terminal, Play, Square, Split } from 'lucide-react';
import { getTask, getAgents, assignAgent, stopAgent, getAgentStatus, getLogs, runDecomposer, getTaskProgress } from '../api';
import api from '../api';

const TaskDetails = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [task, setTask] = useState(null);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [logs, setLogs] = useState('');
  const [progressSteps, setProgressSteps] = useState([]);
  const [activeRunId, setActiveRunId] = useState(null);
  const [showAssignModal, setShowAssignModal] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState('');
  const [wsName, setWsName] = useState('');
  const [workspaceFiles, setWorkspaceFiles] = useState([]);

  const fetchData = async () => {
    try {
      const taskResp = await getTask(id);
      setTask(taskResp.data);
      setWsName(taskResp.data.workspace || '');

      const agentsResp = await getAgents();
      setAgents(agentsResp.data);

      if (taskResp.data.assigned_agent_run_id) {
        setActiveRunId(taskResp.data.assigned_agent_run_id);
        fetchLogs(taskResp.data.assigned_agent_run_id);
        fetchProgress();
      }

      // Load workspace files
      try {
        const filesResp = await api.get(`/tasks/${id}/workspace-files`);
        setWorkspaceFiles(filesResp.data.files || []);
      } catch {}

      setLoading(false);
    } catch (error) {
      console.error('Error fetching task details:', error);
      setLoading(false);
    }
  };

  const fetchLogs = async (runId) => {
    try {
      const resp = await getLogs(runId);
      setLogs(resp.data.logs);
    } catch (error) {
      // Logs might not be available yet
      console.warn('Could not fetch logs');
    }
  };

  const fetchProgress = async () => {
      try {
          const resp = await getTaskProgress(id);
          setProgressSteps(resp.data.steps || []);
      } catch (e) {
          console.warn('Could not fetch progress');
      }
  }

  useEffect(() => {
    fetchData();
    const interval = setInterval(() => {
        fetchData();
    }, 5000);
    return () => clearInterval(interval);
  }, [id]);

  const handleAssignAgent = async () => {
    try {
      const resp = await assignAgent(id, { agent_id: selectedAgent });
      setActiveRunId(resp.data.run_id);
      setShowAssignModal(false);
      fetchData();
    } catch (error) {
      alert('Error assigning agent: ' + error.response?.data?.detail || error.message);
    }
  };

  const handleStopAgent = async () => {
    try {
      await stopAgent(id);
      fetchData();
    } catch (error) {
      console.error('Error stopping agent:', error);
    }
  };

  const handleRunDecomposer = async () => {
    try {
      const resp = await runDecomposer(id, {});
      if (resp.data?.run_id) {
        setActiveRunId(resp.data.run_id);
        setShowAssignModal(false);
        fetchData();
      }
    } catch (error) {
      alert('Error running decomposer: ' + (error.response?.data?.detail || error.message));
    }
  };

  if (loading) return <div className="text-center py-10">Loading task details...</div>;
  if (!task) return <div className="text-center py-10">Task not found</div>;

  const getStatusIcon = (status) => {
    switch (status) {
      case 'done': return <CheckCircle className="w-5 h-5 text-green-500" />;
      case 'in_progress': return <Clock className="w-5 h-5 text-blue-500" />;
      case 'blocked': return <AlertCircle className="w-5 h-5 text-red-500" />;
      case 'stopped': return <StopCircle className="w-5 h-5 text-gray-500" />;
      default: return <Clock className="w-5 h-5 text-gray-400" />;
    }
  };

  return (
    <div>
      <Link to="/" className="flex items-center text-indigo-600 hover:text-indigo-900 mb-6">
        <ChevronLeft className="w-4 h-4 mr-1" /> Back to Tasks
      </Link>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left Column: Task Info & Subtasks */}
        <div className="lg:col-span-2 space-y-8">
          <div className="bg-white p-6 shadow-md rounded-lg">
            <div className="flex justify-between items-start mb-4">
              <div>
                <h2 className="text-2xl font-bold text-gray-900">{task.title}</h2>
                <div className="flex items-center mt-1">
                  {getStatusIcon(task.status)}
                  <span className="ml-2 capitalize text-sm font-medium text-gray-600">{task.status.replace('_', ' ')}</span>
                  <span className="mx-2 text-gray-300">|</span>
                  <span className="text-sm text-gray-500">ID: {task.id}</span>
                </div>
              </div>
              <div className="flex space-x-2">
                {task.agent_state === 'running' ? (
                  <button
                    onClick={handleStopAgent}
                    className="flex items-center px-4 py-2 bg-red-600 text-white rounded-md hover:bg-red-700"
                  >
                    <Square className="w-4 h-4 mr-2" /> Stop Agent
                  </button>
                ) : (
                  <button
                    onClick={() => setShowAssignModal(true)}
                    className="flex items-center px-4 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700"
                  >
                    <Play className="w-4 h-4 mr-2" /> Assign Agent
                  </button>
                )}
                {task.created_by === 'user' && (
                  <button
                    onClick={handleRunDecomposer}
                    className="flex items-center px-4 py-2 bg-emerald-600 text-white rounded-md hover:bg-emerald-700"
                  >
                    <Split className="w-4 h-4 mr-2" /> Run Decomposer
                  </button>
                )}
              </div>
            </div>
            <p className="text-gray-700 whitespace-pre-wrap">{task.description}</p>
            {task.blocked_reason && (
              <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-md">
                <p className="text-sm text-red-800 font-semibold flex items-center">
                  <AlertCircle className="w-4 h-4 mr-2" /> Blocked Reason:
                </p>
                <p className="text-sm text-red-700">{task.blocked_reason}</p>
              </div>
            )}
          </div>

          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4">Subtasks (Decomposition)</h3>
            {task.subtasks && task.subtasks.length > 0 ? (
              <div className="space-y-4">
                {task.subtasks.sort((a,b) => (a.order || 0) - (b.order || 0)).map((st) => (
                  <div
                    key={st.id}
                    className="flex items-center p-3 border border-gray-100 rounded-lg hover:bg-gray-50 cursor-pointer"
                    onClick={() => navigate(`/tasks/${st.id}`)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/tasks/${st.id}`); }}
                  >
                    {getStatusIcon(st.status)}
                    <div className="ml-4 flex-1">
                      <h4 className="font-medium text-gray-900">{st.title}</h4>
                      <p className="text-xs text-gray-500 truncate">{st.description}</p>
                    </div>
                    <div className="text-right">
                       <span className="text-xs font-mono bg-gray-100 px-2 py-1 rounded text-gray-600">
                         {st.assigned_agent_type || 'unassigned'}
                       </span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-gray-500 italic">No subtasks yet. The orchestrator will decompose this task if it's top-level.</p>
            )}
          </div>
        </div>

        {/* Right Column: Execution Status & Logs */}
        <div className="space-y-8">
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4">Execution Status</h3>
            <div className="space-y-3">
              <div className="flex justify-between">
                <span className="text-gray-500">Agent:</span>
                <span className="font-medium">{task.assigned_agent_type || 'None'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">State:</span>
                <span className={`font-medium capitalize ${task.agent_state === 'running' ? 'text-blue-600' : ''}`}>
                  {task.agent_state}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Workspace:</span>
                <div className="flex items-center space-x-2">
                  <input
                    className="border border-gray-300 rounded px-2 py-1 text-sm w-40"
                    placeholder="name"
                    value={wsName}
                    onChange={(e) => setWsName(e.target.value)}
                  />
                  <button
                    onClick={async () => {
                      try {
                        await api.post(`/tasks/${id}/workspace`, { workspace_name: wsName });
                        fetchData();
                      } catch (e) { alert('Failed to set workspace'); }
                    }}
                    className="text-xs bg-gray-800 text-white px-2 py-1 rounded"
                  >
                    Save
                  </button>
                </div>
              </div>
            </div>
          </div>

          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4">Execution Steps</h3>
            {progressSteps.length > 0 ? (
                <div className="space-y-4 max-h-[300px] overflow-auto pr-2">
                    {progressSteps.map((s, idx) => (
                        <div key={idx} className="border-l-2 border-indigo-500 pl-4 py-1">
                            <div className="flex justify-between text-[10px] text-gray-500 mb-1">
                                <span>Step {s.step}: {s.tool}</span>
                                <span>{new Date(s.started_at).toLocaleTimeString()}</span>
                            </div>
                            <div className="text-xs font-mono bg-gray-50 p-1 rounded truncate" title={s.input}>
                                {s.input}
                            </div>
                            {s.output && (
                                <div className="text-[10px] text-gray-600 mt-1 bg-green-50 p-1 rounded italic truncate">
                                    → {s.output}
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            ) : (
                <p className="text-gray-500 text-sm italic">No detailed steps yet.</p>
            )}
          </div>

          <div className="bg-gray-900 rounded-lg shadow-md overflow-hidden flex flex-col h-[400px]">
            <div className="bg-gray-800 px-4 py-2 flex items-center justify-between">
              <div className="flex items-center text-gray-300 text-sm">
                <Terminal className="w-4 h-4 mr-2" /> Agent Logs
              </div>
              {activeRunId && (
                <span className="text-[10px] text-gray-500 font-mono">Run: {activeRunId.slice(0,8)}</span>
              )}
            </div>
            <div className="p-4 flex-1 overflow-auto font-mono text-xs text-green-400 bg-black">
              {logs ? (
                <pre className="whitespace-pre-wrap">{logs}</pre>
              ) : (
                <p className="text-gray-600 italic">No logs available for this task.</p>
              )}
            </div>
          </div>

          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4">Workspace Files</h3>
            {workspaceFiles.length ? (
              <ul className="text-sm text-gray-700 max-h-64 overflow-auto list-disc pl-6">
                {workspaceFiles.map((f) => (
                  <li key={f} className="truncate">{f}</li>
                ))}
              </ul>
            ) : (
              <p className="text-gray-500 text-sm">No files found.</p>
            )}
          </div>
        </div>
      </div>

      {/* Assign Agent Modal */}
      {showAssignModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg max-w-md w-full p-6">
            <h3 className="text-xl font-bold mb-4">Assign Agent to Task</h3>
            <div className="mb-6">
              <label className="block text-sm font-medium text-gray-700 mb-2">Select Agent</label>
              <select
                className="w-full border border-gray-300 rounded-md px-3 py-2 focus:ring-indigo-500 focus:border-indigo-500"
                value={selectedAgent}
                onChange={(e) => setSelectedAgent(e.target.value)}
              >
                <option value="">-- Choose Agent --</option>
                {agents.map(agent => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name} ({agent.id})
                  </option>
                ))}
              </select>
            </div>
            <div className="flex justify-end space-x-3">
              <button
                onClick={() => setShowAssignModal(false)}
                className="px-4 py-2 text-gray-700 hover:text-gray-900"
              >
                Cancel
              </button>
              <button
                onClick={handleAssignAgent}
                disabled={!selectedAgent}
                className="bg-indigo-600 text-white px-4 py-2 rounded-md hover:bg-indigo-700 disabled:opacity-50"
              >
                Start Execution
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default TaskDetails;
