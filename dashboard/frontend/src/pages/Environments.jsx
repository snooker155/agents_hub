import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import {
  getEnvironments,
  createEnvironment,
  updateEnvironment,
  archiveEnvironment,
  deleteEnvironment,
  setDefaultEnvironment,
  getEnvironmentUsage,
  buildEnvironmentImage,
} from '../api';
import {
  Container,
  Plus,
  X,
  Loader,
  RefreshCw,
  Star,
  Archive,
  Trash2,
  Hammer,
  Eye,
  Globe,
  ShieldOff,
  ShieldAlert,
  Pencil,
  CheckCircle,
  XCircle,
} from 'lucide-react';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';

// ── helpers ───────────────────────────────────────────────────────────────

const NETWORK_TYPES = ['unrestricted', 'none', 'limited'];
const MODES = ['inherit', 'local', 'docker'];

function linesToList(text) {
  return (text || '')
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean);
}

function listToLines(list) {
  return (list || []).join('\n');
}

function envMapToLines(env) {
  return Object.entries(env || {}).map(([k, v]) => `${k}=${v}`).join('\n');
}

function linesToEnvMap(text) {
  const out = {};
  for (const line of linesToList(text)) {
    const idx = line.indexOf('=');
    if (idx === -1) continue;
    out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
  }
  return out;
}

function NetworkBadge({ network, t }) {
  const type = network?.type || 'unrestricted';
  if (type === 'none') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-50 text-red-700 border border-red-200">
        <ShieldOff className="w-3 h-3" /> {t('environments.network.none')}
      </span>
    );
  }
  if (type === 'limited') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-700 border border-amber-200">
        <ShieldAlert className="w-3 h-3" /> {t('environments.network.limited')}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200">
      <Globe className="w-3 h-3" /> {t('environments.network.unrestricted')}
    </span>
  );
}

function ModeBadge({ mode, t }) {
  const cls = mode === 'docker'
    ? 'bg-blue-50 text-blue-700 border-blue-200'
    : mode === 'local'
      ? 'bg-gray-100 text-gray-600 border-gray-200'
      : 'bg-gray-50 text-gray-500 border-gray-200';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${cls}`}>
      {t(`environments.mode.${mode}`)}
    </span>
  );
}

// ── create / edit modal ──────────────────────────────────────────────────

function EnvironmentModal({ env, workspace, onClose, onSaved }) {
  const { t } = useI18n();
  const isEdit = !!env;
  const [name, setName] = useState(env?.name || '');
  const [description, setDescription] = useState(env?.description || '');
  const [scope, setScope] = useState(env ? (env.workspace ? 'workspace' : 'global') : (workspace ? 'workspace' : 'global'));
  const [mode, setMode] = useState(env?.mode || 'inherit');
  const [image, setImage] = useState(env?.image || '');
  const [packagesText, setPackagesText] = useState(listToLines(env?.packages));
  const [networkType, setNetworkType] = useState(env?.network?.type || 'unrestricted');
  const [allowedHostsText, setAllowedHostsText] = useState(listToLines(env?.network?.allowed_hosts));
  const [allowPkgMgrs, setAllowPkgMgrs] = useState(env?.network?.allow_package_managers ?? true);
  const [memory, setMemory] = useState(env?.limits?.memory || '');
  const [cpus, setCpus] = useState(env?.limits?.cpus || '');
  const [pidsLimit, setPidsLimit] = useState(env?.limits?.pids_limit ?? '');
  const [envText, setEnvText] = useState(envMapToLines(env?.env));
  const [isDefault, setIsDefault] = useState(env?.is_default || false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSave = async () => {
    if (!name.trim()) { setError(t('environments.errors.nameRequired')); return; }
    setSaving(true);
    setError('');
    const payload = {
      name: name.trim(),
      description: description.trim(),
      workspace: scope === 'workspace' ? (workspace || null) : null,
      mode,
      image: mode === 'docker' ? (image.trim() || null) : null,
      packages: mode === 'docker' ? linesToList(packagesText) : [],
      network: {
        type: networkType,
        allowed_hosts: networkType === 'limited' ? linesToList(allowedHostsText) : [],
        allow_package_managers: allowPkgMgrs,
      },
      limits: {
        memory: memory.trim() || null,
        cpus: cpus.trim() || null,
        pids_limit: pidsLimit === '' ? null : Number(pidsLimit),
      },
      env: linesToEnvMap(envText),
      is_default: isDefault,
    };
    try {
      if (isEdit) await updateEnvironment(env.id, payload);
      else await createEnvironment(payload);
      onSaved();
    } catch (err) {
      setError(err?.response?.data?.detail || t('environments.errors.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 space-y-4 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-gray-900">{isEdit ? t('environments.editEnvironment') : t('environments.newEnvironment')}</h2>
          <button onClick={onClose} className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('environments.name')}</label>
          <input
            type="text"
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('environments.namePlaceholder')}
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('environments.description')}</label>
          <textarea
            rows={2}
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        <div className="flex gap-3">
          <div className="flex-1">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('environments.scope')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              disabled={!workspace}
            >
              <option value="global">{t('environments.scopeGlobal')}</option>
              {workspace && <option value="workspace">{t('environments.scopeWorkspace', { workspace })}</option>}
            </select>
          </div>
          <div className="flex-1">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('environments.mode.label')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={mode}
              onChange={(e) => setMode(e.target.value)}
            >
              {MODES.map((m) => <option key={m} value={m}>{t(`environments.mode.${m}`)}</option>)}
            </select>
          </div>
        </div>

        {mode === 'docker' && (
          <>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('environments.image')}</label>
              <input
                type="text"
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
                value={image}
                onChange={(e) => setImage(e.target.value)}
                placeholder={t('environments.imagePlaceholder')}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('environments.packages')}</label>
              <textarea
                rows={3}
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
                value={packagesText}
                onChange={(e) => setPackagesText(e.target.value)}
                placeholder={'numpy\npandas'}
              />
              <p className="text-[11px] text-gray-400 mt-1">{t('environments.packagesHint')}</p>
            </div>
          </>
        )}

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('environments.network.label')}</label>
          <select
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            value={networkType}
            onChange={(e) => setNetworkType(e.target.value)}
          >
            {NETWORK_TYPES.map((n) => <option key={n} value={n}>{t(`environments.network.${n}`)}</option>)}
          </select>
          {networkType === 'limited' && (
            <textarea
              rows={2}
              className="mt-2 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={allowedHostsText}
              onChange={(e) => setAllowedHostsText(e.target.value)}
              placeholder={'api.example.com\ngithub.com'}
            />
          )}
          <label className="mt-2 flex items-center gap-2 text-xs text-gray-600 cursor-pointer">
            <input
              type="checkbox"
              checked={allowPkgMgrs}
              onChange={(e) => setAllowPkgMgrs(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
            />
            {t('environments.network.allowPackageManagers')}
          </label>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('environments.limits.label')}</label>
          <div className="flex gap-3">
            <input
              type="text"
              className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={memory}
              onChange={(e) => setMemory(e.target.value)}
              placeholder={t('environments.limits.memoryPlaceholder')}
            />
            <input
              type="text"
              className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={cpus}
              onChange={(e) => setCpus(e.target.value)}
              placeholder={t('environments.limits.cpusPlaceholder')}
            />
            <input
              type="number"
              min="0"
              className="w-28 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={pidsLimit}
              onChange={(e) => setPidsLimit(e.target.value)}
              placeholder={t('environments.limits.pidsPlaceholder')}
            />
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('environments.envVars')}</label>
          <textarea
            rows={2}
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
            value={envText}
            onChange={(e) => setEnvText(e.target.value)}
            placeholder={'KEY=value'}
          />
        </div>

        <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
          <input
            type="checkbox"
            checked={isDefault}
            onChange={(e) => setIsDefault(e.target.checked)}
            className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
          />
          {t('environments.setAsDefault')}
        </label>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50">
            {t('environments.cancel')}
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {saving ? <Loader className="w-4 h-4 animate-spin" /> : <Container className="w-4 h-4" />}
            {isEdit ? t('environments.save') : t('environments.create')}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── usage drawer ──────────────────────────────────────────────────────────

function UsageDrawer({ env, onClose }) {
  const { t } = useI18n();
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getEnvironmentUsage(env.id)
      .then(({ data }) => { if (!cancelled) setUsage(data); })
      .catch(() => { if (!cancelled) setUsage({ nodes: [], jobs: [], runs: [] }); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [env.id]);

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex justify-end" onClick={onClose}>
      <div className="bg-white h-full w-full max-w-md shadow-xl p-6 space-y-5 overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-gray-900">{t('environments.usageFor', { name: env.name })}</h2>
          <button onClick={onClose} className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100">
            <X className="w-5 h-5" />
          </button>
        </div>
        {loading ? (
          <div className="flex justify-center py-10"><Loader className="w-5 h-5 animate-spin text-indigo-500" /></div>
        ) : (
          <>
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">{t('environments.usageNodes')}</h3>
              {(usage?.nodes || []).length === 0 ? (
                <p className="text-sm text-gray-400">{t('environments.usageNone')}</p>
              ) : (
                <ul className="space-y-1.5">
                  {usage.nodes.map((n) => (
                    <li key={n.node_id}>
                      <Link to={`/nodes/${n.node_id}`} className="text-sm text-indigo-600 hover:underline">
                        {n.agent_name || n.agent_id} · {n.node_id.slice(0, 8)}…
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">{t('environments.usageJobs')}</h3>
              {(usage?.jobs || []).length === 0 ? (
                <p className="text-sm text-gray-400">{t('environments.usageNone')}</p>
              ) : (
                <ul className="space-y-1.5">
                  {usage.jobs.map((j) => (
                    <li key={j.id}>
                      <Link to="/deployments" className="text-sm text-indigo-600 hover:underline">
                        {j.title || j.id}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">{t('environments.usageRuns')}</h3>
              {(usage?.runs || []).length === 0 ? (
                <p className="text-sm text-gray-400">{t('environments.usageNone')}</p>
              ) : (
                <ul className="space-y-1.5">
                  {usage.runs.map((r) => (
                    <li key={r.run_id || r.id}>
                      <Link to={`/messages/${r.run_id || r.id}`} className="text-sm text-indigo-600 hover:underline">
                        {r.run_id || r.id}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── main page ──────────────────────────────────────────────────────────────

export default function Environments() {
  const { t } = useI18n();
  const { workspaceFilter } = useWorkspace();
  const [environments, setEnvironments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [modalEnv, setModalEnv] = useState(undefined); // undefined = closed, null = create, object = edit
  const [usageEnv, setUsageEnv] = useState(null);
  const [acting, setActing] = useState({});
  const [buildResult, setBuildResult] = useState({});

  const fetchData = useCallback(async () => {
    try {
      const { data } = await getEnvironments(workspaceFilter, includeArchived);
      setEnvironments(data || []);
    } catch (err) {
      console.error('Failed to load environments', err);
    } finally {
      setLoading(false);
    }
  }, [workspaceFilter, includeArchived]);

  useEffect(() => { setLoading(true); fetchData(); }, [fetchData]);

  const act = async (id, fn) => {
    setActing((s) => ({ ...s, [id]: true }));
    try {
      await fn();
      await fetchData();
    } catch (err) {
      window.alert(err?.response?.data?.detail || t('environments.errors.actionFailed'));
    } finally {
      setActing((s) => ({ ...s, [id]: false }));
    }
  };

  const handleBuild = async (env) => {
    setActing((s) => ({ ...s, [env.id]: true }));
    try {
      const { data } = await buildEnvironmentImage(env.id);
      setBuildResult((s) => ({ ...s, [env.id]: data }));
    } catch (err) {
      setBuildResult((s) => ({ ...s, [env.id]: { ok: false, error: err?.response?.data?.detail || t('environments.errors.buildFailed') } }));
    } finally {
      setActing((s) => ({ ...s, [env.id]: false }));
    }
  };

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={Container}
        title={t('environments.environments')}
        description={t('environments.pageDescription')}
        actions={<>
          <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={(e) => setIncludeArchived(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
            />
            {t('environments.showArchived')}
          </label>
          <button
            onClick={fetchData}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" /> {t('environments.refresh')}
          </button>
          <button
            onClick={() => setModalEnv(null)}
            className="flex items-center gap-2 px-3 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
          >
            <Plus className="w-4 h-4" /> {t('environments.newEnvironment')}
          </button>
        </>}
      />

      {loading ? (
        <div className="bg-white rounded-xl border border-gray-200 flex justify-center py-16">
          <Loader className="w-6 h-6 animate-spin text-indigo-500" />
        </div>
      ) : environments.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 text-center py-16">
          <Container className="w-10 h-10 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500 text-sm">{t('environments.noEnvironments')}</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50">
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('environments.name')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('environments.scope')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('environments.mode.label')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('environments.network.label')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('environments.limits.label')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('environments.usage')}</th>
                <th className="text-right px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('environments.actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {environments.map((env) => {
                const busy = acting[env.id];
                const inUse = (env.usage_counts?.nodes || 0) > 0 || (env.usage_counts?.jobs || 0) > 0;
                const archived = !!env.archived_at;
                const build = buildResult[env.id];
                return (
                  <tr key={env.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-gray-900">{env.name}</span>
                        {env.is_default && (
                          <span className="inline-flex items-center gap-0.5 text-[10px] font-bold uppercase bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded">
                            <Star className="w-2.5 h-2.5" /> {t('environments.default')}
                          </span>
                        )}
                        {archived && (
                          <span className="text-[10px] font-bold uppercase bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">
                            {t('environments.archived')}
                          </span>
                        )}
                      </div>
                      {env.description && <div className="text-xs text-gray-400 mt-0.5 truncate max-w-xs">{env.description}</div>}
                      {build && (
                        <div className={`text-xs mt-1 flex items-center gap-1 ${build.ok ? 'text-emerald-600' : 'text-red-600'}`}>
                          {build.ok ? <CheckCircle className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                          {build.ok ? build.image : build.error}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-gray-600 text-xs">
                      {env.workspace ? env.workspace : <span className="italic text-gray-400">{t('environments.scopeGlobal')}</span>}
                    </td>
                    <td className="px-4 py-3"><ModeBadge mode={env.mode} t={t} /></td>
                    <td className="px-4 py-3"><NetworkBadge network={env.network} t={t} /></td>
                    <td className="px-4 py-3 text-gray-600 text-xs">
                      {[env.limits?.memory, env.limits?.cpus && `${env.limits.cpus} cpu`, env.limits?.pids_limit && `${env.limits.pids_limit} pids`]
                        .filter(Boolean).join(' · ') || <span className="text-gray-400">—</span>}
                    </td>
                    <td className="px-4 py-3 text-gray-600 text-xs">
                      {t('environments.usageCounts', { nodes: env.usage_counts?.nodes || 0, jobs: env.usage_counts?.jobs || 0 })}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1.5">
                        {busy ? (
                          <Loader className="w-4 h-4 animate-spin text-gray-400" />
                        ) : (
                          <>
                            <button
                              title={t('environments.usage')}
                              onClick={() => setUsageEnv(env)}
                              className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50"
                            ><Eye className="w-4 h-4" /></button>
                            {!archived && (
                              <button
                                title={t('environments.edit')}
                                onClick={() => setModalEnv(env)}
                                className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50"
                              ><Pencil className="w-4 h-4" /></button>
                            )}
                            {!archived && !env.is_default && (
                              <button
                                title={t('environments.makeDefault')}
                                onClick={() => act(env.id, () => setDefaultEnvironment(env.id))}
                                className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50"
                              ><Star className="w-4 h-4" /></button>
                            )}
                            {env.mode === 'docker' && !archived && (
                              <button
                                title={t('environments.buildImage')}
                                onClick={() => handleBuild(env)}
                                className="p-1.5 rounded text-gray-400 hover:text-blue-600 hover:bg-blue-50"
                              ><Hammer className="w-4 h-4" /></button>
                            )}
                            {!archived && (
                              <button
                                title={t('environments.archive')}
                                onClick={() => act(env.id, () => archiveEnvironment(env.id))}
                                className="p-1.5 rounded text-gray-400 hover:text-amber-600 hover:bg-amber-50"
                              ><Archive className="w-4 h-4" /></button>
                            )}
                            <button
                              title={inUse ? t('environments.deleteDisabled') : t('environments.delete')}
                              disabled={inUse}
                              onClick={() => {
                                if (window.confirm(t('environments.confirmDelete', { name: env.name }))) act(env.id, () => deleteEnvironment(env.id));
                              }}
                              className="p-1.5 rounded text-gray-400 hover:text-red-600 hover:bg-red-50 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-gray-400"
                            ><Trash2 className="w-4 h-4" /></button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {modalEnv !== undefined && (
        <EnvironmentModal
          env={modalEnv}
          workspace={workspaceFilter}
          onClose={() => setModalEnv(undefined)}
          onSaved={() => { setModalEnv(undefined); fetchData(); }}
        />
      )}
      {usageEnv && (
        <UsageDrawer env={usageEnv} onClose={() => setUsageEnv(null)} />
      )}
    </PageContainer>
  );
}
