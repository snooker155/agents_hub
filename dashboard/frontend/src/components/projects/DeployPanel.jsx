import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Rocket, Square, RotateCw, Hammer, Search, Loader, RefreshCw, Copy, ExternalLink,
  Link2, Globe, Lock, History, Terminal, Plus, Trash2, Save, Eye, EyeOff, AlertCircle,
  CheckCircle, Clock,
} from 'lucide-react';

import {
  getProjectDeployment, updateProjectDeployment, detectProjectDeployment, deployProject,
  restartProjectDeployment, stopProjectDeployment, getProjectDeploymentLogs,
  getProjectDeploymentEvents, setProjectDeploymentVisibility, resetProjectDeploymentLink,
  getEnvironments,
} from '../../api';
import PreviewFrame from '../preview/PreviewFrame';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';

/*
 * The Deploy tab of a project (docs/project-deployments.md): the services
 * the hub runs for it, the buttons that start, stop, restart and rebuild
 * them, their logs and the journal, the running app inside the hub through
 * the ticket proxy, and the share link that opens it from anywhere the hub
 * is reachable (and from the agent's own browser).
 *
 * The record is polled while something is building or starting, and every
 * ten seconds while it runs, since the supervisor may restart a service
 * behind the page's back.
 */

const POLL_ACTIVE_MS = 2500;
const POLL_IDLE_MS = 10000;
// The logs pane and the journal share one height, so the two columns line up.
const PANE_HEIGHT = 360;

const STATUS_STYLE = {
  running: 'bg-green-50 text-green-700 border-green-200',
  starting: 'bg-amber-50 text-amber-700 border-amber-200',
  building: 'bg-amber-50 text-amber-700 border-amber-200',
  unhealthy: 'bg-orange-50 text-orange-700 border-orange-200',
  failed: 'bg-red-50 text-red-700 border-red-200',
  paused: 'bg-red-50 text-red-700 border-red-200',
  stopped: 'bg-gray-50 text-gray-500 border-gray-200',
};

export function DeployStatusPill({ status, t }) {
  const cls = STATUS_STYLE[status] || STATUS_STYLE.stopped;
  const busy = status === 'building' || status === 'starting';
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${cls}`}>
      {busy ? <Loader className="w-3 h-3 animate-spin" /> : null}
      {t(`projectDeploy.status.${status}`)}
    </span>
  );
}

function fmtDate(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return iso;
  }
}

const field = 'w-full border border-gray-300 rounded-lg px-2.5 py-1.5 text-sm focus:outline-none';
const label = 'block text-[11px] font-semibold text-gray-500 uppercase tracking-wider mb-1';

function envToText(env) {
  return Object.entries(env || {}).map(([k, v]) => `${k}=${v}`).join('\n');
}

function textToEnv(text) {
  const out = {};
  String(text || '').split('\n').forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) return;
    const idx = trimmed.indexOf('=');
    if (idx <= 0) return;
    out[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1);
  });
  return out;
}

function ServiceEditor({ svc, onChange, onRemove, t }) {
  const set = (k, v) => onChange({ ...svc, [k]: v });
  return (
    <div className="border border-gray-200 rounded-lg p-3 space-y-2 bg-white">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <div>
          <label className={label}>{t('projectDeploy.svc.name')}</label>
          <input className={field} value={svc.name || ''} onChange={(e) => set('name', e.target.value)} />
        </div>
        <div>
          <label className={label}>{t('projectDeploy.svc.kind')}</label>
          <select className={field} value={svc.kind || 'other'} onChange={(e) => set('kind', e.target.value)}>
            {['frontend', 'backend', 'other'].map((k) => <option key={k} value={k}>{t(`projectDeploy.kind.${k}`)}</option>)}
          </select>
        </div>
        <div>
          <label className={label}>{t('projectDeploy.svc.language')}</label>
          <select className={field} value={svc.language || 'node'} onChange={(e) => set('language', e.target.value)}>
            {['node', 'python', 'static', 'docker'].map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </div>
        <div>
          <label className={label}>{t('projectDeploy.svc.port')}</label>
          <input className={field} type="number" value={svc.port ?? ''} onChange={(e) => set('port', Number(e.target.value))} />
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <div>
          <label className={label}>{t('projectDeploy.svc.path')}</label>
          <input className={field} value={svc.path || ''} placeholder="frontend" onChange={(e) => set('path', e.target.value)} />
        </div>
        <div>
          <label className={label}>{t('projectDeploy.svc.dockerfile')}</label>
          <input className={field} value={svc.dockerfile || ''} placeholder="Dockerfile" onChange={(e) => set('dockerfile', e.target.value || null)} />
        </div>
        <div>
          <label className={label}>{t('projectDeploy.svc.install')}</label>
          <input className={`${field} font-mono`} value={svc.install_command || ''} placeholder="npm ci" onChange={(e) => set('install_command', e.target.value || null)} />
        </div>
        <div>
          <label className={label}>{t('projectDeploy.svc.command')}</label>
          <input className={`${field} font-mono`} value={svc.command || ''} placeholder="npm run dev -- --host 0.0.0.0 --port $PORT" onChange={(e) => set('command', e.target.value || null)} />
        </div>
        <div>
          <label className={label}>{t('projectDeploy.svc.image')}</label>
          <input className={`${field} font-mono`} value={svc.image || ''} placeholder="node:20-bookworm-slim" onChange={(e) => set('image', e.target.value || null)} />
        </div>
        <div>
          <label className={label}>{t('projectDeploy.svc.health')}</label>
          <input className={`${field} font-mono`} value={svc.health_path ?? ''} placeholder="/" onChange={(e) => set('health_path', e.target.value || null)} />
        </div>
      </div>
      <div>
        <label className={label}>{t('projectDeploy.svc.env')}</label>
        <textarea className={`${field} font-mono`} rows={2} value={svc._envText ?? envToText(svc.env)}
          onChange={(e) => onChange({ ...svc, _envText: e.target.value, env: textToEnv(e.target.value) })} />
      </div>
      <div className="flex justify-end">
        <button type="button" onClick={onRemove} className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-700">
          <Trash2 className="w-3.5 h-3.5" /> {t('projectDeploy.svc.remove')}
        </button>
      </div>
    </div>
  );
}

function ConfigForm({ dep, environments, onSaved, t }) {
  const toast = useToast();
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setForm({
      mode: dep.mode,
      compose_file: dep.compose_file || '',
      environment_id: dep.environment_id || '',
      primary_service: dep.primary_service || '',
      restart_on_exit: !!dep.restart_on_exit,
      env: envToText(dep.env),
      services: (dep.services || []).map((s) => ({ ...s })),
    });
  }, [dep.id, dep.updated_at]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!form) return null;
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const setService = (i, svc) => setForm((f) => ({ ...f, services: f.services.map((s, j) => (j === i ? svc : s)) }));

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      const payload = {
        mode: form.mode,
        compose_file: form.compose_file,
        environment_id: form.environment_id,
        primary_service: form.primary_service,
        restart_on_exit: form.restart_on_exit,
        env: textToEnv(form.env),
        services: form.services.map(({ _envText, ...s }) => s),
      };
      const { data } = await updateProjectDeployment(dep.project_id, payload);
      onSaved(data);
      toast.success(t('projectDeploy.saved'));
    } catch (err) {
      toast.error(t('projectDeploy.saveFailed'), errorDetail(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <div>
          <label className={label}>{t('projectDeploy.mode')}</label>
          <select className={field} value={form.mode} onChange={(e) => set('mode', e.target.value)}>
            {['docker', 'compose', 'local'].map((m) => <option key={m} value={m}>{t(`projectDeploy.modes.${m}`)}</option>)}
          </select>
        </div>
        {form.mode === 'compose' && (
          <div>
            <label className={label}>{t('projectDeploy.composeFile')}</label>
            <input className={`${field} font-mono`} value={form.compose_file} placeholder="docker-compose.yml" onChange={(e) => set('compose_file', e.target.value)} />
          </div>
        )}
        <div>
          <label className={label}>{t('projectDeploy.environment')}</label>
          <select className={field} value={form.environment_id} onChange={(e) => set('environment_id', e.target.value)}>
            <option value="">{t('projectDeploy.noEnvironment')}</option>
            {(environments || []).map((env) => <option key={env.id} value={env.id}>{env.name}</option>)}
          </select>
        </div>
        <div>
          <label className={label}>{t('projectDeploy.primary')}</label>
          <select className={field} value={form.primary_service} onChange={(e) => set('primary_service', e.target.value)}>
            <option value="">{t('projectDeploy.primaryAuto')}</option>
            {form.services.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>
        </div>
      </div>
      <label className="inline-flex items-center gap-2 text-sm text-gray-700">
        <input type="checkbox" checked={form.restart_on_exit} onChange={(e) => set('restart_on_exit', e.target.checked)} />
        {t('projectDeploy.restartOnExit')}
      </label>
      <div>
        <label className={label}>{t('projectDeploy.sharedEnv')}</label>
        <textarea className={`${field} font-mono`} rows={2} value={form.env} onChange={(e) => set('env', e.target.value)} />
      </div>
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className={label}>{t('projectDeploy.services')}</span>
          <button type="button" onClick={() => set('services', [...form.services, { name: `service${form.services.length + 1}`, kind: 'other', language: 'node', port: 3000, env: {} }])}
            className="inline-flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800">
            <Plus className="w-3.5 h-3.5" /> {t('projectDeploy.addService')}
          </button>
        </div>
        {form.services.length === 0 && <p className="text-xs text-gray-400">{t('projectDeploy.noServices')}</p>}
        {form.services.map((svc, i) => (
          <ServiceEditor key={i} svc={svc} t={t} onChange={(s) => setService(i, s)}
            onRemove={() => set('services', form.services.filter((_, j) => j !== i))} />
        ))}
      </div>
      <div className="flex justify-end">
        <button type="submit" disabled={saving}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50">
          {saving ? <Loader className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} {t('projectDeploy.save')}
        </button>
      </div>
    </form>
  );
}

function LogsPane({ projectId, dep, t }) {
  const services = useMemo(() => [...(dep.services || []).map((s) => s.name), 'build'], [dep.services]);
  const [service, setService] = useState(services[0] || 'build');
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  const [follow, setFollow] = useState(true);
  const pre = useRef(null);

  useEffect(() => {
    if (!services.includes(service)) setService(services[0] || 'build');
  }, [services, service]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await getProjectDeploymentLogs(projectId, service, 400);
      setText(data.text || '');
    } catch (err) {
      setText(errorDetail(err) || '');
    } finally {
      setLoading(false);
    }
  }, [projectId, service]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!follow) return undefined;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [follow, load]);
  useEffect(() => {
    if (follow && pre.current) pre.current.scrollTop = pre.current.scrollHeight;
  }, [text, follow]);

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden bg-white flex flex-col" style={{ height: PANE_HEIGHT }}>
      <div className="flex items-center gap-2 px-2 py-1.5 border-b border-gray-100 bg-gray-50 shrink-0">
        <Terminal className="w-3.5 h-3.5 text-gray-400" />
        <select className="text-xs border border-gray-200 rounded px-2 py-1" value={service} onChange={(e) => setService(e.target.value)}>
          {services.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <label className="inline-flex items-center gap-1 text-xs text-gray-500 ml-2">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> {t('projectDeploy.follow')}
        </label>
        <button type="button" onClick={load} className="ml-auto text-gray-400 hover:text-indigo-600 p-1">
          {loading ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
        </button>
      </div>
      <pre ref={pre} className="text-[11px] leading-4 font-mono p-3 overflow-auto bg-gray-950 text-gray-100 flex-1 min-h-0">
        {text || t('projectDeploy.noLogs')}
      </pre>
    </div>
  );
}

function EventsPane({ projectId, refreshKey, t }) {
  const [items, setItems] = useState([]);
  useEffect(() => {
    let cancelled = false;
    getProjectDeploymentEvents(projectId, 100).then(({ data }) => {
      if (!cancelled) setItems(data.items || []);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [projectId, refreshKey]);
  if (!items.length) {
    return (
      <div className="border border-gray-200 rounded-lg bg-white flex items-center justify-center text-xs text-gray-400" style={{ height: PANE_HEIGHT }}>
        {t('projectDeploy.noEvents')}
      </div>
    );
  }
  return (
    <ul className="divide-y divide-gray-100 border border-gray-200 rounded-lg bg-white overflow-auto" style={{ height: PANE_HEIGHT }}>
      {items.map((ev, i) => (
        <li key={i} className="px-3 py-1.5 text-xs flex items-start gap-2">
          <span className="text-gray-400 shrink-0 w-32">{fmtDate(ev.at)}</span>
          <span className="font-medium text-gray-700 shrink-0">{ev.kind}</span>
          {ev.service && <span className="text-indigo-600 shrink-0">{ev.service}</span>}
          <span className="text-gray-500 break-all">{ev.detail}</span>
        </li>
      ))}
    </ul>
  );
}

export default function DeployPanel({ project }) {
  const { t } = useI18n();
  const toast = useToast();
  const projectId = project.id;
  const [dep, setDep] = useState(null);
  const [error, setError] = useState('');
  const [environments, setEnvironments] = useState([]);
  const [busy, setBusy] = useState('');
  const [showConfig, setShowConfig] = useState(false);
  const [previewService, setPreviewService] = useState('');
  const [showKey, setShowKey] = useState(false);

  const load = useCallback(async (refresh = true) => {
    try {
      const { data } = await getProjectDeployment(projectId, refresh);
      setDep(data);
      setError('');
    } catch (err) {
      setError(errorDetail(err) || t('projectDeploy.loadFailed'));
    }
  }, [projectId, t]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    getEnvironments(project.workspace).then(({ data }) => setEnvironments(data.items || data || [])).catch(() => {});
  }, [project.workspace]);

  const active = dep && (dep.status === 'building' || dep.status === 'starting' || dep.in_flight);
  useEffect(() => {
    if (!dep) return undefined;
    if (dep.desired !== 'running' && !active) return undefined;
    const timer = setInterval(() => load(true), active ? POLL_ACTIVE_MS : POLL_IDLE_MS);
    return () => clearInterval(timer);
  }, [dep?.desired, active, load]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (dep && !dep.services?.length) setShowConfig(true);
  }, [dep?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (name, fn, okMessage) => {
    setBusy(name);
    try {
      const { data } = await fn();
      setDep(data);
      if (okMessage) toast.success(okMessage);
    } catch (err) {
      toast.error(t('projectDeploy.actionFailed'), errorDetail(err));
    } finally {
      setBusy('');
    }
  };

  if (error) return <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">{error}</div>;
  if (!dep) return <div className="text-sm text-gray-400 flex items-center gap-2"><Loader className="w-4 h-4 animate-spin" /> {t('projectDeploy.loading')}</div>;

  const running = dep.desired === 'running' && ['running', 'starting', 'unhealthy'].includes(dep.status);
  const links = dep.links || {};
  const externalHref = links.external_url || '';
  const previewTarget = { kind: 'deployment', deployment_id: dep.id, ...(previewService ? { service: previewService } : {}) };
  const previewRt = dep.runtime?.[previewService || dep.primary_service] || {};
  const canPreview = running && ['starting', 'running'].includes(previewRt.state) && previewRt.healthy === true;
  const anyLiveNotAnswering = running && (dep.services || []).some((s) => {
    const rt = dep.runtime?.[s.name];
    return rt && ['starting', 'running'].includes(rt.state) && rt.healthy === false;
  });

  const copy = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(t('projectDeploy.copied'));
    } catch {
      toast.error(t('projectDeploy.copyFailed'));
    }
  };

  const browserHref = `/browser?url=${encodeURIComponent(links.browser_url || '')}&workspace=${encodeURIComponent(project.workspace || '')}`;

  return (
    <div className="space-y-4">
      {/* Header: status + actions */}
      <div className="bg-white rounded-xl border border-gray-200 p-4 flex flex-wrap items-center gap-3">
        <Rocket className="w-5 h-5 text-indigo-500" />
        <DeployStatusPill status={dep.status} t={t} />
        <span className="text-xs text-gray-500">{t(`projectDeploy.modes.${dep.mode}`)} · {(dep.services || []).length} {t('projectDeploy.servicesCount')}</span>
        {dep.paused_reason && <span className="text-xs text-red-600 flex items-center gap-1"><AlertCircle className="w-3.5 h-3.5" /> {dep.paused_reason}</span>}
        {dep.last_error && dep.status === 'failed' && <span className="text-xs text-red-600 break-all">{dep.last_error}</span>}
        {dep.status === 'unhealthy' && <span className="text-xs text-orange-700">{t('projectDeploy.notRespondingHint')}</span>}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <button type="button" disabled={!!busy || active} onClick={() => act('detect', () => detectProjectDeployment(projectId), t('projectDeploy.detected'))}
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-gray-200 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50">
            <Search className="w-3.5 h-3.5" /> {t('projectDeploy.detect')}
          </button>
          <button type="button" onClick={() => setShowConfig((v) => !v)}
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-gray-200 text-xs text-gray-700 hover:bg-gray-50">
            {showConfig ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />} {t('projectDeploy.configure')}
          </button>
          {running ? (
            <>
              <button type="button" disabled={!!busy || active} onClick={() => act('restart', () => restartProjectDeployment(projectId))}
                className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-gray-200 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50">
                <RotateCw className="w-3.5 h-3.5" /> {t('projectDeploy.restart')}
              </button>
              <button type="button" disabled={!!busy || active} onClick={() => act('rebuild', () => deployProject(projectId, true))}
                className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-gray-200 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50">
                <Hammer className="w-3.5 h-3.5" /> {t('projectDeploy.rebuild')}
              </button>
              <button type="button" disabled={!!busy} onClick={() => act('stop', () => stopProjectDeployment(projectId), t('projectDeploy.stoppedToast'))}
                className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-red-600 text-white text-xs font-medium hover:bg-red-700 disabled:opacity-50">
                {busy === 'stop' ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Square className="w-3.5 h-3.5" />} {t('projectDeploy.stop')}
              </button>
            </>
          ) : (
            <button type="button" disabled={!!busy || active || !(dep.services || []).length} onClick={() => act('deploy', () => deployProject(projectId, true))}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-medium hover:bg-indigo-700 disabled:opacity-50">
              {busy === 'deploy' || active ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Rocket className="w-3.5 h-3.5" />} {t('projectDeploy.deploy')}
            </button>
          )}
        </div>
      </div>

      {showConfig && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <ConfigForm dep={dep} environments={environments} t={t} onSaved={(d) => { setDep(d); }} />
        </div>
      )}

      {/* Services */}
      {(dep.services || []).length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-[11px] uppercase tracking-wider text-gray-500">
              <tr>
                <th className="text-left px-3 py-2">{t('projectDeploy.svc.name')}</th>
                <th className="text-left px-3 py-2">{t('projectDeploy.svc.kind')}</th>
                <th className="text-left px-3 py-2">{t('projectDeploy.svc.state')}</th>
                <th className="text-left px-3 py-2">{t('projectDeploy.svc.port')}</th>
                <th className="text-left px-3 py-2">{t('projectDeploy.svc.command')}</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {dep.services.map((svc) => {
                const rt = dep.runtime?.[svc.name] || {};
                const isPrimary = svc.name === dep.primary_service;
                return (
                  <tr key={svc.name}>
                    <td className="px-3 py-2 font-medium text-gray-800">{svc.name}{isPrimary && <span className="ml-1 text-[10px] text-indigo-600">{t('projectDeploy.primaryBadge')}</span>}</td>
                    <td className="px-3 py-2 text-gray-500">{t(`projectDeploy.kind.${svc.kind}`)}</td>
                    <td className="px-3 py-2">
                      <span className="inline-flex items-center gap-1 text-xs">
                        {rt.state === 'running' && rt.healthy ? <CheckCircle className="w-3.5 h-3.5 text-green-500" />
                          : rt.state === 'starting' ? <Clock className="w-3.5 h-3.5 text-amber-500" />
                            : rt.state === 'failed' || rt.state === 'exited' ? <AlertCircle className="w-3.5 h-3.5 text-red-500" />
                              : <span className="w-3.5 h-3.5 inline-block rounded-full bg-gray-300" />}
                        {rt.state || 'stopped'}{rt.exit_code != null ? ` (${rt.exit_code})` : ''}
                      </span>
                      {rt.error && <div className="text-[11px] text-red-600 break-all max-w-md">{rt.error}</div>}
                      {!rt.error && rt.healthy === false && rt.health_error && (
                        <div className="text-[11px] text-orange-700 break-all max-w-md">{rt.health_error}</div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-gray-500 font-mono text-xs">{svc.port}{rt.host_port && rt.host_port !== svc.port ? ` → ${rt.host_port}` : ''}</td>
                    <td className="px-3 py-2 text-gray-500 font-mono text-xs truncate max-w-xs">{svc.dockerfile ? `Dockerfile: ${svc.dockerfile}` : svc.command}</td>
                    <td className="px-3 py-2 text-right">
                      {['starting', 'running'].includes(rt.state) && rt.healthy && (
                        <button type="button" onClick={() => setPreviewService(isPrimary ? '' : svc.name)}
                          className="text-xs text-indigo-600 hover:text-indigo-800">{t('projectDeploy.show')}</button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Links */}
      <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <Link2 className="w-4 h-4 text-gray-400" />
          <span className="text-sm font-medium text-gray-700">{t('projectDeploy.shareLink')}</span>
          <span className="inline-flex items-center gap-1 text-xs text-gray-500">
            {dep.visibility === 'public' ? <Globe className="w-3.5 h-3.5" /> : <Lock className="w-3.5 h-3.5" />}
            {t(`projectDeploy.visibility.${dep.visibility}`)}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <button type="button" onClick={() => act('vis', () => setProjectDeploymentVisibility(projectId, dep.visibility === 'public' ? 'private' : 'public'))}
              className="text-xs px-2.5 py-1 rounded-lg border border-gray-200 hover:bg-gray-50">
              {dep.visibility === 'public' ? t('projectDeploy.makePrivate') : t('projectDeploy.makePublic')}
            </button>
            <button type="button" onClick={() => { if (window.confirm(t('projectDeploy.resetConfirm'))) act('reset', () => resetProjectDeploymentLink(projectId), t('projectDeploy.linkReset')); }}
              className="text-xs px-2.5 py-1 rounded-lg border border-gray-200 hover:bg-gray-50">
              {t('projectDeploy.resetLink')}
            </button>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <code className="flex-1 min-w-0 text-xs bg-gray-50 border border-gray-200 rounded px-2 py-1.5 truncate">
            {showKey || dep.visibility === 'public' ? externalHref : externalHref.replace(/key=.*$/, 'key=••••••')}
          </code>
          {dep.visibility !== 'public' && (
            <button type="button" onClick={() => setShowKey((v) => !v)} className="text-gray-400 hover:text-indigo-600 p-1" title={t('projectDeploy.showKey')}>
              {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          )}
          <button type="button" onClick={() => copy(externalHref.startsWith('http') ? externalHref : `${window.location.origin}${externalHref}`)} className="text-gray-400 hover:text-indigo-600 p-1" title={t('projectDeploy.copy')}>
            <Copy className="w-4 h-4" />
          </button>
          <a href={externalHref} target="_blank" rel="noopener noreferrer" className={`text-gray-400 hover:text-indigo-600 p-1 ${running ? '' : 'pointer-events-none opacity-40'}`} title={t('projectDeploy.openExternal')}>
            <ExternalLink className="w-4 h-4" />
          </a>
          <Link to={browserHref} className={`inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-lg border border-gray-200 hover:bg-gray-50 ${running ? '' : 'pointer-events-none opacity-40'}`}>
            <Globe className="w-3.5 h-3.5" /> {t('projectDeploy.openInAgentBrowser')}
          </Link>
        </div>
        {!links.external_absolute && <p className="text-[11px] text-gray-400">{t('projectDeploy.relativeLinkHint')}</p>}
      </div>

      {/* Preview inside the hub */}
      {canPreview ? (
        <PreviewFrame target={previewTarget} height={600} />
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 text-center py-10 text-gray-400 text-sm">
          {active ? t('projectDeploy.previewWait') : anyLiveNotAnswering ? t('projectDeploy.previewNotResponding') : t('projectDeploy.previewStopped')}
        </div>
      )}

      {/* Logs + events */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="space-y-2">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider flex items-center gap-1"><Terminal className="w-3.5 h-3.5" /> {t('projectDeploy.logs')}</h3>
          <LogsPane projectId={projectId} dep={dep} t={t} />
        </div>
        <div className="space-y-2">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider flex items-center gap-1"><History className="w-3.5 h-3.5" /> {t('projectDeploy.events')}</h3>
          <EventsPane projectId={projectId} refreshKey={dep.updated_at} t={t} />
        </div>
      </div>
    </div>
  );
}
