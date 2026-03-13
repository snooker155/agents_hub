import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  Users,
  CheckCircle2,
  Clock,
  Database,
  Zap,
  Brain,
  GitBranch,
  Server,
  MessageSquare,
  Wrench,
  FileCode2,
  Settings as SettingsIcon,
  Network,
  TrendingUp,
  AlertCircle,
  Play,
  ChevronRight,
  Layers,
  Bot,
  FlaskConical,
} from 'lucide-react';
import {
  getStats,
  getAgents,
  getRuns,
  getSharedMemories,
  getNodes,
  listFlows,
  getSessions,
} from '../api';
import { useWorkspace } from '../components/WorkspaceContext';

const Dashboard = () => {
  const { selectedWorkspace, workspaceFilter, liveUpdates } = useWorkspace();
  const [stats, setStats] = useState(null);
  const [agents, setAgents] = useState([]);
  const [runs, setRuns] = useState([]);
  const [memories, setMemories] = useState([]);
  const [nodes, setNodes] = useState([]);
  const [flows, setFlows] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const fetchData = async () => {
    try {
      const sessParams = { limit: 10 };
      if (workspaceFilter) sessParams.workspace = workspaceFilter;
      const [statsResp, agentsResp, runsResp, memResp, nodesResp, flowsResp, sessResp] =
        await Promise.allSettled([
          getStats(workspaceFilter),
          getAgents(workspaceFilter),
          getRuns(workspaceFilter),
          getSharedMemories(workspaceFilter),
          getNodes(workspaceFilter),
          listFlows(workspaceFilter),
          getSessions(sessParams),
        ]);

      if (statsResp.status === 'fulfilled') setStats(statsResp.value.data);
      if (agentsResp.status === 'fulfilled') setAgents(agentsResp.value.data);
      if (runsResp.status === 'fulfilled') setRuns(runsResp.value.data);
      if (memResp.status === 'fulfilled') setMemories(memResp.value.data);
      if (nodesResp.status === 'fulfilled') setNodes(nodesResp.value.data);
      if (flowsResp.status === 'fulfilled') setFlows(flowsResp.value.data);
      if (sessResp.status === 'fulfilled') setSessions(sessResp.value.data);

      setLoading(false);
    } catch (error) {
      console.error('Error fetching dashboard data:', error);
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    if (!liveUpdates) return;
    const interval = setInterval(fetchData, 8000);
    return () => clearInterval(interval);
  }, [selectedWorkspace, liveUpdates]);

  if (loading || !stats) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600"></div>
      </div>
    );
  }

  const activePods = runs.filter(r => r.status === 'running');
  const activeNodes = nodes.filter(n => n.status === 'running');
  const ragIndexedFiles = memories.reduce((acc, m) => {
    return acc + (m.files || []).filter(f => f.rag_status === 'indexed').length;
  }, 0);
  const totalMemoryFiles = memories.reduce((acc, m) => acc + (m.files || []).length, 0);

  const sessionSuccessRate = sessions.length > 0
    ? Math.round((sessions.filter(s => s.status === 'completed').length / sessions.length) * 100)
    : 0;

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold text-gray-800">System Overview</h2>
          <p className="text-sm text-gray-500 mt-0.5">
            {workspaceFilter ? `Workspace: ${workspaceFilter}` : 'All workspaces'}
          </p>
        </div>
        <span className="flex items-center text-xs bg-blue-50 text-blue-700 px-2.5 py-1 rounded-full border border-blue-100 font-medium">
          <Activity className="w-3 h-3 mr-1" />
          Cluster Healthy
        </span>
      </div>

      {/* Top Stats — 6 cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
        <StatCard
          title="Tasks"
          value={stats.total_tasks}
          icon={CheckCircle2}
          color="bg-blue-500"
          subtext={`${stats.completed_tasks} done · ${stats.completion_rate}%`}
          to="/tasks"
        />
        <StatCard
          title="Active Sessions"
          value={stats.active_runs}
          icon={Zap}
          color="bg-orange-500"
          subtext={`${stats.available_slots} slots free`}
          to="/sessions"
          pulse={stats.active_runs > 0}
        />
        <StatCard
          title="Agents"
          value={agents.length}
          icon={Bot}
          color="bg-indigo-500"
          subtext={`${agents.filter(a => a.type === 'http').length} remote`}
          to="/agents"
        />
        <StatCard
          title="Memory Pools"
          value={memories.length}
          icon={Brain}
          color="bg-purple-500"
          subtext={`${ragIndexedFiles} RAG-indexed files`}
          to="/memory"
        />
        <StatCard
          title="Flows"
          value={flows.length}
          icon={GitBranch}
          color="bg-rose-500"
          subtext="custom pipelines"
          to="/factory"
        />
        <StatCard
          title="Nodes"
          value={`${activeNodes.length}/${nodes.length}`}
          icon={Server}
          color="bg-gray-500"
          subtext={activeNodes.length > 0 ? `${activeNodes.length} running` : 'none running'}
          to="/nodes"
          pulse={activeNodes.length > 0}
        />
      </div>

      {/* Feature Quick Access */}
      <div>
        <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">Features</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-4 gap-3">
          <FeatureCard
            to="/"
            icon={MessageSquare}
            iconColor="text-blue-600"
            bgColor="bg-blue-50"
            title="Chat"
            description="Converse with agents in real time"
          />
          <FeatureCard
            to="/orchestrator"
            icon={Network}
            iconColor="text-indigo-600"
            bgColor="bg-indigo-50"
            title="Orchestrator"
            description="Auto-route tasks to specialized agents"
          />
          <FeatureCard
            to="/factory"
            icon={Layers}
            iconColor="text-rose-600"
            bgColor="bg-rose-50"
            title="Agent Factory"
            description={`${flows.length} visual workflows`}
          />
          <FeatureCard
            to="/tools"
            icon={Wrench}
            iconColor="text-amber-600"
            bgColor="bg-amber-50"
            title="Tools Explorer"
            description="Browse agent capabilities"
          />
          <FeatureCard
            to="/manifest"
            icon={FileCode2}
            iconColor="text-cyan-600"
            bgColor="bg-cyan-50"
            title="Agent Manifest"
            description="View & apply YAML definitions"
          />
          <FeatureCard
            to="/sessions"
            icon={FlaskConical}
            iconColor="text-green-600"
            bgColor="bg-green-50"
            title="Sessions"
            description={`${sessionSuccessRate}% success rate`}
          />
          <FeatureCard
            to="/nodes"
            icon={Server}
            iconColor="text-gray-600"
            bgColor="bg-gray-100"
            title="Nodes"
            description={`${activeNodes.length} running`}
          />
          <FeatureCard
            to="/settings"
            icon={SettingsIcon}
            iconColor="text-violet-600"
            bgColor="bg-violet-50"
            title="Settings"
            description="Models, RAG & observability"
          />
        </div>
      </div>

      {/* Active Sessions + Memory row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Active Sessions */}
        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
          <div className="flex justify-between items-center mb-5">
            <h3 className="font-bold text-gray-700 flex items-center text-sm">
              <Activity className="w-4 h-4 mr-2 text-orange-500" />
              Active Sessions
              {activePods.length > 0 && (
                <span className="ml-2 px-1.5 py-0.5 text-xs bg-orange-100 text-orange-700 rounded-full font-semibold">
                  {activePods.length}
                </span>
              )}
            </h3>
            <Link to="/sessions" className="text-xs text-indigo-600 hover:underline flex items-center">
              View all <ChevronRight className="w-3 h-3 ml-0.5" />
            </Link>
          </div>
          {activePods.length > 0 ? (
            <div className="space-y-3">
              {activePods.slice(0, 5).map(run => (
                <div key={run.run_id} className="flex items-center justify-between py-2 border-b border-gray-50 last:border-0">
                  <div className="flex items-center space-x-3">
                    <div className="w-2 h-2 rounded-full bg-orange-400 animate-pulse" />
                    <div>
                      <div className="text-sm font-semibold text-gray-700">{run.agent_id}</div>
                      <div className="font-mono text-[10px] text-gray-400">
                        {run.task_id ? (
                          <Link to={`/tasks/${run.task_id}`} className="hover:text-indigo-600">
                            task/{run.task_id.slice(0, 8)}
                          </Link>
                        ) : `run/${run.run_id.slice(0, 8)}`}
                      </div>
                    </div>
                  </div>
                  <div className="text-xs text-gray-400 flex items-center">
                    <Clock className="w-3 h-3 mr-1" />
                    {new Date(run.started_at).toLocaleTimeString()}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState message="No sessions currently running" icon={Play} />
          )}
          <div className="mt-4 pt-4 border-t border-gray-50">
            <div className="flex justify-between text-xs text-gray-500 mb-1">
              <span>Capacity</span>
              <span>{stats.active_runs}/{stats.total_capacity}</span>
            </div>
            <div className="w-full bg-gray-100 rounded-full h-2">
              <div
                className="bg-orange-400 h-2 rounded-full transition-all duration-500"
                style={{ width: `${stats.total_capacity > 0 ? (stats.active_runs / stats.total_capacity) * 100 : 0}%` }}
              />
            </div>
          </div>
        </div>

        {/* Memory Pools */}
        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
          <div className="flex justify-between items-center mb-5">
            <h3 className="font-bold text-gray-700 flex items-center text-sm">
              <Brain className="w-4 h-4 mr-2 text-purple-500" />
              Shared Memory & RAG
            </h3>
            <Link to="/memory" className="text-xs text-indigo-600 hover:underline flex items-center">
              Manage <ChevronRight className="w-3 h-3 ml-0.5" />
            </Link>
          </div>
          <div className="grid grid-cols-3 gap-3 mb-4">
            <MiniStat label="Pools" value={memories.length} color="text-purple-600" />
            <MiniStat label="Files" value={totalMemoryFiles} color="text-blue-600" />
            <MiniStat label="Indexed" value={ragIndexedFiles} color="text-green-600" />
          </div>
          {memories.length > 0 ? (
            <div className="space-y-2">
              {memories.slice(0, 4).map(mem => {
                const fileCount = (mem.files || []).length;
                const indexed = (mem.files || []).filter(f => f.rag_status === 'indexed').length;
                return (
                  <div key={mem.id} className="flex items-center justify-between py-1.5 px-3 rounded-lg bg-gray-50">
                    <div className="flex items-center space-x-2">
                      <Database className="w-3.5 h-3.5 text-purple-400" />
                      <span className="text-sm font-medium text-gray-700">{mem.name}</span>
                    </div>
                    <div className="flex items-center space-x-2 text-xs text-gray-400">
                      <span>{fileCount} files</span>
                      {indexed > 0 && (
                        <span className="bg-green-50 text-green-700 border border-green-100 px-1.5 py-0.5 rounded-full font-medium">
                          {indexed} indexed
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <EmptyState message="No memory pools created yet" />
          )}
        </div>
      </div>

      {/* Recent Run History */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100 flex justify-between items-center">
          <h3 className="font-bold text-gray-700 flex items-center text-sm">
            <TrendingUp className="w-4 h-4 mr-2 text-emerald-500" />
            Recent Run History
          </h3>
          <Link to="/sessions" className="text-xs text-indigo-600 hover:underline flex items-center">
            All sessions <ChevronRight className="w-3 h-3 ml-0.5" />
          </Link>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead className="bg-gray-50 border-b border-gray-100">
              <tr className="text-xs uppercase text-gray-400">
                <th className="px-6 py-3 font-semibold">Agent</th>
                <th className="px-6 py-3 font-semibold">Workspace</th>
                <th className="px-6 py-3 font-semibold">Status</th>
                <th className="px-6 py-3 font-semibold">Duration</th>
                <th className="px-6 py-3 font-semibold">Finished</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {stats.recent_runs.length > 0 ? stats.recent_runs.map(run => {
                const isSuccess = run.status === 'completed';
                const isRunning = run.status === 'running';
                const isFailed = !isSuccess && !isRunning;
                const duration = run.finished_at
                  ? `${Math.round((new Date(run.finished_at) - new Date(run.started_at)) / 1000)}s`
                  : '--';

                return (
                  <tr key={run.run_id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-6 py-3.5">
                      <div className="font-medium text-gray-900 text-sm">{run.agent_id}</div>
                      <div className="text-[10px] font-mono text-gray-400">{run.run_id.slice(0, 12)}…</div>
                    </td>
                    <td className="px-6 py-3.5 text-xs text-gray-500">
                      {run.workspace || <span className="italic text-gray-300">—</span>}
                    </td>
                    <td className="px-6 py-3.5">
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${
                        isRunning ? 'bg-orange-50 text-orange-700 border-orange-100' :
                        isSuccess ? 'bg-green-50 text-green-700 border-green-100' :
                        'bg-red-50 text-red-700 border-red-100'
                      }`}>
                        {isRunning && <span className="w-1.5 h-1.5 rounded-full bg-orange-400 animate-pulse" />}
                        {isSuccess && <CheckCircle2 className="w-3 h-3" />}
                        {isFailed && <AlertCircle className="w-3 h-3" />}
                        {run.status}
                      </span>
                    </td>
                    <td className="px-6 py-3.5 text-xs text-gray-500 font-mono">{duration}</td>
                    <td className="px-6 py-3.5 text-xs text-gray-400">
                      {run.finished_at ? new Date(run.finished_at).toLocaleString() : '—'}
                    </td>
                  </tr>
                );
              }) : (
                <tr>
                  <td colSpan="5" className="px-6 py-10 text-center text-gray-400 italic text-sm">
                    No runs recorded yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

/* ── Sub-components ─────────────────────────────────────────── */

const StatCard = ({ title, value, icon: Icon, color, subtext, to, pulse }) => (
  <Link to={to} className="block bg-white p-5 rounded-xl shadow-sm border border-gray-100 hover:shadow-md hover:border-gray-200 transition-all group">
    <div className="flex justify-between items-start mb-3">
      <div className={`p-2 rounded-lg ${color} text-white`}>
        <Icon className="w-5 h-5" />
      </div>
      {pulse && <span className="w-2 h-2 rounded-full bg-orange-400 animate-pulse mt-1" />}
    </div>
    <div>
      <div className="text-2xl font-bold text-gray-900 group-hover:text-indigo-600 transition-colors">{value}</div>
      <div className="text-xs font-medium text-gray-500 uppercase tracking-wide mt-0.5">{title}</div>
      <div className="text-xs text-gray-400 mt-1">{subtext}</div>
    </div>
  </Link>
);

const FeatureCard = ({ to, icon: Icon, iconColor, bgColor, title, description }) => (
  <Link
    to={to}
    className="flex items-center space-x-3 p-4 bg-white rounded-xl border border-gray-100 shadow-sm hover:shadow-md hover:border-gray-200 transition-all group"
  >
    <div className={`p-2.5 rounded-lg ${bgColor} flex-shrink-0`}>
      <Icon className={`w-4 h-4 ${iconColor}`} />
    </div>
    <div className="min-w-0">
      <div className="text-sm font-semibold text-gray-800 group-hover:text-indigo-600 transition-colors">{title}</div>
      <div className="text-xs text-gray-400 truncate">{description}</div>
    </div>
    <ChevronRight className="w-3.5 h-3.5 text-gray-300 group-hover:text-indigo-400 flex-shrink-0 ml-auto transition-colors" />
  </Link>
);

const MiniStat = ({ label, value, color }) => (
  <div className="text-center py-2 px-3 bg-gray-50 rounded-lg">
    <div className={`text-xl font-bold ${color}`}>{value}</div>
    <div className="text-xs text-gray-400 mt-0.5">{label}</div>
  </div>
);

const EmptyState = ({ message, icon: Icon = Database }) => (
  <div className="flex flex-col items-center justify-center py-6 text-gray-300">
    <Icon className="w-8 h-8 mb-2" />
    <p className="text-sm italic">{message}</p>
  </div>
);

export default Dashboard;
