import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useLiveRefetch } from '../components/stream';
import {
  Activity,
  Users,
  CheckCircle2,
  Database,
  Zap,
  Brain,
  GitBranch,
  Server,
  Radio,
  MessageSquare,
  Settings as SettingsIcon,
  Network,
  AlertCircle,
  ChevronRight,
  Layers,
  Bot,
  HardDrive,
  Gauge,
  LayoutDashboard,
  Share2,
  KeyRound,
} from 'lucide-react';
import {
  getStats,
  getStatsOverview,
  getAgents,
  getSharedMemories,
  getInstancesSummary,
  listFlows,
  getSystemHealth,
  listConnections,
} from '../api';
import { getProactiveSummary } from '../api/proactive';
import { useWorkspace } from '../components/workspace';
import { useI18n } from '../i18n';
import { poolName } from '../components/memoryManager/helpers';

import { PageContainer, PageHeader } from '../components/PageLayout';
import {
  Panel, LiveWidget, RunsWidget, CostsWidget, LocalModelsWidget, EndpointWidget, ServicesWidget,
} from '../components/dashboard/OverviewWidgets';
import { fmtBytes as formatBytes, fmtInt } from '../components/dashboard/format';
import BalancedColumns from '../components/dashboard/BalancedColumns';
import PageLoader from '../components/PageLoader';
const Dashboard = () => {
  const { selectedWorkspace, workspaceFilter, liveUpdates } = useWorkspace();
  const { t } = useI18n();
  const [stats, setStats] = useState(null);
  const [agents, setAgents] = useState([]);
  const [memories, setMemories] = useState([]);
  const [instanceCounts, setInstanceCounts] = useState({ live: 0, total: 0 });
  const [flows, setFlows] = useState([]);
  const [health, setHealth] = useState(null);
  const [connections, setConnections] = useState([]);
  const [pulse, setPulse] = useState(null);
  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(true);
  const fetchData = useCallback(() => (
    Promise.allSettled([
      getStats(workspaceFilter),
      getAgents(workspaceFilter),
      getSharedMemories(workspaceFilter),
      getInstancesSummary({ workspace: workspaceFilter }),
      listFlows(workspaceFilter),
      getSystemHealth(),
      listConnections(workspaceFilter),
      getProactiveSummary(workspaceFilter),
      getStatsOverview(workspaceFilter),
    ])
      .then(([statsResp, agentsResp, memResp, instSummaryResp, flowsResp, healthResp,
              connectionsResp, pulseResp, overviewResp]) => {
        // Each panel stands on its own: one failed endpoint must not blank the page.
        if (statsResp.status === 'fulfilled') setStats(statsResp.value.data);
        if (agentsResp.status === 'fulfilled') setAgents(agentsResp.value.data);
        if (memResp.status === 'fulfilled') setMemories(memResp.value.data);
        if (instSummaryResp.status === 'fulfilled') {
          setInstanceCounts(instSummaryResp.value.data?.counts || { live: 0, total: 0 });
        }
        if (flowsResp.status === 'fulfilled') setFlows(flowsResp.value.data);
        if (healthResp.status === 'fulfilled') setHealth(healthResp.value.data);
        if (connectionsResp.status === 'fulfilled') {
          setConnections(connectionsResp.value.data.connections || []);
        }
        if (pulseResp.status === 'fulfilled') setPulse(pulseResp.value.data || null);
        if (overviewResp.status === 'fulfilled') setOverview(overviewResp.value.data || null);
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
    return <PageLoader size="lg" />;
  }

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
        {/* Only when something is attached. An install used purely to watch
            external agents would otherwise have a front page about parts of the
            product it does not use; one that has none should not be told about
            a feature it has not asked for. */}
        {connections.length > 0 && (
          <StatCard
            title={t('dashboard.stats.connections')}
            value={connections.length}
            icon={Share2}
            color="bg-teal-500"
            subtext={t('dashboard.stats.connectionsSub', {
              runs: connections.reduce((acc, c) => acc + ((c.stats || {}).runs || 0), 0),
            })}
            to="/connections"
            pulse={connections.some((c) => (c.stats || {}).running > 0)}
          />
        )}
        {/* Proactive agents (docs/proactive.md): only once a pulse is on, for
            the same reason as the connections card above. */}
        {pulse?.totals?.agents > 0 && (
          <StatCard
            title={t('dashboard.stats.pulse')}
            value={pulse.totals.agents}
            icon={Activity}
            color="bg-rose-500"
            subtext={t('dashboard.stats.pulseSub', {
              acted: pulse.totals.acted, quiet: pulse.totals.quiet, skipped: pulse.totals.skipped,
            })}
            to="/agents"
            pulse={pulse.totals.running > 0}
          />
        )}
        <StatCard
          title={t('dashboard.stats.instances')}
          value={`${instanceCounts.live || 0}/${instanceCounts.total || 0}`}
          icon={Radio}
          color="bg-gray-500"
          subtext={instanceCounts.live > 0 ? t('dashboard.stats.instancesLive', { count: instanceCounts.live }) : t('dashboard.stats.noneRunning')}
          to="/instances"
          pulse={instanceCounts.live > 0}
        />
      </div>

      {/* System Health */}
      {health && <SystemHealth health={health} />}

      {/* Feature Quick Access */}
      <div>
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

      {/* What runs now, load and spend: runs, costs, local models, the /v1
          endpoint and the agent services (GET /api/stats/overview), and the
          memory pools, in two columns balanced by content. */}
      {overview && (
        <BalancedColumns
          items={[
            { key: 'live', node: <LiveWidget live={overview.live} generatedAt={overview.generated_at} /> },
            { key: 'runs', node: <RunsWidget runs={overview.runs} /> },
            { key: 'costs', node: <CostsWidget costs={overview.costs} /> },
            { key: 'local', node: <LocalModelsWidget local={overview.local_models} /> },
            { key: 'endpoint', node: <EndpointWidget endpoint={overview.endpoint} days={overview.days} /> },
            { key: 'services', node: <ServicesWidget services={overview.services} /> },
            {
              key: 'memory',
              node: (
                <MemoryPanel
                  memories={memories} totalFiles={totalMemoryFiles} indexed={ragIndexedFiles}
                  showWorkspace={!workspaceFilter}
                />
              ),
            },
          ]}
        />
      )}


    </PageContainer>
  );
};

/* ── Sub-components ─────────────────────────────────────────── */

// Background services and the singleton role each one runs under: a role's
// lease held by any process (common/leases.py) means the service runs
// somewhere, which this process's own flag cannot tell on a split deployment.
const BACKGROUND = [
  { key: 'plan_scheduler', role: 'scheduler', label: 'serviceScheduler' },
  { key: 'run_watchdog', role: 'watchdog', label: 'serviceWatchdog' },
  { key: 'watchers', role: 'watchers', label: 'serviceWatchers' },
  { role: 'services', label: 'serviceSupervisor' },
  { role: 'outbox', label: 'serviceOutbox' },
  { key: 'external_publisher', label: 'serviceLiveUpdates' },
  { key: 'telegram_poller', role: 'telegram', label: 'serviceTelegram', optional: true },
];

const SystemHealth = ({ health }) => {
  const { t } = useI18n();
  const db = health.database || {};
  const counts = db.counts || {};
  const services = health.services || {};
  const storage = health.storage || {};
  const cache = health.agent_cache || {};
  const cluster = health.cluster || {};
  const queue = cluster.queue || {};
  const outbox = cluster.outbox || {};
  const providers = health.providers || {};
  const activeKinds = Object.entries((health.entity_runs || {}).active_by_kind || {}).filter(([, n]) => n > 0);
  const cacheLookups = (cache.hits || 0) + (cache.misses || 0);
  const cacheHitRate = cacheLookups > 0 ? Math.round((cache.hits / cacheLookups) * 100) : null;
  const held = new Set((cluster.leases || []).filter((l) => !l.expired).map((l) => l.role));

  const background = BACKGROUND.map((b) => {
    const own = b.key ? services[b.key] : undefined;
    const state = own === true || (b.role && held.has(b.role)) ? true : (own ?? (b.role ? false : null));
    return { ...b, state };
  }).filter((b) => !b.optional || b.state === true);

  const keys = [
    ['openai_key_set', 'OpenAI'],
    ['anthropic_key_set', 'Anthropic'],
    ['google_key_set', 'Google'],
  ];

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden" data-testid="dash-system-health">
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

      <div className="p-6 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
        {/* Database */}
        <div>
          <HealthHeading icon={Database} color="text-indigo-500" label={t('dashboard.health.database')} />
          <div className="grid grid-cols-2 gap-2">
            <MiniStat label={t('dashboard.health.runs')} value={counts.runs ?? '—'} color="text-indigo-600" />
            <MiniStat label={t('dashboard.health.tasks')} value={counts.tasks ?? '—'} color="text-blue-600" />
            <MiniStat label={t('dashboard.health.sessions')} value={counts.sessions ?? '—'} color="text-purple-600" />
            <MiniStat label={t('dashboard.health.running')} value={db.running_runs ?? '—'} color="text-orange-600" />
          </div>
        </div>

        {/* Background services */}
        <div>
          <HealthHeading icon={Server} color="text-gray-500" label={t('dashboard.health.services')} />
          <div className="space-y-1.5">
            {background.map((b) => (
              <ServiceRow key={b.label} label={t(`dashboard.health.${b.label}`)} state={b.state} />
            ))}
          </div>
        </div>

        {/* Launch queue and delivery */}
        <div>
          <HealthHeading icon={Layers} color="text-sky-500" label={t('dashboard.health.queue')} />
          <div className="space-y-1.5">
            <StorageRow label={t('dashboard.health.queueWaiting')} value={fmtInt(queue.queued)} warn={queue.queued > 0 && queue.oldest_queued_seconds > 60} />
            <StorageRow label={t('dashboard.health.queueRunning')} value={fmtInt((queue.running || 0) + (queue.leased || 0))} />
            <StorageRow
              label={t('dashboard.health.queueOldest')}
              value={queue.queued > 0 ? t('dashboard.health.seconds', { count: Math.round(queue.oldest_queued_seconds || 0) }) : '—'}
            />
            <StorageRow label={t('dashboard.health.outboxPending')} value={fmtInt(outbox.pending)} />
            <StorageRow label={t('dashboard.health.outboxDead')} value={fmtInt(outbox.dead)} warn={outbox.dead > 0} />
            {activeKinds.length > 0 && (
              <StorageRow
                label={t('dashboard.health.activeEntityRuns')}
                value={activeKinds.map(([k, n]) => `${t(`dashboard.health.kinds.${k}`, { defaultValue: k })}: ${n}`).join(' · ')}
              />
            )}
          </div>
        </div>

        {/* Providers and access */}
        <div>
          <HealthHeading icon={KeyRound} color="text-amber-500" label={t('dashboard.health.providers')} />
          <div className="space-y-1.5">
            <StorageRow label={t('dashboard.health.defaultProvider')} value={providers.default_provider || '—'} />
            <div className="flex flex-wrap gap-1.5 py-1 px-2.5 rounded-lg bg-gray-50">
              {keys.map(([k, name]) => (
                <span
                  key={k}
                  className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border ${
                    providers[k] ? 'bg-green-50 text-green-700 border-green-100' : 'bg-white text-gray-400 border-gray-200'
                  }`}
                  title={providers[k] ? t('dashboard.health.keySet') : t('dashboard.health.keyMissing')}
                >
                  {providers[k] ? <CheckCircle2 className="w-3 h-3" /> : <span className="w-1.5 h-1.5 rounded-full bg-gray-300" />}
                  {name}
                </span>
              ))}
            </div>
            <StorageRow label={t('dashboard.health.authMode')} value={t(`dashboard.health.authModes.${providers.auth_mode}`, { defaultValue: providers.auth_mode || '—' })} />
            <StorageRow label={t('dashboard.health.role')} value={cluster.role || '—'} />
          </div>
        </div>

        {/* Agent builds (agents/agent_cache.py): runs that found their agent
            already built, over this process and every live replica. Not the
            providers' prompt cache, hence the name. */}
        <div>
          <HealthHeading icon={Gauge} color="text-emerald-500" label={t('dashboard.health.agentBuilds')} />
          {cache.enabled === false ? (
            <p className="text-sm text-gray-400 italic">{t('dashboard.health.buildsOff')}</p>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-2">
                <MiniStat label={t('dashboard.health.buildsReuseRate')} value={cacheHitRate === null ? '—' : `${cacheHitRate}%`} color="text-emerald-600" />
                <MiniStat label={t('dashboard.health.buildsHeld')} value={cache.entries ?? '—'} color="text-gray-600" />
                <MiniStat label={t('dashboard.health.buildsReused')} value={cache.hits ?? '—'} color="text-green-600" />
                <MiniStat label={t('dashboard.health.buildsBuilt')} value={cache.misses ?? '—'} color="text-gray-500" />
              </div>
              <p className="text-[11px] text-gray-400 mt-2" title={t('dashboard.health.buildsHint')}>
                {t('dashboard.health.buildsReplicas', { count: cache.replicas?.replicas ?? 0 })}
              </p>
            </>
          )}
        </div>

        {/* Storage */}
        <div>
          <HealthHeading icon={HardDrive} color="text-rose-500" label={t('dashboard.health.storage')} />
          <div className="space-y-1.5 text-sm">
            <StorageRow label={t('dashboard.health.storageDatabase')} value={formatBytes(storage.db_bytes)} />
            {storage.db_wal_bytes > 0 && (
              <StorageRow label={t('dashboard.health.storageWal')} value={formatBytes(storage.db_wal_bytes)} />
            )}
            <StorageRow label={t('dashboard.health.storageRunLogs')} value={t('dashboard.health.logFiles', { size: formatBytes(storage.run_logs_bytes), count: storage.run_logs_files ?? 0 })} />
            <StorageRow label={t('dashboard.health.storageTotalState')} value={formatBytes(storage.agents_hub_bytes)} />
          </div>
        </div>
      </div>
    </div>
  );
};

const HealthHeading = ({ icon: Icon, color, label }) => (
  <div className="flex items-center text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
    <Icon className={`w-3.5 h-3.5 mr-1.5 ${color}`} />
    {label}
  </div>
);

const MemoryPanel = ({ memories, totalFiles, indexed, showWorkspace }) => {
  const { t } = useI18n();
  return (
    <Panel
      icon={Brain} iconColor="text-purple-500" testId="dash-memory"
      title={t('dashboard.memory.heading')}
      to="/memory" linkLabel={t('dashboard.memory.manage')}
    >
      <div className="grid grid-cols-3 gap-3 mb-4">
        <MiniStat label={t('dashboard.memory.pools')} value={memories.length} color="text-purple-600" />
        <MiniStat label={t('dashboard.memory.files')} value={totalFiles} color="text-blue-600" />
        <MiniStat label={t('dashboard.memory.indexed')} value={indexed} color="text-green-600" />
      </div>
      {memories.length > 0 ? (
        <div className="space-y-2">
          {memories.slice(0, 4).map((mem) => {
            const fileCount = (mem.files || []).length;
            const indexedCount = (mem.files || []).filter((f) => f.rag_status === 'indexed').length;
            return (
              <div key={mem.id} className="flex items-center justify-between gap-2 py-1.5 px-3 rounded-lg bg-gray-50">
                <div className="flex items-center space-x-2 min-w-0">
                  <Database className="w-3.5 h-3.5 text-purple-400 shrink-0" />
                  <span className="text-sm font-medium text-gray-700 truncate">{poolName(mem, t, showWorkspace)}</span>
                </div>
                <div className="flex items-center space-x-2 text-xs text-gray-400 shrink-0">
                  <span>{t('dashboard.memory.fileCount', { count: fileCount })}</span>
                  {indexedCount > 0 && (
                    <span className="bg-green-50 text-green-700 border border-green-100 px-1.5 py-0.5 rounded-full font-medium">
                      {t('dashboard.memory.indexedCount', { count: indexedCount })}
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
    </Panel>
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

const StorageRow = ({ label, value, warn }) => (
  <div className="flex items-center justify-between gap-2 py-1 px-2.5 rounded-lg bg-gray-50">
    <span className="text-gray-500 text-xs">{label}</span>
    <span className={`text-xs font-medium tabular-nums text-right ${warn ? 'text-red-600' : 'text-gray-700'}`}>{value}</span>
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
