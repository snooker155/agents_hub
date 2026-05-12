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
  Layers,
  ThumbsUp,
  FolderGit2,
  RefreshCcw,
  Info,
} from 'lucide-react';
import {
  getOrchestratorSettings,
  updateOrchestratorSettings,
  getOrchestratorRoutingLog,
  getAgents,
  getProjects,
  getNodes,
  getSettings,
  getWorkspace,
} from '../api';
import { useWorkspace } from '../components/WorkspaceContext';

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
  const [settings, setSettings] = useState({ enabled: false, assignment_mode: 'manual' });
  const [agents, setAgents] = useState([]);
  const [routingLog, setRoutingLog] = useState([]);
  const [projects, setProjects] = useState([]);
  const [nodes, setNodes] = useState([]);
  const [selectedProject, setSelectedProject] = useState('');
  const [agentMode, setAgentMode] = useState('local');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [expandedRow, setExpandedRow] = useState(null);

  const fetchData = async () => {
    const settingsWorkspace = selectedWorkspace || 'default';
    try {
      const [settingsResp, agentsResp, routingResp, projectsResp, nodesResp, globalSettingsResp, wsResp] = await Promise.allSettled([
        getOrchestratorSettings(settingsWorkspace),
        getAgents(workspaceFilter),
        getOrchestratorRoutingLog(workspaceFilter),
        workspaceFilter ? getProjects(workspaceFilter) : Promise.resolve({ data: [] }),
        getNodes(workspaceFilter),
        getSettings(),
        workspaceFilter && workspaceFilter !== 'default' ? getWorkspace(workspaceFilter) : Promise.resolve(null),
      ]);
      if (settingsResp.status === 'fulfilled') setSettings(settingsResp.value.data);
      if (agentsResp.status === 'fulfilled') setAgents(agentsResp.value.data);
      if (routingResp.status === 'fulfilled') setRoutingLog(routingResp.value.data || []);
      if (projectsResp.status === 'fulfilled') setProjects(projectsResp.value.data || []);
      if (nodesResp.status === 'fulfilled') setNodes(nodesResp.value.data || []);
      // Agent mode: workspace-specific override takes priority over global setting
      const globalMode = globalSettingsResp.status === 'fulfilled' ? (globalSettingsResp.value.data.agent_mode || 'local') : 'local';
      const wsMode = wsResp.status === 'fulfilled' && wsResp.value ? (wsResp.value.data?.metadata?.settings?.agent_mode || null) : null;
      setAgentMode(wsMode || globalMode);
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
    const settingsWorkspace = selectedWorkspace || 'default';
    if (!settings.enabled && !hasRunningOrchestratorNode) {
      alert('Start an orchestrator node first to enable auto-orchestration.');
      return;
    }
    setSaving(true);
    try {
      const newSettings = { ...settings, enabled: !settings.enabled };
      await updateOrchestratorSettings(newSettings, settingsWorkspace);
      setSettings(newSettings);
    } catch {
      alert('Error updating orchestrator settings');
    } finally {
      setSaving(false);
    }
  };

  const handleAssignmentModeToggle = async () => {
    const settingsWorkspace = selectedWorkspace || 'default';
    setSaving(true);
    try {
      const newMode = settings.assignment_mode === 'manual' ? 'live' : 'manual';
      const newSettings = { ...settings, assignment_mode: newMode };
      await updateOrchestratorSettings(newSettings, settingsWorkspace);
      setSettings(newSettings);
    } catch {
      alert('Error updating assignment mode');
    } finally {
      setSaving(false);
    }
  };

  const handleFollowupModeToggle = async () => {
    const settingsWorkspace = selectedWorkspace || 'default';
    setSaving(true);
    try {
      const newMode = settings.followup_mode === 'continuous' ? 'single' : 'continuous';
      // Mutually exclusive: enabling continuous follow-up disables wait_for_completion
      const newSettings = {
        ...settings,
        followup_mode: newMode,
        wait_for_completion: newMode === 'continuous' ? false : settings.wait_for_completion,
      };
      await updateOrchestratorSettings(newSettings, settingsWorkspace);
      setSettings(newSettings);
    } catch {
      alert('Error updating follow-up mode');
    } finally {
      setSaving(false);
    }
  };

  const handleExecutionModeToggle = async () => {
    const settingsWorkspace = selectedWorkspace || 'default';
    setSaving(true);
    try {
      const newMode = settings.execution_mode === 'node' ? 'subprocess' : 'node';
      const newSettings = { ...settings, execution_mode: newMode, ...(newMode === 'subprocess' ? { enabled: false } : {}) };
      await updateOrchestratorSettings(newSettings, settingsWorkspace);
      setSettings(newSettings);
    } catch {
      alert('Error updating execution mode setting');
    } finally {
      setSaving(false);
    }
  };

  const handleWaitForCompletionToggle = async () => {
    const settingsWorkspace = selectedWorkspace || 'default';
    setSaving(true);
    try {
      const newWait = !settings.wait_for_completion;
      // Mutually exclusive: enabling wait_for_completion disables continuous follow-up
      const newSettings = {
        ...settings,
        wait_for_completion: newWait,
        followup_mode: newWait ? 'single' : settings.followup_mode,
      };
      await updateOrchestratorSettings(newSettings, settingsWorkspace);
      setSettings(newSettings);
    } catch {
      alert('Error updating wait for completion setting');
    } finally {
      setSaving(false);
    }
  };

  const routingEvents = routingLog;

  // Agent lookup map
  const agentMap = Object.fromEntries(agents.map(a => [a.id, a]));
  const runningOrchestratorNodes = nodes.filter(
    (n) => n.agent_id === 'orchestrator' && (n.status === 'running' || n.status === 'starting')
  );
  const hasRunningOrchestratorNode = runningOrchestratorNodes.length > 0;

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
      <div className="flex justify-between items-start">
        <div>
          <h2 className="text-2xl font-bold text-gray-800 flex items-center">
            <Shield className="w-7 h-7 mr-3 text-indigo-600" />
            Central Orchestrator
          </h2>
          <p className="text-gray-500 text-sm">Automated task routing and agent coordination.</p>
        </div>
        {projects.length > 0 && (
          <div className="flex items-center gap-2 text-sm">
            <FolderGit2 className="w-4 h-4 text-emerald-500 flex-shrink-0" />
            <select
              value={selectedProject}
              onChange={(e) => setSelectedProject(e.target.value)}
              className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm text-gray-700 bg-white focus:outline-none focus:ring-2 focus:ring-emerald-400"
            >
              <option value="">All projects</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* Control panel */}
      {!hasRunningOrchestratorNode && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-amber-900 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-amber-600 flex-shrink-0" />
              No running orchestrator node
            </p>
            <p className="text-xs text-amber-800 mt-1">
              Auto-orchestration cannot run until an orchestrator agent node is started.
            </p>
          </div>
          <Link
            to="/nodes"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-amber-900 bg-white border border-amber-300 rounded-lg hover:bg-amber-100 transition-colors whitespace-nowrap"
          >
            Start New Node
            <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </div>
      )}

      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
        <div className="p-8">
          {/* Execution mode toggle */}
          <div className="flex items-center justify-between mb-8 pb-8 border-b border-gray-50">
            <div className="flex items-center space-x-4">
              <div className={`p-4 rounded-2xl ${settings.execution_mode === 'node' ? 'bg-purple-50 text-purple-600' : 'bg-gray-50 text-gray-400'}`}>
                <Cpu className="w-8 h-8" />
              </div>
              <div>
                <h3 className="text-xl font-bold text-gray-900">
                  Execution Mode: {settings.execution_mode === 'node' ? 'Agent Nodes' : 'Subprocesses'}
                </h3>
                <p className="text-gray-500 text-sm max-w-md">
                  {settings.execution_mode === 'node'
                    ? 'After assignment, tasks wait for a running worker agent node to pick them up and process them.'
                    : 'After assignment, tasks are immediately executed in a new subprocess. Running worker nodes will stay idle.'
                  }
                </p>
                {settings.execution_mode === 'node' && (
                  <div className={`inline-flex items-center gap-1.5 mt-2 px-2.5 py-1 rounded-full text-xs font-medium border ${
                    agentMode === 'docker'
                      ? 'bg-blue-50 text-blue-700 border-blue-200'
                      : 'bg-gray-50 text-gray-600 border-gray-200'
                  }`}>
                    <Info className="w-3 h-3" />
                    Agent mode: <span className="font-semibold">{agentMode === 'docker' ? 'Docker containers' : 'Local processes'}</span>
                  </div>
                )}
              </div>
            </div>
            <button
              onClick={handleExecutionModeToggle}
              disabled={saving}
              className={`relative inline-flex h-10 w-20 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-purple-500 focus:ring-offset-2 ${
                settings.execution_mode === 'node' ? 'bg-purple-500' : 'bg-gray-200'
              }`}
            >
              <span className="sr-only">Toggle Execution Mode</span>
              <span
                className={`inline-block h-8 w-8 transform rounded-full bg-white transition-transform shadow-sm ${
                  settings.execution_mode === 'node' ? 'translate-x-11' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* Auto-orchestration toggle — only relevant in node execution mode */}
          <div className={`flex items-center justify-between mb-8 pb-8 border-b border-gray-50 ${settings.execution_mode !== 'node' ? 'opacity-40 pointer-events-none' : ''}`}>
            <div className="flex items-center space-x-4">
              <div className={`p-4 rounded-2xl ${settings.enabled && settings.execution_mode === 'node' ? 'bg-green-50 text-green-600' : 'bg-gray-50 text-gray-400'}`}>
                <Zap className={`w-8 h-8 ${settings.enabled && settings.execution_mode === 'node' ? 'fill-current' : ''}`} />
              </div>
              <div>
                <h3 className="text-xl font-bold text-gray-900">
                  Auto-Orchestration is {settings.enabled ? 'Active' : 'Paused'}
                </h3>
                <p className="text-gray-500 text-sm max-w-md">
                  {settings.execution_mode !== 'node'
                    ? 'Switch to Agent Nodes execution mode to enable auto-orchestration.'
                    : 'When active, all incoming tasks are automatically analysed and routed to the most suitable specialized agent.'
                  }
                </p>
              </div>
            </div>
            <button
              onClick={handleToggle}
              disabled={saving || settings.execution_mode !== 'node' || (!settings.enabled && !hasRunningOrchestratorNode)}
              className={`relative inline-flex h-10 w-20 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 ${
                settings.enabled && settings.execution_mode === 'node' ? 'bg-indigo-600' : 'bg-gray-200'
              }`}
            >
              <span className="sr-only">Toggle Orchestrator</span>
              <span
                className={`inline-block h-8 w-8 transform rounded-full bg-white transition-transform shadow-sm ${
                  settings.enabled && settings.execution_mode === 'node' ? 'translate-x-11' : 'translate-x-1'
                }`}
              />
            </button>
          </div>
          {settings.execution_mode === 'node' && !settings.enabled && !hasRunningOrchestratorNode && (
            <p className="text-xs text-amber-700 -mt-5 mb-6">
              Enable is blocked until an orchestrator node is running.
            </p>
          )}

          {/* Assignment mode toggle */}
          <div className="flex items-center justify-between mb-8 pb-8 border-b border-gray-50">
            <div className="flex items-center space-x-4">
              <div className={`p-4 rounded-2xl ${settings.assignment_mode === 'live' ? 'bg-amber-50 text-amber-600' : 'bg-gray-50 text-gray-400'}`}>
                <ThumbsUp className="w-8 h-8" />
              </div>
              <div>
                <h3 className="text-xl font-bold text-gray-900">
                  Assignment Mode: {settings.assignment_mode === 'manual' ? 'Manual Approval' : 'Live (Auto-run)'}
                </h3>
                <p className="text-gray-500 text-sm max-w-md">
                  {settings.assignment_mode === 'manual'
                    ? 'Agent assignments wait for your approval before executing. You can approve or reject each assignment in the task board.'
                    : 'Agent assignments start executing immediately after being assigned.'
                  }
                </p>
              </div>
            </div>
            <button
              onClick={handleAssignmentModeToggle}
              disabled={saving}
              className={`relative inline-flex h-10 w-20 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-amber-500 focus:ring-offset-2 ${
                settings.assignment_mode === 'live' ? 'bg-amber-500' : 'bg-gray-200'
              }`}
            >
              <span className="sr-only">Toggle Assignment Mode</span>
              <span
                className={`inline-block h-8 w-8 transform rounded-full bg-white transition-transform shadow-sm ${
                  settings.assignment_mode === 'live' ? 'translate-x-11' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* Wait for completion toggle */}
          <div className="flex items-center justify-between mb-8 pb-8 border-b border-gray-50">
            <div className="flex items-center space-x-4">
              <div className={`p-4 rounded-2xl ${settings.wait_for_completion ? 'bg-indigo-50 text-indigo-600' : 'bg-gray-50 text-gray-400'}`}>
                <Clock className="w-8 h-8" />
              </div>
              <div>
                <h3 className="text-xl font-bold text-gray-900">
                  Wait for Completion: {settings.wait_for_completion ? 'On' : 'Off'}
                </h3>
                <p className="text-gray-500 text-sm max-w-md">
                  {settings.wait_for_completion
                    ? 'The orchestrator polls the agent until it finishes, then reports the result.'
                    : 'The orchestrator starts the agent and stops immediately. You can check back later for results.'
                  }
                </p>
              </div>
            </div>
            <button
              onClick={handleWaitForCompletionToggle}
              disabled={saving}
              className={`relative inline-flex h-10 w-20 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 ${
                settings.wait_for_completion ? 'bg-indigo-500' : 'bg-gray-200'
              }`}
            >
              <span className="sr-only">Toggle Wait for Completion</span>
              <span
                className={`inline-block h-8 w-8 transform rounded-full bg-white transition-transform shadow-sm ${
                  settings.wait_for_completion ? 'translate-x-11' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* Follow-up mode toggle */}
          <div className="flex items-center justify-between mb-8 pb-8 border-b border-gray-50">
            <div className="flex items-center space-x-4">
              <div className={`p-4 rounded-2xl ${settings.followup_mode === 'continuous' ? 'bg-indigo-50 text-indigo-600' : 'bg-gray-50 text-gray-400'}`}>
                <RefreshCcw className="w-8 h-8" />
              </div>
              <div>
                <h3 className="text-xl font-bold text-gray-900">
                  Follow-up Mode: {settings.followup_mode === 'continuous' ? 'Continuous' : 'Single Run'}
                </h3>
                <p className="text-gray-500 text-sm max-w-md">
                  {settings.followup_mode === 'continuous'
                    ? 'After an agent finishes, the orchestrator is automatically re-invoked to chain the next step, assign a reviewer, or mark the task done.'
                    : 'The orchestrator runs once per task and stops. Follow-up steps (review, finalise) must be triggered manually.'
                  }
                </p>
              </div>
            </div>
            <button
              onClick={handleFollowupModeToggle}
              disabled={saving}
              className={`relative inline-flex h-10 w-20 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 ${
                settings.followup_mode === 'continuous' ? 'bg-indigo-500' : 'bg-gray-200'
              }`}
            >
              <span className="sr-only">Toggle Follow-up Mode</span>
              <span
                className={`inline-block h-8 w-8 transform rounded-full bg-white transition-transform shadow-sm ${
                  settings.followup_mode === 'continuous' ? 'translate-x-11' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* Compatibility callout */}
          {(() => {
            return (
              <div className="mb-8 pb-8 border-b border-gray-50 space-y-3">
                {false && null /* conflict warning removed: toggles are now mutually exclusive */}

                <div className="flex items-start gap-3 bg-gray-50 border border-gray-100 rounded-xl p-4">
                  <Info className="w-4 h-4 text-gray-400 flex-shrink-0 mt-0.5" />
                  <div className="w-full">
                    <p className="text-xs font-semibold text-gray-600 mb-2">Recommended mode combinations</p>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div className={`rounded-lg p-3 border ${!settings.wait_for_completion && settings.followup_mode === 'continuous' ? 'bg-indigo-50 border-indigo-200' : 'bg-white border-gray-100'}`}>
                        <p className="font-semibold text-gray-700 mb-1">Fire &amp; Chain</p>
                        <p className="text-gray-500">Wait for Completion <span className="font-medium text-gray-700">off</span> + Follow-up <span className="font-medium text-gray-700">Continuous</span></p>
                        <p className="text-gray-400 mt-1">Orchestrator starts the agent and exits. A new orchestrator session is automatically triggered when the worker finishes to chain the next step.</p>
                      </div>
                      <div className={`rounded-lg p-3 border ${settings.wait_for_completion && settings.followup_mode !== 'continuous' ? 'bg-indigo-50 border-indigo-200' : 'bg-white border-gray-100'}`}>
                        <p className="font-semibold text-gray-700 mb-1">Poll &amp; Report</p>
                        <p className="text-gray-500">Wait for Completion <span className="font-medium text-gray-700">on</span> + Follow-up <span className="font-medium text-gray-700">Single</span></p>
                        <p className="text-gray-400 mt-1">Orchestrator stays in session, polls until the worker finishes, then reports the orchestration summary. No automatic chaining.</p>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            );
          })()}

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
                    {agents.filter(a => a.status !== 'offline').length}
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
            {routingEvents.map(entry => {
              const agentSpec = agentMap[entry.agent_id];
              const isExpanded = expandedRow === entry.id;
              const domainColor = agentSpec
                ? DOMAIN_COLORS[agentSpec.domain] || 'bg-gray-100 text-gray-600'
                : 'bg-gray-100 text-gray-600';

              return (
                <div key={entry.id}>
                  {/* Main row */}
                  <div
                    className="px-6 py-4 hover:bg-gray-50 transition-colors cursor-pointer"
                    onClick={() => setExpandedRow(isExpanded ? null : entry.id)}
                  >
                    <div className="flex items-center gap-4">
                      <div className="flex items-center gap-2 min-w-0 flex-1">
                        <div className="flex-shrink-0">
                          <div className="p-1.5 bg-indigo-50 rounded-lg" title="Routed by orchestrator">
                            <Layers className="w-3.5 h-3.5 text-indigo-500" />
                          </div>
                        </div>

                        {/* Task → Agent */}
                        <div className="flex items-center gap-2 min-w-0 flex-1">
                          <Link
                            to={`/tasks/${entry.task_id}`}
                            onClick={e => e.stopPropagation()}
                            className="text-sm font-semibold text-gray-800 hover:text-indigo-600 truncate max-w-[200px]"
                          >
                            {entry.task_title}
                          </Link>
                          <ArrowRight className="w-3.5 h-3.5 text-gray-300 flex-shrink-0" />
                          <Link
                            to={`/agents/${entry.agent_id}`}
                            onClick={e => e.stopPropagation()}
                            className="flex items-center gap-1.5 flex-shrink-0"
                          >
                            <Bot className="w-3.5 h-3.5 text-indigo-500" />
                            <span className="text-sm font-bold text-indigo-700 hover:underline">
                              {entry.agent_id}
                            </span>
                          </Link>
                          {agentSpec?.domain && (
                            <span className={`hidden sm:inline text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${domainColor}`}>
                              {agentSpec.domain}
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Right side: timestamp + workspace */}
                      <div className="flex items-center gap-3 flex-shrink-0">
                        {entry.workspace && (
                          <span className="hidden md:inline text-xs text-gray-400">{entry.workspace}</span>
                        )}
                        <span className="text-xs text-gray-300 flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {new Date(entry.timestamp).toLocaleString()}
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
                            {entry.reason
                              ? entry.reason
                              : <span className="italic text-gray-300">No routing reason recorded</span>}
                          </p>
                          <div className="mt-2 flex items-center gap-2 text-xs text-gray-400">
                            <Layers className="w-3 h-3 text-indigo-400" />
                            <span>Orchestrator reasoning</span>
                          </div>
                        </div>

                        {/* Assignment details */}
                        <div>
                          <div className="text-[10px] uppercase font-bold text-gray-400 mb-1.5 tracking-wider">
                            Assignment Details
                          </div>
                          <div className="bg-white rounded-lg p-3 border border-gray-100 space-y-2">
                            <DetailRow label="Task ID" value={<span className="text-[11px]">{entry.task_id}</span>} />
                            <DetailRow label="Agent" value={
                              <Link to={`/agents/${entry.agent_id}`} className="text-indigo-600 hover:underline font-medium">
                                {entry.agent_id}
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
                            {entry.workspace && (
                              <DetailRow label="Workspace" value={
                                <Link to={`/workspaces/${entry.workspace}`} className="text-indigo-600 hover:underline">
                                  {entry.workspace}
                                </Link>
                              } />
                            )}
                          </div>
                        </div>
                      </div>

                      <div className="mt-3">
                        <Link
                          to={`/tasks/${entry.task_id}`}
                          className="text-xs text-indigo-600 hover:underline flex items-center gap-1"
                        >
                          View task <ArrowRight className="w-3 h-3" />
                        </Link>
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
