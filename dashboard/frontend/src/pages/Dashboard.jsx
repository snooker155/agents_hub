import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useLiveRefetch } from '../components/stream';
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
  Settings as SettingsIcon,
  Network,
  TrendingUp,
  AlertCircle,
  Play,
  ChevronRight,
  Layers,
  Bot,
  HardDrive,
  Gauge,
  LayoutDashboard,
} from 'lucide-react';
import {
  getStats,
  getAgents,
  getRuns,
  getSharedMemories,
  getNodes,
  listFlows,
  getSystemHealth,
} from '../api';
import { useWorkspace } from '../components/workspace';
import { useI18n } from '../i18n';

import { PageContainer, PageHeader } from '../components/PageLayout';
const Dashboard = () => {
  const { selectedWorkspace, workspaceFilter, liveUpdates } = useWorkspace();
  const { t } = useI18n();
  const [stats, setStats] = useState(null);
  const [agents, setAgents] = useState([]);
  const [runs, setRuns] = useState([]);
  const [memories, setMemories] = useState([]);
  const [nodes, setNodes] = useState([]);
  const [flows, setFlows] = useState([]);
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const fetchData = useCallback(() => (
    Promise.allSettled([
      getStats(workspaceFilter),
      getAgents(workspaceFilter),
      getRuns(workspaceFilter),
      getSharedMemories(workspaceFilter),
      getNodes(workspaceFilter),
      listFlows(workspaceFilter),
      getSystemHealth(),
    ])
      .then(([statsResp, agentsResp, runsResp, memResp, nodesResp, flowsResp, healthResp]) => {
        // Each panel stands on its own: one failed endpoint must not blank the page.
        if (statsResp.status === 'fulfilled') setStats(statsResp.value.data);
        if (agentsResp.status === 'fulfilled') setAgents(agentsResp.value.data);
        if (runsResp.status === 'fulfilled') setRuns(runsResp.value.data);
        if (memResp.status === 'fulfilled') setMemories(memResp.value.data);
        if (nodesResp.status === 'fulfilled') setNodes(nodesResp.value.data);
        if (flowsResp.status === 'fulfilled') setFlows(flowsResp.value.data);
        if (healthResp.status === 'fulfilled') setHealth(healthResp.value.data);
      })
      .catch((error) => console.error('Error fetching dashboard data:', error))
      .finally(() => setLoading(false))
  ), [workspaceFilter]);

  useEffect(() => {
    fetchData();
  }, [selectedWorkspace, liveUpdates, fetchData]);
  // Aggregate page: refetch (debounced) on any app-wide change.
  useLiveRefetch(fetchData, { enabled: liveUpdates });

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

  return (
    <PageContainer className="space-y-6">
      {/* The selected workspace is already shown in the top bar — only label the
          scope on the default (all-workspaces) view. */}
      <PageHeader
        icon={LayoutDashboard}
        title={t('dashboard.title')}
        description={(!selectedWorkspace || selectedWorkspace === 'default') ? t('dashboard.allWorkspaces') : undefined}
        actions={(() => {
          const degraded = health && health.status !== 'ok';
          return (
            <span className={`flex items-center text-xs px-2.5 py-1 rounded-full border font-medium ${
              degraded
                ? 'bg-red-50 text-red-700 border-red-100'
                : 'bg-blue-50 text-blue-700 border-blue-100'
            }`}>
              {degraded ? <AlertCircle className="w-3 h-3 mr-1" /> : <Activity className="w-3 h-3 mr-1" />}
              {degraded ? t('dashboard.clusterDegraded') : t('dashboard.clusterHealthy')}
            </span>
          );
        })()}
      />

      {/* Top Stats — 6 cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
        <StatCard
          title={t('dashboard.stats.tasks')}
          value={stats.total_tasks}
          icon={CheckCircle2}
          color="bg-blue-500"
          subtext={t('dashboard.stats.tasksSub', { done: stats.completed_tasks, rate: stats.completion_rate })}
          to="/tasks"
        />
        <StatCard
          title={t('dashboard.stats.activeSessions')}
          value={stats.active_runs}
          icon={Zap}
          color="bg-orange-500"
          subtext={t('dashboard.stats.slotsFree', { count: stats.available_slots })}
          to="/sessions"
          pulse={stats.active_runs > 0}
        />
        <StatCard
          title={t('dashboard.stats.agents')}
          value={agents.length}
          icon={Bot}
          color="bg-indigo-500"
          subtext={t('dashboard.stats.agentsRemote', { count: agents.filter(a => a.type === 'http').length })}
          to="/agents"
        />
        <StatCard
          title={t('dashboard.stats.memoryPools')}
          value={memories.length}
          icon={Brain}
          color="bg-purple-500"
          subtext={t('dashboard.stats.ragIndexedFiles', { count: ragIndexedFiles })}
          to="/memory"
        />
        <StatCard
          title={t('dashboard.stats.flows')}
          value={flows.length}
          icon={GitBranch}
          color="bg-rose-500"
          subtext={t('dashboard.stats.customPipelines')}
          to="/flows"
        />
        <StatCard
          title={t('dashboard.stats.nodes')}
          value={`${activeNodes.length}/${nodes.length}`}
          icon={Server}
          color="bg-gray-500"
          subtext={activeNodes.length > 0 ? t('dashboard.stats.nodesRunning', { count: activeNodes.length }) : t('dashboard.stats.noneRunning')}
          to="/nodes"
          pulse={activeNodes.length > 0}
        />
      </div>

      {/* Feature Quick Access */}
      <div>
        <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">{t('dashboard.features.heading')}</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-4 gap-3">
          <FeatureCard
            to="/chat"
            icon={MessageSquare}
            iconColor="text-blue-600"
            bgColor="bg-blue-50"
            title={t('dashboard.features.chat')}
            description={t('dashboard.features.chatDesc')}
          />
          <FeatureCard
            to="/orchestrator"
            icon={Network}
            iconColor="text-indigo-600"
            bgColor="bg-indigo-50"
            title={t('dashboard.features.orchestrator')}
            description={t('dashboard.features.orchestratorDesc')}
          />
          <FeatureCard
            to="/projects"
            icon={GitBranch}
            iconColor="text-emerald-600"
            bgColor="bg-emerald-50"
            title={t('dashboard.features.projects')}
            description={t('dashboard.features.projectsDesc')}
          />
          <FeatureCard
            to="/flows"
            icon={Layers}
            iconColor="text-rose-600"
            bgColor="bg-rose-50"
            title={t('dashboard.features.flows')}
            description={t('dashboard.features.flowsDesc', { count: flows.length })}
          />
          <FeatureCard
            to="/models"
            icon={Brain}
            iconColor="text-violet-600"
            bgColor="bg-violet-50"
            title={t('dashboard.features.models')}
            description={t('dashboard.features.modelsDesc')}
          />
          <FeatureCard
            to="/marketplace"
            icon={Bot}
            iconColor="text-cyan-600"
            bgColor="bg-cyan-50"
            title={t('dashboard.features.marketplace')}
            description={t('dashboard.features.marketplaceDesc')}
          />
          <FeatureCard
            to="/memory"
            icon={Database}
            iconColor="text-amber-600"
            bgColor="bg-amber-50"
            title={t('dashboard.features.memory')}
            description={t('dashboard.features.memoryDesc')}
          />
          <FeatureCard
            to="/settings"
            icon={SettingsIcon}
            iconColor="text-gray-600"
            bgColor="bg-gray-100"
            title={t('dashboard.features.settings')}
            description={t('dashboard.features.settingsDesc')}
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
              {t('dashboard.sessions.heading')}
              {activePods.length > 0 && (
                <span className="ml-2 px-1.5 py-0.5 text-xs bg-orange-100 text-orange-700 rounded-full font-semibold">
                  {activePods.length}
                </span>
              )}
            </h3>
            <Link to="/sessions" className="text-xs text-indigo-600 hover:underline flex items-center">
              {t('dashboard.sessions.viewAll')} <ChevronRight className="w-3 h-3 ml-0.5" />
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
                      <div className=" text-[10px] text-gray-400">
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
            <EmptyState message={t('dashboard.sessions.empty')} icon={Play} />
          )}
          <div className="mt-4 pt-4 border-t border-gray-50">
            <div className="flex justify-between text-xs text-gray-500 mb-1">
              <span>{t('dashboard.sessions.capacity')}</span>
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
              {t('dashboard.memory.heading')}
            </h3>
            <Link to="/memory" className="text-xs text-indigo-600 hover:underline flex items-center">
              {t('dashboard.memory.manage')} <ChevronRight className="w-3 h-3 ml-0.5" />
            </Link>
          </div>
          <div className="grid grid-cols-3 gap-3 mb-4">
            <MiniStat label={t('dashboard.memory.pools')} value={memories.length} color="text-purple-600" />
            <MiniStat label={t('dashboard.memory.files')} value={totalMemoryFiles} color="text-blue-600" />
            <MiniStat label={t('dashboard.memory.indexed')} value={ragIndexedFiles} color="text-green-600" />
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
                      <span>{t('dashboard.memory.fileCount', { count: fileCount })}</span>
                      {indexed > 0 && (
                        <span className="bg-green-50 text-green-700 border border-green-100 px-1.5 py-0.5 rounded-full font-medium">
                          {t('dashboard.memory.indexedCount', { count: indexed })}
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <EmptyState message={t('dashboard.memory.empty')} />
          )}
        </div>
      </div>

      {/* System Health */}
      {health && <SystemHealth health={health} />}

      {/* Recent Run History */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100 flex justify-between items-center">
          <h3 className="font-bold text-gray-700 flex items-center text-sm">
            <TrendingUp className="w-4 h-4 mr-2 text-emerald-500" />
            {t('dashboard.runs.heading')}
          </h3>
          <Link to="/sessions" className="text-xs text-indigo-600 hover:underline flex items-center">
            {t('dashboard.runs.allSessions')} <ChevronRight className="w-3 h-3 ml-0.5" />
          </Link>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead className="bg-gray-50 border-b border-gray-100">
              <tr className="text-xs uppercase text-gray-400">
                <th className="px-6 py-3 font-semibold">{t('dashboard.runs.agent')}</th>
                <th className="px-6 py-3 font-semibold">{t('dashboard.runs.workspace')}</th>
                <th className="px-6 py-3 font-semibold">{t('dashboard.runs.status')}</th>
                <th className="px-6 py-3 font-semibold">{t('dashboard.runs.duration')}</th>
                <th className="px-6 py-3 font-semibold">{t('dashboard.runs.finished')}</th>
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
                      <div className="text-[10px] text-gray-400">{run.run_id.slice(0, 12)}…</div>
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
                    <td className="px-6 py-3.5 text-xs text-gray-500">{duration}</td>
                    <td className="px-6 py-3.5 text-xs text-gray-400">
                      {run.finished_at ? new Date(run.finished_at).toLocaleString() : '—'}
                    </td>
                  </tr>
                );
              }) : (
                <tr>
                  <td colSpan="5" className="px-6 py-10 text-center text-gray-400 italic text-sm">
                    {t('dashboard.runs.empty')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </PageContainer>
  );
};

/* ── Sub-components ─────────────────────────────────────────── */

const formatBytes = (n) => {
  const b = Number(n || 0);
  if (b < 1024) return `${b} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = b / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
};

const SystemHealth = ({ health }) => {
  const { t } = useI18n();
  const db = health.database || {};
  const counts = db.counts || {};
  const services = health.services || {};
  const storage = health.storage || {};
  const cache = health.agent_cache || {};
  const cacheLookups = (cache.hits || 0) + (cache.misses || 0);
  const cacheHitRate = cacheLookups > 0 ? Math.round((cache.hits / cacheLookups) * 100) : null;

  // Order services so the always-on ones read first; label each for humans.
  const serviceLabels = {
    plan_scheduler: t('dashboard.health.serviceScheduler'),
    run_watchdog: t('dashboard.health.serviceWatchdog'),
    external_publisher: t('dashboard.health.serviceLiveUpdates'),
    telegram_poller: t('dashboard.health.serviceTelegram'),
  };

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
      <div className="px-6 py-4 border-b border-gray-100 flex justify-between items-center">
        <h3 className="font-bold text-gray-700 flex items-center text-sm">
          <Activity className="w-4 h-4 mr-2 text-emerald-500" />
          {t('dashboard.health.heading')}
        </h3>
        <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${
          health.status === 'ok'
            ? 'bg-green-50 text-green-700 border-green-100'
            : 'bg-red-50 text-red-700 border-red-100'
        }`}>
          {health.status === 'ok' ? t('dashboard.health.operational') : t('dashboard.health.degraded')}
        </span>
      </div>

      <div className="p-6 grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Database */}
        <div>
          <div className="flex items-center text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
            <Database className="w-3.5 h-3.5 mr-1.5 text-indigo-500" />
            {t('dashboard.health.database')}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <MiniStat label={t('dashboard.health.runs')} value={counts.runs ?? '—'} color="text-indigo-600" />
            <MiniStat label={t('dashboard.health.tasks')} value={counts.tasks ?? '—'} color="text-blue-600" />
            <MiniStat label={t('dashboard.health.sessions')} value={counts.sessions ?? '—'} color="text-purple-600" />
            <MiniStat label={t('dashboard.health.running')} value={db.running_runs ?? '—'} color="text-orange-600" />
          </div>
        </div>

        {/* Background services */}
        <div>
          <div className="flex items-center text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
            <Server className="w-3.5 h-3.5 mr-1.5 text-gray-500" />
            {t('dashboard.health.services')}
          </div>
          <div className="space-y-1.5">
            {Object.entries(serviceLabels).map(([key, label]) => (
              <ServiceRow key={key} label={label} state={services[key]} />
            ))}
          </div>
        </div>

        {/* Agent build cache */}
        <div>
          <div className="flex items-center text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
            <Gauge className="w-3.5 h-3.5 mr-1.5 text-emerald-500" />
            {t('dashboard.health.agentCache')}
          </div>
          {cache.enabled === false ? (
            <p className="text-sm text-gray-400 italic">{t('dashboard.health.cacheDisabled')}</p>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              <MiniStat label={t('dashboard.health.hitRate')} value={cacheHitRate === null ? '—' : `${cacheHitRate}%`} color="text-emerald-600" />
              <MiniStat label={t('dashboard.health.cached')} value={cache.entries ?? '—'} color="text-gray-600" />
              <MiniStat label={t('dashboard.health.hits')} value={cache.hits ?? '—'} color="text-green-600" />
              <MiniStat label={t('dashboard.health.misses')} value={cache.misses ?? '—'} color="text-gray-500" />
            </div>
          )}
        </div>

        {/* Storage */}
        <div>
          <div className="flex items-center text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
            <HardDrive className="w-3.5 h-3.5 mr-1.5 text-rose-500" />
            {t('dashboard.health.storage')}
          </div>
          <div className="space-y-1.5 text-sm">
            <StorageRow label={t('dashboard.health.storageDatabase')} value={formatBytes(storage.db_bytes)} />
            <StorageRow label={t('dashboard.health.storageRunLogs')} value={t('dashboard.health.logFiles', { size: formatBytes(storage.run_logs_bytes), count: storage.run_logs_files ?? 0 })} />
            <StorageRow label={t('dashboard.health.storageTotalState')} value={formatBytes(storage.agents_hub_bytes)} />
          </div>
        </div>
      </div>
    </div>
  );
};

const ServiceRow = ({ label, state }) => {
  const { t } = useI18n();
  // true = running (green), false = stopped (gray), null/undefined = unknown (amber).
  const color = state === true ? 'bg-green-400' : state === false ? 'bg-gray-300' : 'bg-amber-400';
  const text = state === true ? t('dashboard.health.stateRunning') : state === false ? t('dashboard.health.stateStopped') : t('dashboard.health.stateUnknown');
  return (
    <div className="flex items-center justify-between py-1 px-2.5 rounded-lg bg-gray-50">
      <span className="text-sm text-gray-700">{label}</span>
      <span className="flex items-center gap-1.5 text-xs text-gray-400">
        <span className={`w-2 h-2 rounded-full ${color} ${state === true ? 'animate-pulse' : ''}`} />
        {text}
      </span>
    </div>
  );
};

const StorageRow = ({ label, value }) => (
  <div className="flex items-center justify-between py-1 px-2.5 rounded-lg bg-gray-50">
    <span className="text-gray-500 text-xs">{label}</span>
    <span className="text-gray-700 text-xs font-medium">{value}</span>
  </div>
);

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
