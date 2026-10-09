import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  Box,
  Check,
  CheckCircle2,
  GitBranch,
  Info,
  Loader,
  Play,
  RefreshCw,
  Server,
  Square,
  Workflow,
  X,
} from 'lucide-react';
import { recheckImportedAgent, refreshAgentTopology } from '../api';
import {
  buildImportedAgentImage,
  getImportedAgentDocker,
  setImportedAgentRuntimeMode,
  startImportedAgentContainer,
  stopImportedAgentContainer,
} from '../api/agentImport';
import { useI18n } from '../i18n';
import GraphMirror from './GraphMirror';

/**
 * The imported-agent panel on an agent's page.
 *
 * An agent can be imported before it is runnable, so this is where the
 * remaining work gets done: point the record at the service once it is up,
 * re-run the checks, and see the same verdict the import dialog showed. It
 * renders nothing for agents that were not imported.
 */
export default function ImportedAgentPanel({ agent, workspace = '', onUpdated }) {
  const { t } = useI18n();
  const remote = agent?.remote;
  const [url, setUrl] = useState(remote?.url || '');
  const [readiness, setReadiness] = useState(remote?.readiness || null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');
  const [topology, setTopology] = useState(remote?.topology || null);
  const [fetchingGraph, setFetchingGraph] = useState(false);

  // Docker mode: the hub builds the image and runs a container per workspace.
  // Only an agent whose manifest ships a Dockerfile can be switched to it.
  const dockerCapable = !!remote?.dockerfile && remote?.kind !== 'a2a';
  const [mode, setMode] = useState(remote?.runtime_mode === 'docker' ? 'docker' : 'url');
  const [docker, setDocker] = useState(null);
  const [dockerBusy, setDockerBusy] = useState('');

  const loadDocker = useCallback(async () => {
    if (!dockerCapable || !agent?.id) return;
    try {
      const { data } = await getImportedAgentDocker(agent.id);
      setDocker(data);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    }
  }, [dockerCapable, agent?.id]);

  useEffect(() => {
    if (mode === 'docker') loadDocker();
  }, [mode, loadDocker]);

  if (!remote) return null;

  const ready = !!readiness?.runnable;

  const dockerAction = async (label, call) => {
    setDockerBusy(label);
    setError('');
    try {
      const { data } = await call();
      if (data?.report) setReadiness(data.report);
      await loadDocker();
      onUpdated?.();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setDockerBusy('');
    }
  };

  const switchMode = async (next) => {
    if (next === mode) return;
    setDockerBusy('mode');
    setError('');
    try {
      const { data } = await setImportedAgentRuntimeMode(agent.id, next);
      setMode(next);
      if (data?.report) setReadiness(data.report);
      onUpdated?.();
      // Entering Docker mode is only useful with an image: build it right away,
      // so the first run does not stall on a build nobody started.
      if (next === 'docker') {
        const { data: built } = await buildImportedAgentImage(agent.id);
        if (built?.report) setReadiness(built.report);
        await loadDocker();
      }
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setDockerBusy('');
    }
  };

  const recheck = async () => {
    setChecking(true);
    setError('');
    try {
      const { data } = await recheckImportedAgent(agent.id, {
        // In Docker mode the address belongs to the workspace's container,
        // not to the record: sending an empty URL would erase a saved one.
        url: mode === 'docker' ? undefined : url.trim(),
        workspace: workspace || undefined,
      });
      setReadiness(data.report);
      // A re-check re-fetches the graph too, so the picture cannot keep showing
      // the shape of a service the agent no longer points at.
      if (data.topology) setTopology(data.topology);
      onUpdated?.();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setChecking(false);
    }
  };

  const reloadGraph = async () => {
    setFetchingGraph(true);
    setError('');
    try {
      const { data } = await refreshAgentTopology(agent.id);
      setTopology(data.topology);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setFetchingGraph(false);
    }
  };

  return (
    <div className="bg-white p-6 shadow-md rounded-lg">
      <div className="flex items-start justify-between gap-4 mb-4">
        <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2">
          <Server className="w-4 h-4 text-indigo-500" /> {t('importedAgentPanel.importedAgent')}
        </h3>
        <span
          className={`inline-flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider px-2.5 py-1 rounded-full ${
            ready ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
          }`}
        >
          {ready ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
          {ready ? t('importedAgentPanel.ready') : t('importedAgentPanel.needsSetup')}
        </span>
      </div>

      <p className="text-sm text-gray-500 mb-4">
        {t('importedAgentPanel.runsOutsideHub')}{' '}
        {remote.stream_path
          ? t('importedAgentPanel.streams')
          : t('importedAgentPanel.doesNotStream')}
      </p>

      {/* Provenance */}
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-5 text-sm">
        <div>
          <dt className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('importedAgentPanel.repository')}</dt>
          <dd className="text-gray-700 break-all flex items-start gap-1.5">
            <GitBranch className="w-3.5 h-3.5 mt-0.5 text-gray-400 shrink-0" />
            {remote.repo_url || '—'}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('importedAgentPanel.commit')}</dt>
          <dd className="text-gray-700">
            <code>{remote.commit || '—'}</code>
            {remote.branch && <span className="text-gray-400"> · {remote.branch}</span>}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('importedAgentPanel.runEndpoint')}</dt>
          <dd className="text-gray-700 break-all">
            POST {(remote.url || t('importedAgentPanel.notSet')) + (remote.run_path || '/run')}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('importedAgentPanel.healthEndpoint')}</dt>
          <dd className="text-gray-700 break-all">
            GET {(remote.url || t('importedAgentPanel.notSet')) + (remote.health_path || '/health')}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('importedAgentPanel.graphEndpoint')}</dt>
          <dd className="text-gray-700 break-all">
            {remote.graph_path
              ? `GET ${(remote.url || t('importedAgentPanel.notSet')) + remote.graph_path}`
              : t('importedAgentPanel.notDeclared')}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('importedAgentPanel.streaming')}</dt>
          <dd className="text-gray-700 break-all">
            {remote.stream_path
              ? `POST ${(remote.url || t('importedAgentPanel.notSet')) + remote.stream_path}`
              : t('importedAgentPanel.notDeclared')}
          </dd>
        </div>
      </dl>

      {/* Where it runs: a service the operator runs, or a container the hub runs */}
      {dockerCapable && (
        <div className="mb-5 border border-gray-100 rounded-lg p-4" data-testid="imported-runtime">
          <h4 className="text-sm font-semibold text-gray-700 flex items-center gap-2 mb-3">
            <Box className="w-4 h-4 text-indigo-500" /> {t('importedAgentPanel.runtime')}
          </h4>
          <div className="space-y-2">
            {[
              ['url', t('importedAgentPanel.modeUrl'), t('importedAgentPanel.modeUrlHint')],
              ['docker', t('importedAgentPanel.modeDocker'), t('importedAgentPanel.modeDockerHint')],
            ].map(([value, label, hint]) => (
              <label key={value} className="flex items-start gap-2 text-sm cursor-pointer">
                <input
                  type="radio"
                  name="imported-runtime-mode"
                  value={value}
                  checked={mode === value}
                  disabled={dockerBusy === 'mode'}
                  onChange={() => switchMode(value)}
                  className="mt-1"
                />
                <span>
                  <span className="font-medium text-gray-800">{label}</span>
                  <span className="block text-gray-500 text-[13px]">{hint}</span>
                </span>
              </label>
            ))}
          </div>
          {mode === 'docker' && (
            <div className="mt-4 space-y-3">
              {docker && docker.docker_available === false && (
                <p className="text-sm text-amber-700">{t('importedAgentPanel.dockerUnavailable')} {docker.error}</p>
              )}
              <div className="flex items-center justify-between gap-3 text-sm">
                <span>
                  <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold mr-2">{t('importedAgentPanel.image')}</span>
                  <code>{docker?.image?.tag || '…'}</code>{' '}
                  <span className="text-gray-500">
                    {docker?.image?.exists
                      ? `${t('importedAgentPanel.imageBuilt')}${docker.image.built_at ? ` · ${new Date(docker.image.built_at).toLocaleString()}` : ''}`
                      : t('importedAgentPanel.imageNotBuilt')}
                  </span>
                </span>
                <button
                  onClick={() => dockerAction('build', () => buildImportedAgentImage(agent.id, !!docker?.image?.exists))}
                  disabled={!!dockerBusy}
                  className="text-xs text-indigo-600 font-semibold flex items-center gap-1.5 hover:text-indigo-700 disabled:opacity-50"
                >
                  {dockerBusy === 'build' || dockerBusy === 'mode'
                    ? <Loader className="w-3.5 h-3.5 animate-spin" />
                    : <RefreshCw className="w-3.5 h-3.5" />}
                  {dockerBusy === 'build' || dockerBusy === 'mode'
                    ? t('importedAgentPanel.building')
                    : docker?.image?.exists ? t('importedAgentPanel.rebuild') : t('importedAgentPanel.build')}
                </button>
              </div>
              <div>
                <div className="flex items-center justify-between gap-3 mb-1">
                  <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('importedAgentPanel.containers')}</span>
                  <span className="flex items-center gap-3">
                    <button
                      onClick={() => dockerAction('start', () => startImportedAgentContainer(agent.id, workspace || 'default'))}
                      disabled={!!dockerBusy || !docker?.image?.exists}
                      className="text-xs text-indigo-600 font-semibold flex items-center gap-1.5 hover:text-indigo-700 disabled:opacity-50"
                    >
                      {dockerBusy === 'start' ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                      {t('importedAgentPanel.startForWorkspace')} ({workspace || 'default'})
                    </button>
                    {(docker?.containers || []).length > 0 && (
                      <button
                        onClick={() => dockerAction('stop', () => stopImportedAgentContainer(agent.id, null))}
                        disabled={!!dockerBusy}
                        className="text-xs text-gray-600 font-semibold flex items-center gap-1.5 hover:text-gray-800 disabled:opacity-50"
                      >
                        <Square className="w-3.5 h-3.5" /> {t('importedAgentPanel.stopAll')}
                      </button>
                    )}
                  </span>
                </div>
                {(docker?.containers || []).length === 0 ? (
                  <p className="text-[13px] text-gray-500">{t('importedAgentPanel.noContainers')}</p>
                ) : (
                  <ul className="space-y-1.5">
                    {docker.containers.map((c) => (
                      <li key={c.name} className="text-sm flex items-start justify-between gap-3">
                        <span className="min-w-0">
                          <span className="font-medium text-gray-800">{c.workspace || c.name}</span>
                          <span className={`ml-2 text-[11px] uppercase tracking-wider font-bold ${c.state === 'running' ? 'text-emerald-600' : 'text-amber-600'}`}>{c.state}</span>
                          {c.url && <span className="text-gray-500 break-all"> · {c.url}</span>}
                          {c.mounts?.length > 0 && (
                            <span className="block text-[12px] text-gray-400 break-all">
                              {t('importedAgentPanel.mounts')}: {c.mounts.join(', ')}
                            </span>
                          )}
                        </span>
                        <button
                          onClick={() => dockerAction(`stop:${c.name}`, () => stopImportedAgentContainer(agent.id, c.workspace))}
                          disabled={!!dockerBusy}
                          className="text-xs text-gray-600 font-semibold flex items-center gap-1 hover:text-gray-800 disabled:opacity-50 shrink-0"
                        >
                          <Square className="w-3 h-3" /> {t('importedAgentPanel.stop')}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Endpoint + re-check */}
      <div className="mb-5">
        <label className="block text-[10px] text-gray-400 uppercase tracking-wider font-semibold mb-1">
          {t('importedAgentPanel.serviceUrl')}
        </label>
        <div className="flex gap-2">
          <input
            value={mode === 'docker' ? '' : url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && recheck()}
            placeholder={mode === 'docker' ? t('importedAgentPanel.modeDocker') : 'http://localhost:8410'}
            disabled={mode === 'docker'}
            className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm disabled:bg-gray-50 disabled:text-gray-400"
          />
          <button
            onClick={recheck}
            disabled={checking}
            className="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-bold flex items-center gap-2 hover:bg-indigo-700 disabled:opacity-50"
          >
            {checking ? <Loader className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Save &amp; re-check
          </button>
        </div>
        {error && <p className="text-sm text-red-600 mt-2">{error}</p>}
      </div>

      {/* The agent's own graph, when it publishes one. Read-only by design: this
          is a picture of something this hub does not execute. */}
      {remote.graph_path && (
        <div className="mb-5 border border-gray-100 rounded-lg p-4">
          <div className="flex items-center justify-between gap-3 mb-3">
            <h4 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
              <Workflow className="w-4 h-4 text-indigo-500" />
              {t('importedAgentPanel.graphTitle')}
              {topology?.framework && topology.framework !== 'unknown' && (
                <span className="text-[10px] uppercase tracking-wider font-bold text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-full">
                  {topology.framework}
                </span>
              )}
            </h4>
            <button
              onClick={reloadGraph}
              disabled={fetchingGraph}
              className="text-xs text-indigo-600 font-semibold flex items-center gap-1.5 hover:text-indigo-700 disabled:opacity-50"
            >
              {fetchingGraph ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              {t('importedAgentPanel.refreshGraph')}
            </button>
          </div>
          <GraphMirror topology={topology} />
          <p className="text-[11px] text-gray-400 mt-3">
            {t('importedAgentPanel.graphIsAMirror')}
            {topology?.fetched_at && ` · ${new Date(topology.fetched_at).toLocaleString()}`}
          </p>
        </div>
      )}

      {/* Verdict */}
      {readiness && (
        <div>
          <p
            className={`text-sm font-semibold mb-2 ${
              ready ? 'text-emerald-700' : 'text-amber-700'
            }`}
          >
            {readiness.summary}
          </p>
          <ul className="space-y-1.5">
            {(readiness.checks || []).map((c) => (
              <li key={c.id} className="flex items-start gap-2 text-sm">
                <span className="mt-0.5 shrink-0">
                  {c.ok ? (
                    <Check className="w-4 h-4 text-emerald-600" />
                  ) : c.required ? (
                    <X className="w-4 h-4 text-red-600" />
                  ) : (
                    <Info className="w-4 h-4 text-amber-600" />
                  )}
                </span>
                <span className="min-w-0">
                  <span className="font-medium text-gray-800">{c.label}</span>
                  {c.detail && <span className="text-gray-500"> — {c.detail}</span>}
                  {!c.ok && c.fix && (
                    <span className="block text-gray-500 italic text-[13px]">{c.fix}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
          {readiness.checked_at && (
            <p className="text-[11px] text-gray-400 mt-3">
              Last checked {new Date(readiness.checked_at).toLocaleString()}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
