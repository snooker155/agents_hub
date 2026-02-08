import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  BarChart3,
  Activity,
  Users,
  CheckCircle2,
  Clock,
  Cpu,
  Database,
  ExternalLink,
  Zap
} from 'lucide-react';
import { getStats, getAgents, getRuns } from '../api';
import { useWorkspace } from '../components/WorkspaceContext';

const Dashboard = () => {
  const { selectedWorkspace } = useWorkspace();
  const [stats, setStats] = useState(null);
  const [agents, setAgents] = useState([]);
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    try {
      const [statsResp, agentsResp, runsResp] = await Promise.all([
        getStats(selectedWorkspace),
        getAgents(),
        getRuns()
      ]);
      setStats(statsResp.data);
      setAgents(agentsResp.data);
      setRuns(runsResp.data);
      setLoading(false);
    } catch (error) {
      console.error('Error fetching dashboard stats:', error);
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [selectedWorkspace]);

  if (loading || !stats) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600"></div>
      </div>
    );
  }

  const activePods = runs.filter(r => r.status === 'running');

  return (
    <div className="space-y-8">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold text-gray-800">Cluster Overview</h2>
        <div className="flex space-x-2">
          <span className="flex items-center text-xs bg-green-100 text-green-800 px-2 py-1 rounded-full border border-green-200">
            <Activity className="w-3 h-3 mr-1" />
            Cluster Healthy
          </span>
        </div>
      </div>

      {/* Top Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard
          title="Total Tasks"
          value={stats.total_tasks}
          icon={CheckCircle2}
          color="bg-blue-500"
          subtext={`${stats.completed_tasks} completed (${stats.completion_rate}%)`}
        />
        <StatCard
          title="Active Pods"
          value={stats.active_runs}
          icon={Zap}
          color="bg-orange-500"
          subtext={`of ${stats.total_capacity} total capacity`}
        />
        <StatCard
          title="Total Agents"
          value={stats.total_agents}
          icon={Users}
          color="bg-indigo-500"
          subtext={`${Object.keys(stats.domain_usage).length} domains active`}
        />
        <StatCard
          title="Resource Availability"
          value={`${Math.round((stats.available_slots / stats.total_capacity) * 100)}%`}
          icon={Cpu}
          color="bg-emerald-500"
          subtext={`${stats.available_slots} slots available`}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Domain Distribution Chart */}
        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
          <div className="flex justify-between items-center mb-6">
            <h3 className="font-bold text-gray-700 flex items-center">
              <BarChart3 className="w-5 h-5 mr-2 text-indigo-500" />
              Domain Distribution
            </h3>
          </div>
          <div className="space-y-4">
            {Object.entries(stats.domain_usage).map(([domain, count]) => {
              const percentage = (count / (runs.length || 1)) * 100;
              return (
                <div key={domain}>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="capitalize text-gray-600 font-medium">{domain}</span>
                    <span className="text-gray-400">{count} runs</span>
                  </div>
                  <div className="w-full bg-gray-100 rounded-full h-2">
                    <div
                      className="bg-indigo-600 h-2 rounded-full transition-all duration-500"
                      style={{ width: `${percentage}%` }}
                    ></div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Active Pods (Runs) */}
        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
          <div className="flex justify-between items-center mb-6">
            <h3 className="font-bold text-gray-700 flex items-center">
              <Activity className="w-5 h-5 mr-2 text-orange-500" />
              Running Pods
            </h3>
            <Link to="/agents" className="text-xs text-indigo-600 hover:underline">View All Agents</Link>
          </div>
          <div className="overflow-hidden">
            <table className="w-full text-left">
              <thead>
                <tr className="text-xs uppercase text-gray-400 border-b border-gray-50 pb-2">
                  <th className="font-semibold pb-2">Run ID / Agent</th>
                  <th className="font-semibold pb-2">Task</th>
                  <th className="font-semibold pb-2">Age</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {activePods.length > 0 ? activePods.map(run => (
                  <tr key={run.run_id} className="text-sm">
                    <td className="py-3">
                      <div className="font-mono text-[10px] text-gray-400">pod/{run.run_id.slice(0, 8)}</div>
                      <div className="font-semibold text-gray-700">{run.agent_id}</div>
                    </td>
                    <td className="py-3 truncate max-w-[150px]">
                      <Link to={`/tasks/${run.task_id}`} className="hover:text-indigo-600 transition-colors">
                        task/{run.task_id.slice(0, 8)}
                      </Link>
                    </td>
                    <td className="py-3 text-xs text-gray-500 flex items-center">
                      <Clock className="w-3 h-3 mr-1" />
                      {new Date(run.started_at).toLocaleTimeString()}
                    </td>
                  </tr>
                )) : (
                  <tr>
                    <td colSpan="3" className="py-8 text-center text-gray-400 italic">No pods currently running</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Recent History */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-50 flex justify-between items-center bg-gray-50/50">
          <h3 className="font-bold text-gray-700 flex items-center">
            <Database className="w-5 h-5 mr-2 text-emerald-500" />
            Recent Run History
          </h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead className="bg-gray-50">
              <tr className="text-xs uppercase text-gray-400">
                <th className="px-6 py-3 font-semibold">Agent</th>
                <th className="px-6 py-3 font-semibold">Status</th>
                <th className="px-6 py-3 font-semibold">Exit Code</th>
                <th className="px-6 py-3 font-semibold">Duration</th>
                <th className="px-6 py-3 font-semibold">Finished At</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {stats.recent_runs.map(run => {
                const isSuccess = run.status === 'completed';
                const isRunning = run.status === 'running';

                return (
                  <tr key={run.run_id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-6 py-4">
                      <div className="font-medium text-gray-900">{run.agent_id}</div>
                      <div className="text-[10px] font-mono text-gray-400">{run.run_id}</div>
                    </td>
                    <td className="px-6 py-4">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${
                        isRunning ? 'bg-orange-50 text-orange-700 border-orange-100' :
                        isSuccess ? 'bg-green-50 text-green-700 border-green-100' :
                        'bg-red-50 text-red-700 border-red-100'
                      }`}>
                        {run.status}
                      </span>
                    </td>
                    <td className="px-6 py-4 font-mono text-xs text-gray-500">
                      {run.exit_code !== null ? run.exit_code : '--'}
                    </td>
                    <td className="px-6 py-4 text-xs text-gray-500">
                        {run.finished_at ? (
                            `${Math.round((new Date(run.finished_at) - new Date(run.started_at)) / 1000)}s`
                        ) : '--'}
                    </td>
                    <td className="px-6 py-4 text-xs text-gray-500">
                      {run.finished_at ? new Date(run.finished_at).toLocaleString() : '--'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

const StatCard = ({ title, value, icon: Icon, color, subtext }) => (
  <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
    <div className="flex justify-between items-start mb-4">
      <div className={`p-2 rounded-lg ${color} text-white`}>
        <Icon className="w-6 h-6" />
      </div>
    </div>
    <div className="space-y-1">
      <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">{title}</h3>
      <div className="text-3xl font-bold text-gray-900">{value}</div>
      <p className="text-xs text-gray-400 mt-1">{subtext}</p>
    </div>
  </div>
);

export default Dashboard;
