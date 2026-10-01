import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  Activity, Box, Cpu, History, Loader, Pause, Play, Plus, Save, Settings2, Trash2, Info } from 'lucide-react';

import {
  addServiceReplica, clearServiceInboundSecret, deleteService, getService, getServiceConnections,
  getServiceEvents, pauseService, publishService, resumeService, setServiceInboundSecret,
  unpublishService, updateService,
} from '../api';
import InstanceList from '../components/InstanceList';
import AccessTab from '../components/instances/AccessTab';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useLiveRefetch } from '../components/stream';
import { useWorkspace } from '../components/workspace';
import { ReplicasCell, StatusPill } from './Services';
import { useI18n } from '../i18n';
import PageLoader from '../components/PageLoader';

/*
 * One service: its desired state, the replicas realising it, its public
 * address and the supervisor's journal. The replicas are ordinary resident
 * instances: each opens on its own page, where its conversations and process
 * live.
 */

const TABS = [
  { id: 'replicas', icon: Activity },
  { id: 'settings', icon: Settings2 },
  { id: 'access', icon: Box },
  { id: 'events', icon: History },
];

function Stat({ label, value }) {
  return (
    <div className="px-3 py-2 rounded-lg bg-gray-50 border border-gray-100">
      <div className="text-[11px] uppercase tracking-wide text-gray-400">{label}</div>
      <div className="text-sm font-medium text-gray-800 mt-0.5">{value}</div>
    </div>
  );
}

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function SettingsForm({ service, onSaved, t }) {
  const [form, setForm] = useState({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setForm({
      name: service.name || '',
      replicas_min: String(service.replicas_min ?? 0),
      replicas_max: String(service.replicas_max ?? 1),
      concurrency: String(service.concurrency ?? 4),
      idle_stop_minutes: String(Math.round((service.idle_stop_seconds || 0) / 60)),
      take_tasks: !!service.take_tasks,
      budget_usd: service.budget_usd != null ? String(service.budget_usd) : '',
      agent_version: service.agent_version != null ? String(service.agent_version) : '',
    });
  }, [service]);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const field = 'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500';
  const label = 'block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5';

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError('');
    setSaved(false);
    const min = Math.max(0, parseInt(form.replicas_min, 10) || 0);
    try {
      const { data } = await updateService(service.service_id, {
        name: form.name.trim() || undefined,
        replicas_min: min,
        replicas_max: Math.max(min, parseInt(form.replicas_max, 10) || 0),
        concurrency: Math.max(1, parseInt(form.concurrency, 10) || 1),
        idle_stop_seconds: Math.max(0, Math.round((parseFloat(form.idle_stop_minutes) || 0) * 60)),
        take_tasks: !!form.take_tasks,
        ...(form.budget_usd.trim() ? { budget_usd: parseFloat(form.budget_usd) } : { clear_budget: true }),
        ...(form.agent_version.trim() ? { agent_version: parseInt(form.agent_version, 10) } : { clear_agent_version: true }),
      });
      onSaved(data);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setError(err.response?.data?.detail || t('services.settings.failed'));
    } finally {
      setSaving(false);
    }
  };

  const isRunner = service.kind === 'runner';
  return (
    <form onSubmit={submit} className="bg-white border border-gray-200 rounded-xl p-5 space-y-4 max-w-2xl">
      {error && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">{error}</div>}
      <div>
        <label className={label}>{t('services.settings.name')}</label>
        <input className={field} value={form.name || ''} onChange={(e) => set('name', e.target.value)} />
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className={label}>{t('services.deploy.replicasMin')}</label>
          <input type="number" min="0" max="64" className={field} value={form.replicas_min || ''} onChange={(e) => set('replicas_min', e.target.value)} />
        </div>
        <div>
          <label className={label}>{t('services.deploy.replicasMax')}</label>
          <input type="number" min="0" max="64" className={field} value={form.replicas_max || ''} onChange={(e) => set('replicas_max', e.target.value)} />
        </div>
        <div>
          <label className={label}>{t('services.deploy.concurrency')}</label>
          <input type="number" min="1" max="32" className={field} value={form.concurrency || ''} onChange={(e) => set('concurrency', e.target.value)} />
        </div>
      </div>
      <p className="text-xs text-gray-500 -mt-2">{t('services.deploy.replicasHint')}</p>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={label}>{t('services.deploy.idleStop')}</label>
          <input type="number" min="0" className={field} value={form.idle_stop_minutes || ''} onChange={(e) => set('idle_stop_minutes', e.target.value)} />
          <p className="text-xs text-gray-500 mt-1">{t('services.deploy.idleStopHint')}</p>
        </div>
        <div>
          <label className={label}>{t('services.deploy.budget')}</label>
          <input type="number" min="0" step="0.01" className={field} value={form.budget_usd || ''} placeholder="—" onChange={(e) => set('budget_usd', e.target.value)} />
          <p className="text-xs text-gray-500 mt-1">{t('services.deploy.budgetHint')}</p>
        </div>
      </div>
      {!isRunner && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={label}>{t('services.deploy.version')}</label>
            <input type="number" min="1" className={field} value={form.agent_version || ''} placeholder={t('services.deploy.versionLatest')} onChange={(e) => set('agent_version', e.target.value)} />
          </div>
          <label className="flex items-start gap-3 cursor-pointer select-none pt-6">
            <input type="checkbox" className="mt-0.5" checked={!!form.take_tasks} onChange={(e) => set('take_tasks', e.target.checked)} />
            <span>
              <span className="block text-sm font-medium text-gray-800">{t('services.deploy.takeTasks')}</span>
              <span className="block text-xs text-gray-500 mt-0.5">{t('services.deploy.takeTasksHint')}</span>
            </span>
          </label>
        </div>
      )}
      <div className="flex items-center justify-end gap-3 pt-2">
        {saved && <span className="text-xs text-green-600">{t('services.settings.saved')}</span>}
        <button type="submit" disabled={saving}
                className="inline-flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
          {saving ? <Loader className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {t('services.settings.save')}
        </button>
      </div>
    </form>
  );
}

export default function ServiceDetail() {
  const { serviceId } = useParams();
  const { t } = useI18n();
  const navigate = useNavigate();
  const { liveUpdates } = useWorkspace();
  const [service, setService] = useState(null);
  const [events, setEvents] = useState([]);
  const [activeTab, setActiveTab] = useState('replicas');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const { data } = await getService(serviceId);
      setService(data);
      setError('');
    } catch {
      setService(null);
    } finally {
      setLoading(false);
    }
  }, [serviceId]);

  useEffect(() => { load(); }, [load]);
  useLiveRefetch(load, { type: 'services.changed', enabled: liveUpdates });
  useLiveRefetch(load, { type: 'instances.changed', enabled: liveUpdates });

  useEffect(() => {
    if (activeTab !== 'events') return;
    getServiceEvents(serviceId, { limit: 200 }).then((r) => setEvents(r.data.items || [])).catch(() => setEvents([]));
  }, [activeTab, serviceId, service]);

  const act = async (name, fn) => {
    setBusy(name);
    setError('');
    try {
      await fn(serviceId);
      await load();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusy('');
    }
  };

  const isDefaultRunner = service?.kind === 'runner' && !!service?.is_default;
  const pause = () => {
    if (isDefaultRunner && !window.confirm(t('services.pauseDefaultConfirm', { name: service?.name }))) return;
    act('pause', pauseService);
  };
  const remove = async () => {
    const key = isDefaultRunner ? 'services.deleteDefaultConfirm' : 'services.deleteConfirm';
    if (!window.confirm(t(key, { name: service?.name }))) return;
    try {
      await deleteService(serviceId);
      navigate('/services');
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    }
  };

  if (loading) {
    return <PageContainer><PageLoader size="lg" label={t('services.loading')} /></PageContainer>;
  }
  if (!service) {
    return (
      <PageContainer>
        <div className="p-8 text-center">
          <div className="text-sm text-gray-500">{t('services.notFound')}</div>
          <Link to="/services" className="text-sm text-indigo-600 hover:underline mt-2 inline-block">{t('services.backToList')}</Link>
        </div>
      </PageContainer>
    );
  }

  const rep = service.replicas || {};
  const isActive = service.status === 'active';
  const accessActions = {
    publish: publishService,
    unpublish: unpublishService,
    setSecret: setServiceInboundSecret,
    clearSecret: clearServiceInboundSecret,
    connections: getServiceConnections,
  };

  return (
    <PageContainer>
      <PageHeader
        icon={Cpu}
        backTo="/services"
        title={service.name}
        description={service.kind === 'runner'
          ? t('services.detail.runnerDescription', { workspace: service.workspace })
          : t('services.detail.description', { agent: service.agent_id, workspace: service.workspace })}
        actions={(
          <div className="flex items-center gap-2">
            <StatusPill status={service.status} reason={service.paused_reason} t={t} />
            {isActive ? (
              <>
                <button type="button" onClick={() => act('replica', addServiceReplica)}
                        disabled={!!busy || (rep.live || 0) >= service.replicas_max}
                        title={t('services.actions.addReplicaHint')}
                        className="px-3 py-1.5 text-sm rounded-lg border border-indigo-200 text-indigo-600 hover:bg-indigo-50 inline-flex items-center gap-1.5 disabled:opacity-50">
                  <Plus className="w-3.5 h-3.5" />{t('services.actions.addReplica')}
                </button>
                <button type="button" onClick={pause} disabled={!!busy}
                        className="px-3 py-1.5 text-sm rounded-lg border border-amber-200 text-amber-700 hover:bg-amber-50 inline-flex items-center gap-1.5 disabled:opacity-50">
                  <Pause className="w-3.5 h-3.5" />{t('services.actions.pause')}
                </button>
              </>
            ) : (
              <button type="button" onClick={() => act('resume', resumeService)} disabled={!!busy}
                      className="px-3 py-1.5 text-sm rounded-lg border border-green-200 text-green-700 hover:bg-green-50 inline-flex items-center gap-1.5 disabled:opacity-50">
                <Play className="w-3.5 h-3.5" />{t('services.actions.resume')}
              </button>
            )}
            <button type="button" onClick={remove}
                    className="p-1.5 rounded-lg border border-red-200 text-red-600 hover:bg-red-50"
                    title={t('services.actions.delete')}>
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      />

      {error && <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>}

      {/* A runner is where every chat turn lands unless the agent has a service
          of its own, and that is a setting of the hub, not of this service. */}
      {service.kind === 'runner' && (
        <div className="mb-4 rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-2 text-xs text-indigo-800 flex items-start gap-2">
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>
            {t('services.detail.runnerExecutionHint')}{' '}
            <Link to="/settings/execution" className="font-medium underline hover:text-indigo-900">
              {t('services.detail.runnerExecutionLink')}
            </Link>
          </span>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-6 gap-2 mb-4">
        <Stat label={t('services.columns.agent')} value={service.agent_id
          ? <Link to={`/agents/${service.agent_id}`} className="hover:text-indigo-600">{service.agent_id}</Link>
          : t('services.anyAgent')} />
        <Stat label={t('services.columns.workspace')} value={service.workspace} />
        <Stat label={t('services.columns.environment')} value={service.environment_name || '—'} />
        <Stat label={t('services.columns.replicas')} value={<ReplicasCell service={service} t={t} />} />
        <Stat label={t('services.columns.concurrency')} value={service.concurrency} />
        <Stat label={t('services.deploy.idleStop')} value={service.idle_stop_seconds ? `${Math.round(service.idle_stop_seconds / 60)} min` : t('services.detail.never')} />
      </div>

      <div className="flex items-center gap-1 border-b border-gray-200">
        {TABS.map(({ id, icon: Icon }) => (
          <button key={id} type="button" onClick={() => setActiveTab(id)}
                  className={`inline-flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 -mb-px ${
                    activeTab === id ? 'border-indigo-600 text-indigo-700 font-medium' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            <Icon className="w-3.5 h-3.5" />{t(`services.tabs.${id}`)}
          </button>
        ))}
      </div>

      {activeTab === 'replicas' && (
        <InstanceList serviceId={serviceId} liveUpdates={liveUpdates} showFilters={false}
                      showAgentColumn={false} />
      )}
      {activeTab === 'settings' && <SettingsForm service={service} onSaved={setService} t={t} />}
      {activeTab === 'access' && (
        <AccessTab
          instance={{ ...service, instance_id: service.service_id }}
          actions={accessActions}
          onInstanceUpdated={setService}
          exampleBody={service.kind === 'runner'
            ? '{"agent_id": "main-agent", "message": "Hello", "conversation_id": "main"}'
            : null}
          hint={service.kind === 'runner' ? t('services.detail.runnerAddressHint') : null}
        />
      )}
      {activeTab === 'events' && (
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          {!events.length ? (
            <div className="p-6 text-sm text-gray-400 text-center">{t('services.events.empty')}</div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="text-[11px] uppercase tracking-wide text-gray-400 border-b border-gray-200">
                  <th className="px-3 py-2 text-left font-medium">{t('services.events.when')}</th>
                  <th className="px-3 py-2 text-left font-medium">{t('services.events.kind')}</th>
                  <th className="px-3 py-2 text-left font-medium">{t('services.events.detail')}</th>
                  <th className="px-3 py-2 text-left font-medium">{t('services.events.replica')}</th>
                </tr>
              </thead>
              <tbody>
                {events.map((ev) => (
                  <tr key={ev.event_id} className="border-b border-gray-100">
                    <td className="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">{fmtDate(ev.at)}</td>
                    <td className="px-3 py-2 text-xs font-mono text-gray-700">{ev.kind}</td>
                    <td className="px-3 py-2 text-xs text-gray-700">{ev.detail}</td>
                    <td className="px-3 py-2 text-xs">
                      {ev.instance_id ? (
                        <Link to={`/instances/${ev.instance_id}`} className="text-indigo-600 hover:underline font-mono">
                          {String(ev.instance_id).slice(0, 12)}
                        </Link>
                      ) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </PageContainer>
  );
}
