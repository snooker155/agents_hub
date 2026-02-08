import React, { useState, useEffect } from 'react';
import {
  Zap,
  Settings,
  Shield,
  CheckCircle2,
  XCircle,
  RefreshCw,
  Cpu,
  Workflow
} from 'lucide-react';
import { getOrchestratorSettings, updateOrchestratorSettings, getAgents } from '../api';

const Orchestrator = () => {
  const [settings, setSettings] = useState({ enabled: false });
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [settingsResp, agentsResp] = await Promise.all([
          getOrchestratorSettings(),
          getAgents()
        ]);
        setSettings(settingsResp.data);
        setAgents(agentsResp.data);
      } catch (error) {
        console.error('Error fetching orchestrator data:', error);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  const handleToggle = async () => {
    setSaving(true);
    try {
      const newSettings = { ...settings, enabled: !settings.enabled };
      await updateOrchestratorSettings(newSettings);
      setSettings(newSettings);
    } catch (error) {
      alert('Error updating orchestrator settings');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <RefreshCw className="w-8 h-8 text-indigo-400 animate-spin mb-4" />
        <p className="text-gray-500">Loading orchestrator configuration...</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold text-gray-800 flex items-center">
            <Shield className="w-7 h-7 mr-3 text-indigo-600" />
            Central Orchestrator
          </h2>
          <p className="text-gray-500 text-sm">Automated task routing and agent coordination.</p>
        </div>
      </div>

      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
        <div className="p-8">
          <div className="flex items-center justify-between mb-8 pb-8 border-b border-gray-50">
            <div className="flex items-center space-x-4">
              <div className={`p-4 rounded-2xl ${settings.enabled ? 'bg-green-50 text-green-600' : 'bg-gray-50 text-gray-400'}`}>
                <Zap className={`w-8 h-8 ${settings.enabled ? 'fill-current' : ''}`} />
              </div>
              <div>
                <h3 className="text-xl font-bold text-gray-900">
                  Auto-Orchestration is {settings.enabled ? 'Active' : 'Paused'}
                </h3>
                <p className="text-gray-500 text-sm max-w-md">
                  When active, all incoming user tasks are automatically processed by the orchestrator to assign specialized agents.
                </p>
              </div>
            </div>
            <button
              onClick={handleToggle}
              disabled={saving}
              className={`relative inline-flex h-10 w-20 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 ${
                settings.enabled ? 'bg-indigo-600' : 'bg-gray-200'
              }`}
            >
              <span className="sr-only">Toggle Orchestrator</span>
              <span
                className={`inline-block h-8 w-8 transform rounded-full bg-white transition-transform shadow-sm ${
                  settings.enabled ? 'translate-x-11' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            <div className="space-y-6">
              <h4 className="font-bold text-gray-800 flex items-center">
                <Workflow className="w-5 h-5 mr-2 text-indigo-500" />
                Orchestration Logic
              </h4>
              <ul className="space-y-4">
                <li className="flex items-start">
                  <CheckCircle2 className="w-5 h-5 text-green-500 mr-3 mt-0.5 flex-shrink-0" />
                  <span className="text-sm text-gray-600">Analyzes task description to identify required skills.</span>
                </li>
                <li className="flex items-start">
                  <CheckCircle2 className="w-5 h-5 text-green-500 mr-3 mt-0.5 flex-shrink-0" />
                  <span className="text-sm text-gray-600">Checks agent availability and capacity slots.</span>
                </li>
                <li className="flex items-start">
                  <CheckCircle2 className="w-5 h-5 text-green-500 mr-3 mt-0.5 flex-shrink-0" />
                  <span className="text-sm text-gray-600">Triggers decomposition for complex, high-level goals.</span>
                </li>
              </ul>
            </div>

            <div className="bg-indigo-50 rounded-2xl p-6 border border-indigo-100">
              <h4 className="font-bold text-indigo-900 mb-4 flex items-center">
                <Cpu className="w-5 h-5 mr-2" />
                Cluster Status
              </h4>
              <div className="space-y-4">
                <div className="flex justify-between items-center text-sm">
                  <span className="text-indigo-700">Available Agents</span>
                  <span className="font-bold text-indigo-900">{agents.length}</span>
                </div>
                <div className="flex justify-between items-center text-sm">
                  <span className="text-indigo-700">Healthy Nodes</span>
                  <span className="font-bold text-indigo-900">{agents.filter(a => !a.is_remote || a.status !== 'offline').length}</span>
                </div>
                <div className="mt-4 pt-4 border-t border-indigo-200">
                  <div className="text-[10px] uppercase font-bold text-indigo-400 mb-2">Connected Intelligence</div>
                  <div className="flex flex-wrap gap-2">
                    {agents.slice(0, 5).map(a => (
                      <span key={a.id} className="px-2 py-1 bg-white rounded text-[10px] font-bold text-indigo-600 border border-indigo-100">
                        {a.id}
                      </span>
                    ))}
                    {agents.length > 5 && <span className="text-[10px] text-indigo-400">+{agents.length - 5} more</span>}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Orchestrator;
