import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  Zap,
  Shield,
  CheckCircle2,
  RefreshCw,
  Cpu,
  Workflow,
  ArrowRight,
  Bot,
  Clock,
  Network,
  ChevronDown,
  ChevronUp,
  AlertCircle,
  Play,
  Square,
  User,
  Layers,
} from 'lucide-react';
import {
  getOrchestratorSettings,
  updateOrchestratorSettings,
  getAgents,
  getTasks,
} from '../api';
import { useWorkspace } from '../components/WorkspaceContext';

const AGENT_STATE_CONFIG = {
  running:   { label: 'Running',   color: 'bg-orange-50 text-orange-700 border-orange-100',  icon: Play,         dot: 'bg-orange-400 animate-pulse' },
  completed: { label: 'Completed', color: 'bg-green-50 text-green-700 border-green-100',     icon: CheckCircle2, dot: 'bg-green-400' },
  failed:    { label: 'Failed',    color: 'bg-red-50 text-red-700 border-red-100',            icon: AlertCircle,  dot: 'bg-red-400' },
  stopped:   { label: 'Stopped',   color: 'bg-gray-50 text-gray-500 border-gray-100',        icon: Square,       dot: 'bg-gray-300' },
  assigned:  { label: 'Assigned',  color: 'bg-blue-50 text-blue-700 border-blue-100',        icon: Bot,          dot: 'bg-blue-400' },
  none:      { label: 'Pending',   color: 'bg-gray-50 text-gray-400 border-gray-100',        icon: Clock,        dot: 'bg-gray-200' },
};

const DOMAIN_COLORS = {
  orchestration: 'bg-indigo-100 text-indigo-700',
  development:   'bg-blue-100 text-blue-700',
  management:    'bg-teal-100 text-teal-700',
  analysis:      'bg-amber-100 text-amber-700',
  testing:       'bg-green-100 text-green-700',
  design:        'bg-pink-100 text-pink-700',
  operations:    'bg-orange-100 text-orange-700',
  automation:    'bg-purple-100 text-purple-700',
};

const Orchestrator = () => {
  const { selectedWorkspace, workspaceFilter, liveUpdates } = useWorkspace();
  const [settings, setSettings] = useState({ enabled: false });
  const [agents, setAgents] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [expandedRow, setExpandedRow] = useState(null);

  const fetchData = async () => {
    try {
      const [settingsResp, agentsResp, tasksResp] = await Promise.allSettled([
        getOrchestratorSettings(),
        getAgents(workspaceFilter),
        getTasks(workspaceFilter),
      ]);
      if (settingsResp.status === 'fulfilled') setSettings(settingsResp.value.data);
      if (agentsResp.status === 'fulfilled') setAgents(agentsResp.value.data);
      if (tasksResp.status === 'fulfilled') setTasks(tasksResp.value.data);
    } catch (error) {
      console.error('Error fetching orchestrator data:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    if (!liveUpdates) return;
    const interval = setInterval(fetchData, 8000);
    return () => clearInterval(interval);
  }, [selectedWorkspace, liveUpdates]);

  const handleToggle = async () => {
    setSaving(true);
    try {
      const newSettings = { ...settings, enabled: !settings.enabled };
      await updateOrchestratorSettings(newSettings);
      setSettings(newSettings);
    } catch {
      alert('Error updating orchestrator settings');
    } finally {
      setSaving(false);
    }
  };

  // Build routing events: tasks with an assigned agent
  const routingEvents = tasks
    .filter(t => t.assigned_agent_type)
    .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));

  // Agent lookup map
  const agentMap = Object.fromEntries(agents.map(a => [a.id, a]));

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <RefreshCw className="w-8 h-8 text-indigo-400 animate-spin mb-4" />
        <p className="text-gray-500">Loading orchestrator configuration...</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold text-gray-800 flex items-center">
            <Shield className="w-7 h-7 mr-3 text-indigo-600" />
            Central Orchestrator
          </h2>
          <p className="text-gray-500 text-sm">Automated task routing and agent coordination.</p>
        </div>
      </div>

      {/* Control panel */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
        <div className="p-8">
          {/* Toggle row */}
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
                  When active, all incoming tasks are automatically analysed and routed to the most suitable specialized agent.
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

          {/* Logic + cluster */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            <div className="space-y-6">
              <h4 className="font-bold text-gray-800 flex items-center text-sm">
                <Workflow className="w-4 h-4 mr-2 text-indigo-500" />
                Orchestration Logic
              </h4>
              <ul className="space-y-3">
                {[
                  'Analyses task title & description to identify required skills.',
                  'Checks agent availability and domain capability match.',
                  'Triggers the decomposer for complex, high-level goals.',
                  'Routes subtasks to the most appropriate specialized agent.',
                ].map(text => (
                  <li key={text} className="flex items-start">
                    <CheckCircle2 className="w-4 h-4 text-green-500 mr-3 mt-0.5 flex-shrink-0" />
                    <span className="text-sm text-gray-600">{text}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="bg-indigo-50 rounded-2xl p-6 border border-indigo-100">
              <h4 className="font-bold text-indigo-900 mb-4 flex items-center text-sm">
                <Cpu className="w-4 h-4 mr-2" />
                Cluster Status
              </h4>
              <div className="space-y-3">
                <div className="flex justify-between items-center text-sm">
                  <span className="text-indigo-700">Available Agents</span>
                  <span className="font-bold text-indigo-900">{agents.length}</span>
                </div>
                <div className="flex justify-between items-center text-sm">
                  <span className="text-indigo-700">Healthy Nodes</span>
                  <span className="font-bold text-indigo-900">
                    {agents.filter(a => !a.is_remote || a.status !== 'offline').length}
                  </span>
                </div>
                <div className="flex justify-between items-center text-sm">
                  <span className="text-indigo-700">Tasks Routed</span>
                  <span className="font-bold text-indigo-900">{routingEvents.length}</span>
                </div>
                <div className="mt-3 pt-3 border-t border-indigo-200">
                  <div className="text-[10px] uppercase font-bold text-indigo-400 mb-2">Connected Agents</div>
                  <div className="flex flex-wrap gap-1.5">
                    {agents.slice(0, 6).map(a => (
                      <Link
                        key={a.id}
                        to={`/agents/${a.id}`}
                        className="px-2 py-0.5 bg-white rounded text-[10px] font-bold text-indigo-600 border border-indigo-100 hover:bg-indigo-600 hover:text-white transition-colors"
                      >
                        {a.id}
                      </Link>
                    ))}
                    {agents.length > 6 && (
                      <span className="text-[10px] text-indigo-400 self-center">+{agents.length - 6} more</span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Routing History */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
          <h3 className="font-bold text-gray-800 flex items-center text-sm">
            <Network className="w-4 h-4 mr-2 text-indigo-500" />
            Routing History
            {routingEvents.length > 0 && (
              <span className="ml-2 px-2 py-0.5 text-xs bg-indigo-50 text-indigo-600 border border-indigo-100 rounded-full font-semibold">
                {routingEvents.length}
              </span>
            )}
          </h3>
          <button
            onClick={fetchData}
            className="text-xs text-gray-400 hover:text-indigo-600 flex items-center gap-1 transition-colors"
          >
            <RefreshCw className="w-3 h-3" />
            Refresh
          </button>
        </div>

        {routingEvents.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-gray-300">
            <Network className="w-12 h-12 mb-3" />
            <p className="text-sm font-medium text-gray-400">No routing decisions recorded yet</p>
            <p className="text-xs text-gray-400 mt-1">Create a task to see orchestrator routing in action</p>
          </div>
        ) : (
          <div className="divide-y divide-gray-50">
            {routingEvents.map(task => {
              const agentSpec = agentMap[task.assigned_agent_type];
              const state = AGENT_STATE_CONFIG[task.agent_state] || AGENT_STATE_CONFIG.none;
              const StateIcon = state.icon;
              const isAuto = task.created_by === 'orchestrator';
              const isExpanded = expandedRow === task.id;
              const domainColor = agentSpec
                ? DOMAIN_COLORS[agentSpec.domain] || 'bg-gray-100 text-gray-600'
                : 'bg-gray-100 text-gray-600';

              return (
                <div key={task.id}>
                  {/* Main row */}
                  <div
                    className="px-6 py-4 hover:bg-gray-50 transition-colors cursor-pointer"
                    onClick={() => setExpandedRow(isExpanded ? null : task.id)}
                  >
                    <div className="flex items-center gap-4">
                      {/* Route indicator */}
                      <div className="flex items-center gap-2 min-w-0 flex-1">
                        <div className="flex-shrink-0">
                          {isAuto ? (
                            <div className="p-1.5 bg-indigo-50 rounded-lg" title="Auto-routed by orchestrator">
                              <Layers className="w-3.5 h-3.5 text-indigo-500" />
                            </div>
                          ) : (
                            <div className="p-1.5 bg-gray-50 rounded-lg" title="Manually assigned">
                              <User className="w-3.5 h-3.5 text-gray-400" />
                            </div>
                          )}
                        </div>

                        {/* Task → Agent */}
                        <div className="flex items-center gap-2 min-w-0 flex-1">
                          <Link
                            to={`/tasks/${task.id}`}
                            onClick={e => e.stopPropagation()}
                            className="text-sm font-semibold text-gray-800 hover:text-indigo-600 truncate max-w-[200px]"
                          >
                            {task.title}
                          </Link>
                          <ArrowRight className="w-3.5 h-3.5 text-gray-300 flex-shrink-0" />
                          <Link
                            to={`/agents/${task.assigned_agent_type}`}
                            onClick={e => e.stopPropagation()}
                            className="flex items-center gap-1.5 flex-shrink-0"
                          >
                            <Bot className="w-3.5 h-3.5 text-indigo-500" />
                            <span className="text-sm font-bold text-indigo-700 hover:underline">
                              {task.assigned_agent_type}
                            </span>
                          </Link>
                          {agentSpec?.domain && (
                            <span className={`hidden sm:inline text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${domainColor}`}>
                              {agentSpec.domain}
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Right side: status + meta */}
                      <div className="flex items-center gap-3 flex-shrink-0">
                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${state.color}`}>
                          <span className={`w-1.5 h-1.5 rounded-full ${state.dot}`} />
                          {state.label}
                        </span>
                        {task.workspace && (
                          <span className="hidden md:inline text-xs text-gray-400 font-mono">{task.workspace}</span>
                        )}
                        <span className="text-xs text-gray-300 flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {new Date(task.updated_at).toLocaleString()}
                        </span>
                        {isExpanded
                          ? <ChevronUp className="w-4 h-4 text-gray-300" />
                          : <ChevronDown className="w-4 h-4 text-gray-300" />}
                      </div>
                    </div>
                  </div>

                  {/* Expanded detail */}
                  {isExpanded && (
                    <div className="px-6 pb-5 bg-gray-50 border-t border-gray-100">
                      <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                        {/* Routing reason */}
                        <div>
                          <div className="text-[10px] uppercase font-bold text-gray-400 mb-1.5 tracking-wider">
                            Routing Reason
                          </div>
                          <p className="text-sm text-gray-600 leading-relaxed bg-white rounded-lg p-3 border border-gray-100">
                            {task.description
                              ? task.description
                              : <span className="italic text-gray-300">No description provided</span>}
                          </p>
                          <div className="mt-2 flex items-center gap-2 text-xs text-gray-400">
                            {isAuto ? (
                              <>
                                <Layers className="w-3 h-3 text-indigo-400" />
                                <span>Auto-routed by orchestrator based on task description and agent capabilities</span>
                              </>
                            ) : (
                              <>
                                <User className="w-3 h-3" />
                                <span>Manually assigned by user</span>
                              </>
                            )}
                          </div>
                        </div>

                        {/* Assignment details */}
                        <div>
                          <div className="text-[10px] uppercase font-bold text-gray-400 mb-1.5 tracking-wider">
                            Assignment Details
                          </div>
                          <div className="bg-white rounded-lg p-3 border border-gray-100 space-y-2">
                            <DetailRow label="Task ID" value={<span className="font-mono text-[11px]">{task.id}</span>} />
                            <DetailRow label="Agent" value={
                              <Link to={`/agents/${task.assigned_agent_type}`} className="text-indigo-600 hover:underline font-medium">
                                {task.assigned_agent_type}
                              </Link>
                            } />
                            {agentSpec?.domain && (
                              <DetailRow label="Domain" value={
                                <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${domainColor}`}>
                                  {agentSpec.domain}
                                </span>
                              } />
                            )}
                            {agentSpec?.type && (
                              <DetailRow label="Agent Type" value={agentSpec.type} />
                            )}
                            <DetailRow label="Task Status" value={task.status} />
                            {task.workspace && (
                              <DetailRow label="Workspace" value={
                                <Link to={`/workspaces/${task.workspace}`} className="text-indigo-600 hover:underline">
                                  {task.workspace}
                                </Link>
                              } />
                            )}
                            {task.blocked_reason && (
                              <DetailRow label="Blocked Reason" value={
                                <span className="text-red-600">{task.blocked_reason}</span>
                              } />
                            )}
                          </div>
                        </div>
                      </div>

                      <div className="mt-3 flex gap-2">
                        <Link
                          to={`/tasks/${task.id}`}
                          className="text-xs text-indigo-600 hover:underline flex items-center gap-1"
                        >
                          View full task <ArrowRight className="w-3 h-3" />
                        </Link>
                        {task.assigned_agent_run_id && (
                          <Link
                            to={`/sessions/${task.assigned_agent_run_id}`}
                            className="text-xs text-gray-500 hover:text-indigo-600 hover:underline flex items-center gap-1 ml-4"
                          >
                            View session <ArrowRight className="w-3 h-3" />
                          </Link>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

const DetailRow = ({ label, value }) => (
  <div className="flex items-start justify-between gap-2 text-xs">
    <span className="text-gray-400 font-medium flex-shrink-0">{label}</span>
    <span className="text-gray-700 text-right">{value}</span>
  </div>
);

export default Orchestrator;
