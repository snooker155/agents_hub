import React, { useState } from 'react';
import { runFactoryAgent } from '../../api';

function AgentControls({ workspace }) {
  const [description, setDescription] = useState('');

  const runAgent = async (agent, action = null) => {
    if (!workspace) {
      alert("Please select a workspace first");
      return;
    }
    try {
      await runFactoryAgent({
        agent,
        action,
        description: agent === 'graph' || agent === 'pm' ? description : null,
        workspace
      });
      alert(`Started ${agent} ${action || ''}`);
    } catch (e) {
      alert("Failed to start agent");
    }
  };

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-medium text-gray-900 border-b pb-2">Agent Controls</h3>
      <div className="space-y-2">
        <textarea
          placeholder="Project Description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="w-full border border-gray-300 rounded-md px-3 py-2 focus:ring-indigo-500 focus:border-indigo-500"
          rows={3}
        />
        <button
          onClick={() => runAgent('graph')}
          className="w-full bg-indigo-600 text-white px-4 py-2 rounded-md hover:bg-indigo-700 transition-colors font-medium"
        >
          Run Full Graph
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2 mt-4">
        <button onClick={() => runAgent('pm', 'intake')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">PM Intake</button>
        <button onClick={() => runAgent('ba', 'generate')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">BA Generate</button>
        <button onClick={() => runAgent('sd', 'generate')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">SD Generate</button>
        <button onClick={() => runAgent('tl', 'split')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">TL Split Tasks</button>
        <button onClick={() => runAgent('be')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">Run BE Dev</button>
        <button onClick={() => runAgent('fe')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">Run FE Dev</button>
        <button onClick={() => runAgent('qa')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">Run QA</button>
        <button onClick={() => runAgent('ops')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">Run DevOps</button>
      </div>
    </div>
  );
}

export default AgentControls;
