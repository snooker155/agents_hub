import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { useChannel } from '../components/stream';
import {
  Box, RefreshCw, Play, Square, Trash2, Eye, Terminal,
  CheckCircle, AlertCircle, Clock, Package, Layers,
  ChevronDown, ChevronRight, Loader, Network, Globe,
} from 'lucide-react';
import { useWorkspace } from '../components/workspace';
import { getAgents } from '../api';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
const api = axios.create({ baseURL: 'http://localhost:8000' });

const TABS = [
  { id: 'agents',     labelKey: 'containers.tabs.agents',     icon: Package },
  { id: 'containers', labelKey: 'containers.tabs.containers', icon: Box },
  { id: 'network',    labelKey: 'containers.tabs.network',    icon: Network },
];

// ── helpers ───────────────────────────────────────────────────────────────────

function stateIcon(state) {
  const s = (state || '').toLowerCase();
  if (s === 'running') return <span className="inline-block w-2 h-2 rounded-full bg-green-500 mr-1.5" />;
  if (s === 'exited' || s === 'dead') return <span className="inline-block w-2 h-2 rounded-full bg-red-400 mr-1.5" />;
  return <span className="inline-block w-2 h-2 rounded-full bg-gray-400 mr-1.5" />;
}

function Card({ children, className = '' }) {
  return (
    <div className={`bg-white rounded-xl border border-gray-100 shadow-sm p-5 ${className}`}>
      {children}
    </div>
  );
}

function Badge({ children, color = 'gray' }) {
  const colors = {
    green:  'bg-green-50 text-green-700 border-green-200',
    red:    'bg-red-50 text-red-700 border-red-200',
    yellow: 'bg-yellow-50 text-yellow-700 border-yellow-200',
    blue:   'bg-blue-50 text-blue-700 border-blue-200',
    gray:   'bg-gray-50 text-gray-600 border-gray-200',
  };
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium border rounded-full px-2 py-0.5 ${colors[color] || colors.gray}`}>
      {children}
    </span>
  );
}

// ── Log viewer ────────────────────────────────────────────────────────────────

function LogViewer({ name, onClose }) {
  const { t } = useI18n();
  const [logs, setLogs] = useState('');
  const [loading, setLoading] = useState(true);
  const [tail, setTail] = useState(200);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get(`/api/containers/${name}/logs`, { params: { tail } });
      setLogs(data);
    } catch (e) {
      setLogs(`Error fetching logs: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [name, tail]);

  useEffect(() => { fetchLogs(); }, [fetchLogs]);

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b">
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-gray-500" />
            <span className="font-semibold text-sm text-gray-800">{name}</span>
          </div>
          <div className="flex items-center gap-3">
            <select
              value={tail}
              onChange={e => setTail(Number(e.target.value))}
              className="text-xs border rounded px-2 py-1"
            >
              {[50, 100, 200, 500, 1000].map(n => (
                <option key={n} value={n}>{t('containers.lastNLines', { count: n })}</option>
              ))}
            </select>
            <button onClick={fetchLogs} className="text-xs text-indigo-600 hover:text-indigo-700">
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
            <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-800">✕</button>
          </div>
        </div>
        <div className="flex-1 overflow-auto p-4 bg-gray-950 rounded-b-xl">
          {loading ? (
            <div className="flex items-center justify-center h-32 text-gray-400">
              <Loader className="w-5 h-5 animate-spin mr-2" /> {t('containers.loading')}
            </div>
          ) : (
            <pre className="text-xs text-green-400 whitespace-pre-wrap leading-5">
              {logs || '(no output)'}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Dockerfile viewer ─────────────────────────────────────────────────────────

function DockerfileViewer({ agentId, onClose }) {
  const { t } = useI18n();
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get(`/api/containers/dockerfile/${agentId}`)
      .then(({ data }) => setContent(data))
      .catch(e => setContent(`Error: ${e.message}`))
      .finally(() => setLoading(false));
  }, [agentId]);

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[70vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b">
          <span className="font-semibold text-sm">{t('containers.dockerfileFor', { agent: agentId })}</span>
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-800">✕</button>
        </div>
        <div className="flex-1 overflow-auto p-4 bg-gray-950 rounded-b-xl">
          {loading ? (
            <div className="flex items-center gap-2 text-gray-400 h-32 justify-center">
              <Loader className="w-4 h-4 animate-spin" /> {t('containers.loading')}
            </div>
          ) : (
            <pre className="text-xs text-cyan-300 whitespace-pre-wrap leading-5">{content}</pre>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Containers() {
  const { t } = useI18n();
  const { workspaceFilter } = useWorkspace();
  const [activeTab, setActiveTab] = useState('agents');
  const [agentsStatus, setAgentsStatus] = useState([]);
  const [wsAgentIds, setWsAgentIds] = useState(null); // null = show all
  const [containers, setContainers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [buildingBase, setBuildingBase] = useState(false);
  const [building, setBuilding] = useState({});   // agent_id -> bool
  const [stopping, setStopping] = useState({});
  const [removing, setRemoving] = useState({});
  const [logViewer, setLogViewer] = useState(null);   // container name
  const [dockerfileViewer, setDockerfileViewer] = useState(null); // agent id
  const [error, setError] = useState('');
  const [buildLog, setBuildLog] = useState('');
  const [noCache, setNoCache] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [agRes, ctrRes] = await Promise.all([
        api.get('/api/containers/agents-status'),
        api.get('/api/containers'),
      ]);
      setAgentsStatus(agRes.data.agents || []);
      setContainers(ctrRes.data.containers || []);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Live container status pushed over the shared stream while this page is open
  // (the backend emits `containers` snapshots only while subscribed).
  useChannel('containers', (ev) => {
    if (ev.type === 'snapshot') setContainers(ev.data || []);
  });

  // Re-fetch workspace agent list when workspace changes
  useEffect(() => {
    if (!workspaceFilter) {
      setWsAgentIds(null);
      return;
    }
    getAgents(workspaceFilter)
      .then(({ data }) => setWsAgentIds(new Set((Array.isArray(data) ? data : (data.agents || [])).map(a => a.id))))
      .catch(() => setWsAgentIds(null));
  }, [workspaceFilter]);

  const buildBase = async () => {
    setBuildingBase(true);
    setBuildLog('');
    setError('');
    try {
      const { data } = await api.post('/api/containers/build-base', { no_cache: noCache });
      setBuildLog(data.log || '');
      await refresh();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBuildingBase(false);
    }
  };

  const buildAgent = async (agentId) => {
    setBuilding(b => ({ ...b, [agentId]: true }));
    setBuildLog('');
    setError('');
    try {
      const { data } = await api.post(`/api/containers/build/${agentId}`, { no_cache: noCache });
      setBuildLog(data.log || '');
      await refresh();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBuilding(b => ({ ...b, [agentId]: false }));
    }
  };

  const stopContainer = async (name) => {
    setStopping(s => ({ ...s, [name]: true }));
    try {
      await api.post(`/api/containers/${name}/stop`);
      await refresh();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setStopping(s => ({ ...s, [name]: false }));
    }
  };

  const removeContainer = async (name) => {
    if (!window.confirm(`Remove container "${name}"?`)) return;
    setRemoving(s => ({ ...s, [name]: true }));
    try {
      await api.delete(`/api/containers/${name}`);
      await refresh();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setRemoving(s => ({ ...s, [name]: false }));
    }
  };

  const ensureNetwork = async () => {
    try {
      await api.post('/api/containers/network/ensure');
      alert(t('containers.networkReady'));
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    }
  };

  const visibleAgents = wsAgentIds
    ? agentsStatus.filter(a => wsAgentIds.has(a.agent_id))
    : agentsStatus;
  const visibleContainers = wsAgentIds
    ? containers.filter(c => !c.agent_id || wsAgentIds.has(c.agent_id))
    : containers;

  const baseExists = agentsStatus.some(a => a.base_exists);
  const runningCount = visibleContainers.filter(c => c.state === 'running').length;

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={Box}
        title={t('containers.containers')}
        description={t('containers.dockerImageBuildsAndContainer')}
        actions={<>
          <label className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
            <input
              type="checkbox"
              checked={noCache}
              onChange={e => setNoCache(e.target.checked)}
              className="accent-indigo-600"
            />
            --no-cache
          </label>
          <button
            onClick={refresh}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            {t('containers.refresh')}
          </button>
        </>}
      />

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: t('containers.stats.baseImage'), value: baseExists ? t('containers.built') : t('containers.notBuilt'), ok: baseExists },
          { label: t('containers.stats.agentImages'), value: `${visibleAgents.filter(a => a.image_exists).length} / ${visibleAgents.length}`, ok: true },
          { label: t('containers.stats.runningContainers'), value: runningCount, ok: runningCount > 0 },
        ].map(s => (
          <Card key={s.label} className="flex items-center gap-3">
            {s.ok
              ? <CheckCircle className="w-5 h-5 text-green-500 shrink-0" />
              : <AlertCircle className="w-5 h-5 text-yellow-500 shrink-0" />}
            <div>
              <p className="text-xs text-gray-500">{s.label}</p>
              <p className="text-sm font-semibold text-gray-800">{String(s.value)}</p>
            </div>
          </Card>
        ))}
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-100 rounded-xl p-1">
        {TABS.map(tab => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                activeTab === tab.id ? 'bg-white text-indigo-600 shadow-sm' : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              <Icon className="w-4 h-4" />
              <span>{t(tab.labelKey)}</span>
            </button>
          );
        })}
      </div>

      {/* ── Tab: Agent Images ── */}
      {activeTab === 'agents' && (
        <div className="space-y-4">
          {/* Base image card */}
          <Card>
            <div className="flex items-center justify-between mb-3">
              <div>
                <h2 className="text-sm font-semibold text-gray-800 flex items-center gap-2">
                  <Layers className="w-4 h-4 text-indigo-500" />
                  {t('containers.unifiedBaseImage')}
                </h2>
                <p className="text-xs text-gray-500 mt-0.5">
                  <code className="bg-gray-100 rounded px-1">agents-hub/base:latest</code>
                  {' '}— All agents derive from this image. Build it first.
                </p>
              </div>
              <div className="flex items-center gap-2">
                {baseExists
                  ? <Badge color="green"><CheckCircle className="w-3 h-3" /> {t('containers.built')}</Badge>
                  : <Badge color="yellow"><Clock className="w-3 h-3" /> {t('containers.notBuilt')}</Badge>}
                <button
                  onClick={buildBase}
                  disabled={buildingBase}
                  className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-50"
                >
                  {buildingBase ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                  {buildingBase ? t('containers.building') : t('containers.buildBase')}
                </button>
              </div>
            </div>
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-800">
              <strong>{t('containers.security')}</strong> {t('containers.containersRunOnThe')} <code>agents-hub</code> {t('containers.securityAfter')}
            </div>
          </Card>

          {/* Per-agent images */}
          <Card>
            <h2 className="text-sm font-semibold text-gray-800 mb-3">{t('containers.perAgentImages')}</h2>
            {visibleAgents.length === 0 && (
              <p className="text-sm text-gray-500">{t('containers.noAgentsRegistered')}</p>
            )}
            <div className="divide-y divide-gray-50">
              {visibleAgents.map(agent => (
                <div key={agent.agent_id} className="flex items-center justify-between py-3 first:pt-0 last:pb-0">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-800">{agent.agent_name}</p>
                    <p className="text-xs text-gray-400">{agent.image_tag}</p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {agent.http_expose && (
                      <Badge color="blue">
                        <Globe className="w-3 h-3" /> HTTP :{agent.http_host_port || agent.http_port}
                      </Badge>
                    )}
                    {agent.image_exists
                      ? <Badge color="green"><CheckCircle className="w-3 h-3" /> {t('containers.built')}</Badge>
                      : <Badge color="gray">{t('containers.notBuilt')}</Badge>}
                    <button
                      onClick={() => setDockerfileViewer(agent.agent_id)}
                      title={t('containers.previewDockerfile')}
                      className="text-gray-400 hover:text-indigo-600 p-1 rounded"
                    >
                      <Eye className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => buildAgent(agent.agent_id)}
                      disabled={building[agent.agent_id] || !agent.base_exists}
                      title={!agent.base_exists ? t('containers.buildBaseFirst') : t('containers.buildImage')}
                      className="flex items-center gap-1 bg-indigo-50 hover:bg-indigo-100 text-indigo-600 border border-indigo-200 px-2 py-1 rounded text-xs font-medium disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {building[agent.agent_id]
                        ? <Loader className="w-3 h-3 animate-spin" />
                        : <Play className="w-3 h-3" />}
                      Build
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </Card>

          {/* Build log */}
          {buildLog && (
            <Card>
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-sm font-semibold text-gray-800">{t('containers.buildLog')}</h2>
                <button onClick={() => setBuildLog('')} className="text-xs text-gray-400 hover:text-gray-600">{t('containers.clear')}</button>
              </div>
              <pre className="text-xs text-gray-700 bg-gray-50 rounded p-3 overflow-auto max-h-60 leading-5">
                {buildLog}
              </pre>
            </Card>
          )}
        </div>
      )}

      {/* ── Tab: Containers ── */}
      {activeTab === 'containers' && (
        <Card>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-gray-800">{t('containers.runningStoppedContainers')}</h2>
            <p className="text-xs text-gray-400">{t('containers.allContainersWithLabel')} <code>agents-hub.managed=true</code></p>
          </div>

          {visibleContainers.length === 0 ? (
            <div className="text-center py-12 text-gray-400">
              <Box className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p className="text-sm">{t('containers.noManagedContainersFound')}</p>
              <p className="text-xs mt-1">{t('containers.startANodeOrTask')}</p>
            </div>
          ) : (
            <div className="divide-y divide-gray-50">
              {visibleContainers.map(c => (
                <div key={c.id || c.name} className="flex items-center justify-between py-3 first:pt-0 last:pb-0">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-800 flex items-center gap-1">
                      {stateIcon(c.state)}
                      {c.name}
                    </p>
                    <p className="text-xs text-gray-400">
                      {c.image} · {c.status || c.state}
                      {c.agent_id && <> · {t('containers.agentLabel')}: <span className="">{c.agent_id}</span></>}
                    </p>
                    {c.http_url && c.state === 'running' && (
                      <a
                        href={c.http_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800 mt-0.5"
                      >
                        <Globe className="w-3 h-3" />
                        {c.http_url}
                      </a>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button
                      onClick={() => setLogViewer(c.name)}
                      title={t('containers.viewLogs')}
                      className="text-gray-400 hover:text-indigo-600 p-1.5 rounded hover:bg-indigo-50"
                    >
                      <Terminal className="w-3.5 h-3.5" />
                    </button>
                    {c.state === 'running' && (
                      <button
                        onClick={() => stopContainer(c.name)}
                        disabled={stopping[c.name]}
                        title={t('containers.stopContainer')}
                        className="text-yellow-600 hover:text-yellow-700 p-1.5 rounded hover:bg-yellow-50 disabled:opacity-40"
                      >
                        {stopping[c.name] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Square className="w-3.5 h-3.5" />}
                      </button>
                    )}
                    {c.state !== 'running' && (
                      <button
                        onClick={() => removeContainer(c.name)}
                        disabled={removing[c.name]}
                        title={t('containers.removeContainer')}
                        className="text-red-500 hover:text-red-600 p-1.5 rounded hover:bg-red-50 disabled:opacity-40"
                      >
                        {removing[c.name] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {/* ── Tab: Network ── */}
      {activeTab === 'network' && (
        <Card>
          <h2 className="text-sm font-semibold text-gray-800 mb-4">{t('containers.agentsHubDockerNetwork')}</h2>
          <div className="space-y-4">
            <div className="bg-gray-50 rounded-lg p-4 text-sm text-gray-700 space-y-2">
              <p>
                {t('containers.networkIntro')}{' '}
                <code className="bg-white border rounded px-1 py-0.5 text-xs">agents-hub</code>.
              </p>
              <p>
                {t('containers.networkReachBefore')}{' '}
                <code className="text-xs">agents-hub-node-abc123</code>) {t('containers.networkReachAfter')}
              </p>
              <p className="text-gray-500">
                {t('containers.networkAutoCreate')}
              </p>
            </div>
            <button
              onClick={ensureNetwork}
              className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium"
            >
              <Network className="w-4 h-4" />
              {t('containers.ensureNetworkExists')}
            </button>
          </div>
        </Card>
      )}

      {/* Modals */}
      {logViewer && <LogViewer name={logViewer} onClose={() => setLogViewer(null)} />}
      {dockerfileViewer && <DockerfileViewer agentId={dockerfileViewer} onClose={() => setDockerfileViewer(null)} />}
    </PageContainer>
  );
}
