import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Cpu, Globe, Pause, Play, RefreshCw, Rocket, Trash2, AlertTriangle } from 'lucide-react';

import { deleteService, getServices, pauseService, resumeService } from '../api';
import DeployServiceModal from '../components/services/DeployServiceModal';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useLiveRefetch } from '../components/stream';
import { getChatRoute } from '../api';
import { useWorkspace } from '../components/workspace';
import { useI18n } from '../i18n';
import PageLoader from '../components/PageLoader';

/*
 * Services: agents kept running as replicas (docs/services.md).
 *
 * The Instances page shows copies that exist right now; this page shows the
 * desired state behind them: which agent, how many replicas at least and at
 * most, and whether the supervisor is keeping them up. The runner of each
 * workspace, the service every chat turn goes to when the agent has none of
 * its own, is listed here too.
 */

export function StatusPill({ status, reason, t }) {
  const active = status === 'active';
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${
        active ? 'bg-green-100 text-green-800' : 'bg-amber-100 text-amber-800'}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${active ? 'bg-green-500' : 'bg-amber-500'}`} />
        {t(`services.status.${status}`, { defaultValue: status })}
      </span>
      {!active && reason && <span className="text-[11px] text-amber-600 truncate max-w-[220px]" title={reason}>{reason}</span>}
    </span>
  );
}

export function ReplicasCell({ service, t }) {
  const rep = service.replicas || {};
  return (
    <span className="text-xs text-gray-700" title={t('services.replicasTitle', {
      live: rep.live || 0, active: rep.active || 0, standby: rep.standby || 0 })}>
      <span className={`font-semibold ${rep.live ? 'text-green-700' : 'text-gray-500'}`}>{rep.live || 0}</span>
      <span className="text-gray-400"> / {service.replicas_min}–{service.replicas_max}</span>
    </span>
  );
}

export default function Services() {
  const { t } = useI18n();
  const { workspaceFilter, selectedWorkspace, liveUpdates } = useWorkspace();
  const navigate = useNavigate();
  // Whether chat runs on replicas at all: only then is a paused service a
  // chat that will not answer.
  const [chatOnReplicas, setChatOnReplicas] = useState(true);
  useEffect(() => {
    getChatRoute({ workspace: workspaceFilter || undefined })
      .then((r) => setChatOnReplicas((r.data?.mode || 'instances') === 'instances'))
      .catch(() => {});
  }, [workspaceFilter]);
  const [items, setItems] = useState([]);
  // Paused services a chat depends on, for the banner above the list.
  const pausedForChat = chatOnReplicas ? items.filter((s) => s.status !== 'active') : [];
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState({});
  const [showDeploy, setShowDeploy] = useState(false);
  const [error, setError] = useState('');

  // A promise chain rather than an async body, so the effect below may call it
  // without a synchronous setState.
  const load = useCallback(() => getServices({ workspace: workspaceFilter || undefined })
    .then(({ data }) => {
      setItems(data.items || []);
      setError('');
    })
    .catch((e) => setError(e.response?.data?.detail || e.message))
    .finally(() => setLoading(false)), [workspaceFilter]);

  useEffect(() => { load(); }, [load]);
  useLiveRefetch(load, { type: 'services.changed', enabled: liveUpdates });
  useLiveRefetch(load, { type: 'instances.changed', enabled: liveUpdates });

  const act = async (service, fn) => {
    setBusy((b) => ({ ...b, [service.service_id]: true }));
    try {
      await fn(service.service_id);
      await load();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusy((b) => { const n = { ...b }; delete n[service.service_id]; return n; });
    }
  };

  // The workspace's own runner answers every chat turn of an agent without a
  // service of its own: pausing or deleting it is asked about twice over.
  const isDefaultRunner = (service) => service.kind === 'runner' && !!service.is_default;
  const pause = (service) => {
    if (isDefaultRunner(service) && !window.confirm(t('services.pauseDefaultConfirm', { name: service.name }))) return;
    act(service, pauseService);
  };
  const remove = (service) => {
    const key = isDefaultRunner(service) ? 'services.deleteDefaultConfirm' : 'services.deleteConfirm';
    if (!window.confirm(t(key, { name: service.name }))) return;
    act(service, deleteService);
  };

  return (
    <PageContainer>
      <PageHeader
        icon={Cpu}
        title={t('services.title')}
        description={selectedWorkspace && selectedWorkspace !== 'default'
          ? t('services.descriptionInWorkspace', { workspace: selectedWorkspace })
          : t('services.description')}
        actions={(
          <div className="flex items-center gap-2">
            <button type="button" onClick={load}
                    className="p-2 rounded-lg border border-gray-200 bg-white text-gray-500 hover:bg-gray-50"
                    title={t('services.refresh')}>
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button type="button" onClick={() => setShowDeploy(true)}
                    className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700">
              <Rocket className="w-4 h-4" />
              {t('services.deployButton')}
            </button>
          </div>
        )}
      />

      {error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>
      )}

      {pausedForChat.length > 0 && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800" data-testid="services-chat-down">
          <div className="flex items-center gap-2 font-medium">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            {t('services.chatDownTitle')}
          </div>
          <ul className="mt-1.5 ml-6 list-disc space-y-0.5 text-xs">
            {pausedForChat.map((s) => (
              <li key={s.service_id}>
                <Link to={`/services/${s.service_id}`} className="font-medium underline hover:text-amber-900">{s.name}</Link>
                {': '}
                {s.kind === 'runner' ? t('services.chatDownRunner') : t('services.chatDownAgent', { agent: s.agent_id })}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        {loading && !items.length ? (
          <PageLoader label={t('services.loading')} />
        ) : !items.length ? (
          <div className="p-8 text-center">
            <Cpu className="w-8 h-8 text-gray-300 mx-auto mb-2" />
            <div className="text-sm text-gray-500">{t('services.empty.title')}</div>
            <div className="text-xs text-gray-400 mt-1">{t('services.empty.hint')}</div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="text-[11px] uppercase tracking-wide text-gray-400 border-b border-gray-200">
                  <th className="px-3 py-2 text-left font-medium">{t('services.columns.service')}</th>
                  <th className="px-3 py-2 text-left font-medium">{t('services.columns.agent')}</th>
                  <th className="px-3 py-2 text-left font-medium">{t('services.columns.workspace')}</th>
                  <th className="px-3 py-2 text-left font-medium">{t('services.columns.environment')}</th>
                  <th className="px-3 py-2 text-left font-medium">{t('services.columns.status')}</th>
                  <th className="px-3 py-2 text-left font-medium">{t('services.columns.replicas')}</th>
                  <th className="px-3 py-2 text-right font-medium">{t('services.columns.concurrency')}</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {items.map((s) => (
                  <tr
                    key={s.service_id}
                    role="link"
                    tabIndex={0}
                    aria-label={s.name}
                    onClick={() => navigate(`/services/${s.service_id}`)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) navigate(`/services/${s.service_id}`); }}
                    className="border-b border-gray-100 hover:bg-gray-50 cursor-pointer focus:outline-none focus-visible:bg-indigo-50"
                  >
                    <td className="px-3 py-2">
                      <Link to={`/services/${s.service_id}`} onClick={(e) => e.stopPropagation()}
                            className="text-sm font-medium text-gray-900 hover:text-indigo-600 inline-flex items-center gap-1.5">
                        {s.name}
                        {s.is_exposed && <Globe className="w-3 h-3 text-emerald-600" title={t('services.published')} />}
                      </Link>
                      {s.kind === 'runner' && (
                        <div className="text-[11px] text-gray-400">{t('services.runnerLabel')}</div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {s.agent_id ? (
                        <Link to={`/agents/${s.agent_id}`} onClick={(e) => e.stopPropagation()} className="text-gray-700 hover:text-indigo-600">{s.agent_id}</Link>
                      ) : <span className="text-gray-400">{t('services.anyAgent')}</span>}
                    </td>
                    <td className="px-3 py-2 text-xs text-gray-600">{s.workspace}</td>
                    <td className="px-3 py-2 text-xs text-gray-600">{s.environment_name || <span className="text-gray-300">—</span>}</td>
                    <td className="px-3 py-2"><StatusPill status={s.status} reason={s.paused_reason} t={t} /></td>
                    <td className="px-3 py-2"><ReplicasCell service={s} t={t} /></td>
                    <td className="px-3 py-2 text-xs text-gray-600 text-right">{s.concurrency}</td>
                    {/* The buttons act on the row without opening it. */}
                    <td className="px-3 py-2 whitespace-nowrap text-right" onClick={(e) => e.stopPropagation()}>
                      <div className="inline-flex items-center gap-1">
                        {s.status === 'active' ? (
                          <button type="button" onClick={() => pause(s)} disabled={!!busy[s.service_id]}
                                  title={t('services.actions.pause')}
                                  className="p-1.5 rounded hover:bg-amber-50 text-amber-600 disabled:opacity-40">
                            <Pause className="w-3.5 h-3.5" />
                          </button>
                        ) : (
                          <button type="button" onClick={() => act(s, resumeService)} disabled={!!busy[s.service_id]}
                                  title={t('services.actions.resume')}
                                  className="p-1.5 rounded hover:bg-green-50 text-green-600 disabled:opacity-40">
                            <Play className="w-3.5 h-3.5" />
                          </button>
                        )}
                        <button type="button" onClick={() => remove(s)} disabled={!!busy[s.service_id]}
                                title={t('services.actions.delete')}
                                className="p-1.5 rounded hover:bg-red-50 text-red-600 disabled:opacity-40">
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <DeployServiceModal open={showDeploy} onClose={() => setShowDeploy(false)} defaultWorkspace={selectedWorkspace} />
    </PageContainer>
  );
}
