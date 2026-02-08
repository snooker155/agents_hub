import React, { useEffect, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { getWorkspace, getWorkspaceFilesByName, getAgents, addAgentToWorkspace, removeAgentFromWorkspace } from '../api';
import { ChevronLeft, Folder, FileText, Users, ShoppingBag, Plus, Trash2, Shield } from 'lucide-react';

const WorkspaceDetails = () => {
  const { name } = useParams();
  const navigate = useNavigate();
  const [ws, setWs] = useState(null);
  const [files, setFiles] = useState([]);
  const [allAgents, setAllAgents] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    try {
      const [wsResp, filesResp, agentsResp] = await Promise.all([
        getWorkspace(name),
        getWorkspaceFilesByName(name),
        getAgents()
      ]);
      setWs(wsResp.data);
      setFiles(filesResp.data.files || []);
      setAllAgents(agentsResp.data);
    } catch (e) {
      console.error('Failed to load workspace data', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchData(); const i = setInterval(fetchData, 5000); return () => clearInterval(i); }, [name]);

  const handleAddAgent = async (agentId) => {
    try {
      await addAgentToWorkspace(name, agentId);
      fetchData();
    } catch (e) {
      alert('Failed to add agent');
    }
  };

  const handleRemoveAgent = async (agentId) => {
    try {
      await removeAgentFromWorkspace(name, agentId);
      fetchData();
    } catch (e) {
      alert('Failed to remove agent');
    }
  };

  if (loading) return <div className="text-center py-10">Loading workspace...</div>;
  if (!ws) return <div className="text-center py-10">Workspace not found</div>;

  return (
    <div>
      <Link to="/workspaces" className="flex items-center text-indigo-600 hover:text-indigo-900 mb-6">
        <ChevronLeft className="w-4 h-4 mr-1" /> Back to Workspaces
      </Link>

      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center">
          <Folder className="w-7 h-7 text-gray-700 mr-2" />
          <div>
            <h2 className="text-2xl font-bold text-gray-900">{ws.name}</h2>
            <div className="text-sm text-gray-500 font-mono truncate max-w-2xl">{ws.path}</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-lg font-bold mb-4">Allocated Tasks</h3>
          {ws.tasks && ws.tasks.length ? (
            <div className="space-y-3">
              {ws.tasks.map(t => (
                <div
                  key={t.id}
                  className="p-3 border rounded hover:bg-gray-50 cursor-pointer"
                  onClick={() => navigate(`/tasks/${t.id}`)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/tasks/${t.id}`); }}
                >
                  <div className="flex justify-between items-center">
                    <div>
                      <Link to={`/tasks/${t.id}`} onClick={(e) => e.stopPropagation()} className="font-medium text-indigo-700 hover:underline">{t.title}</Link>
                      <div className="text-xs text-gray-500">Status: {t.status}</div>
                    </div>
                    <div className="w-40 bg-gray-200 rounded-full h-2.5">
                      <div className="bg-indigo-600 h-2.5 rounded-full" style={{ width: `${t.progress}%` }}></div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-gray-500 text-sm">No tasks bound to this workspace.</p>
          )}
        </div>

        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-lg font-bold mb-4 flex items-center"><FileText className="w-5 h-5 mr-2"/>Files</h3>
          {files.length ? (
            <ul className="text-sm text-gray-700 max-h-96 overflow-auto list-disc pl-6">
              {files.map(f => <li key={f} className="truncate">{f}</li>)}
            </ul>
          ) : (
            <p className="text-gray-500 text-sm">No files found.</p>
          )}
        </div>
      </div>

      <div className="mt-8">
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-lg font-bold mb-6 flex items-center border-b pb-4">
            <ShoppingBag className="w-6 h-6 mr-2 text-indigo-600" />
            Agent Marketplace & Active Personnel
          </h3>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            {/* Active Agents */}
            <div>
              <h4 className="text-sm font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center">
                <Users className="w-4 h-4 mr-2" />
                Authorized Agents in Workspace
              </h4>
              <div className="space-y-3">
                {ws.metadata?.allowed_agents?.length ? (
                  ws.metadata.allowed_agents.map(agentId => {
                    const agent = allAgents.find(a => a.id === agentId);
                    return (
                      <div key={agentId} className="flex items-center justify-between p-3 bg-indigo-50 border border-indigo-100 rounded-xl">
                        <div className="flex items-center space-x-3">
                          <div className="p-2 bg-white rounded-lg text-indigo-600 shadow-sm">
                            <Shield className="w-4 h-4" />
                          </div>
                          <div>
                            <div className="text-sm font-bold text-indigo-900">{agent?.name || agentId}</div>
                            <div className="text-[10px] text-indigo-400 font-mono">{agentId}</div>
                          </div>
                        </div>
                        <button
                          onClick={() => handleRemoveAgent(agentId)}
                          className="p-2 text-indigo-300 hover:text-red-500 transition-colors"
                          title="Remove from workspace"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    );
                  })
                ) : (
                  <p className="text-gray-500 text-sm italic">No agents authorized for this workspace.</p>
                )}
              </div>
            </div>

            {/* Marketplace */}
            <div>
              <h4 className="text-sm font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center">
                <Plus className="w-4 h-4 mr-2" />
                Available from Marketplace
              </h4>
              <div className="grid grid-cols-1 gap-3 max-h-80 overflow-y-auto pr-2">
                {allAgents
                  .filter(a => !ws.metadata?.allowed_agents?.includes(a.id))
                  .map(agent => (
                    <div key={agent.id} className="flex items-center justify-between p-3 border border-gray-100 rounded-xl hover:bg-gray-50 transition-colors">
                      <div className="flex items-center space-x-3">
                        <div className="p-2 bg-gray-100 rounded-lg text-gray-400 font-bold text-[10px]">
                          {agent.domain.slice(0, 3).toUpperCase()}
                        </div>
                        <div>
                          <div className="text-sm font-semibold text-gray-700">{agent.name}</div>
                          <div className="text-[10px] text-gray-400 line-clamp-1">{agent.description}</div>
                        </div>
                      </div>
                      <button
                        onClick={() => handleAddAgent(agent.id)}
                        className="bg-white border border-indigo-200 text-indigo-600 px-3 py-1 rounded-lg text-xs font-bold hover:bg-indigo-600 hover:text-white transition-all shadow-sm"
                      >
                        Add Agent
                      </button>
                    </div>
                  ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default WorkspaceDetails;
