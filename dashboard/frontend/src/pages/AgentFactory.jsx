import React, { useState, useEffect } from 'react';
import { getWorkspaces } from '../api';
import FlowGraph from '../components/factory/FlowGraph';
import AgentControls from '../components/factory/AgentControls';
import InteractionForm from '../components/factory/InteractionForm';
import LogPanel from '../components/factory/LogPanel';
import ProjectTreeView from '../components/factory/ProjectTreeView';
import { Factory } from 'lucide-react';

const AgentFactory = () => {
  const [workspaces, setWorkspaces] = useState([]);
  const [selectedWorkspace, setSelectedWorkspace] = useState('');

  useEffect(() => {
    const fetchWorkspaces = async () => {
      try {
        const res = await getWorkspaces();
        setWorkspaces(res.data || []);
        if (res.data && res.data.length > 0 && !selectedWorkspace) {
          setSelectedWorkspace(res.data[0].name);
        }
      } catch (e) {
        console.error('Failed to fetch workspaces', e);
      }
    };
    fetchWorkspaces();
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-semibold text-gray-800 flex items-center">
          <Factory className="w-6 h-6 mr-2 text-indigo-600" /> Agent Factory
        </h2>
        <div className="flex items-center space-x-2">
          <label className="text-sm font-medium text-gray-700">Workspace:</label>
          <select
            value={selectedWorkspace}
            onChange={(e) => setSelectedWorkspace(e.target.value)}
            className="border border-gray-300 rounded-md px-3 py-1 focus:ring-indigo-500 focus:border-indigo-500"
          >
            <option value="">Select a workspace</option>
            {workspaces.map((ws) => (
              <option key={ws.name} value={ws.name}>
                {ws.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="bg-white p-4 shadow rounded-lg h-[400px]">
        <FlowGraph workspace={selectedWorkspace} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="space-y-6">
          <div className="bg-white p-6 shadow rounded-lg">
            <AgentControls workspace={selectedWorkspace} />
          </div>
          <div className="bg-white p-6 shadow rounded-lg">
            <ProjectTreeView workspace={selectedWorkspace} />
          </div>
        </div>
        <div className="space-y-6">
          <div className="bg-white p-6 shadow rounded-lg">
            <InteractionForm workspace={selectedWorkspace} />
          </div>
          <div className="bg-white p-6 shadow rounded-lg max-h-[500px] overflow-auto">
            <LogPanel workspace={selectedWorkspace} />
          </div>
        </div>
      </div>
    </div>
  );
};

export default AgentFactory;
