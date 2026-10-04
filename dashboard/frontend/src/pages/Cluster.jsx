import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle, Box, Cpu, Layers, RefreshCw, Repeat, ScrollText, Server, Trash2, Waypoints, X,
} from 'lucide-react';
import { forgetMember, getCluster, getMemberLogs } from '../api';
import { useChannel } from '../components/stream';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import PageLoader from '../components/PageLoader';

/**
 * Cluster — the map of every process and everything they carry.
 *
 * Health answers "is this process healthy"; this page answers "where is
 * everything, and is each place alive". It is built from one document,
 * GET /api/cluster (docs/deployment.md, "The deployment map"; the backend
 * still answers the same document at /api/deployment): the members (backend
 * replicas and workers, each with its heartbeat, load and the singleton
 * roles it holds), the launch queue, and the runs, flow runs, loops, resident
 * instances and containers grouped by the host they run on.
 *
 * ``entity_runs`` is every flow, loop, team and scenario run, kind-agnostic
 * (common/entity_runs.py): counts by kind, then each active one with its kind,
 * host, heartbeat age and resume attempts. ``flow_runs`` and ``loops`` are the
 * same query's flow and loop slices, kept so this page's older cards need no
 * change.
 */

function agoLabel(t, seconds) {
  if (seconds === null || seconds === undefined) return t('cluster.never');
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return t('cluster.ago', { s });
  return t('cluster.minutesAgo', { m: Math.round(s / 60) });
}

function uptimeLabel(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const s = Math.max(0, Math.round(seconds));
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

const STATUS_STYLE = {
  live: 'bg-green-50 text-green-700 border-green-200',
  stale: 'bg-amber-50 text-amber-700 border-amber-200',
  stopped: 'bg-gray-100 text-gray-600 border-gray-200',
};

function StatusPill({ status }) {
  const { t } = useI18n();
  const label = { live: t('cluster.live'), stale: t('cluster.stale'), stopped: t('cluster.stopped') }[status] || status;
  return (
    <span className={`inline-flex items-center text-[11px] font-semibold px-2 py-0.5 rounded-full border ${STATUS_STYLE[status] || STATUS_STYLE.stopped}`}>
      {label}
    </span>
  );
}

function Card({ icon: Icon, title, hint, children }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
      <h3 className="text-sm font-bold text-gray-800 flex items-center gap-2 mb-1">
        <Icon className="w-4 h-4 text-indigo-500" /> {title}
      </h3>
      {hint && <p className="text-xs text-gray-400 mb-3">{hint}</p>}
      {children}
    </div>
  );
}

function Table({ columns, rows, empty }) {
  if (!rows.length) return <p className="text-sm text-gray-500 py-2">{empty}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-500 border-b border-gray-100">
            {columns.map((c) => <th key={c.key} className="py-1.5 pr-3 font-semibold">{c.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.key || i} className="border-b border-gray-50 last:border-0 align-top">
              {columns.map((c) => (
                <td key={c.key} className="py-1.5 pr-3 text-gray-800">{c.render ? c.render(row) : row[c.key]}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MemberLogModal({ member, onClose }) {
  const { t } = useI18n();
  const [logs, setLogs] = useState('');
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    getMemberLogs(member.member_id, 400)
      .then(({ data }) => { if (!cancelled) setLogs(data || t('cluster.noLogs')); })
      .catch(() => { if (!cancelled) setLogs(t('cluster.noLogs')); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [member.member_id, t]);

  // Live tail, same channel shape as a resident instance's log (common/live_state.py).
  useChannel(member.status === 'live' ? `logs:member:${member.member_id}` : null, (ev) => {
    if (ev.type === 'logs') setLogs(ev.content || t('cluster.noLogs'));
  });

  useEffect(() => { bottomRef.current?.scrollIntoView?.(); }, [logs]);

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-950 rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col border border-gray-800">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
          <div className="flex items-center gap-3">
            <StatusPill status={member.status} />
            <span className="text-gray-200 text-sm font-semibold">{t('cluster.logsOf', { member: member.member_id })}</span>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 transition-colors" title={t('cluster.close')}>
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 overflow-auto p-5">
          {loading ? (
            <PageLoader size="sm" />
          ) : (
            <pre className="text-xs text-green-400 whitespace-pre-wrap break-words leading-5">{logs}</pre>
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  );
}

export default function Cluster() {
  const { t } = useI18n();
  const [map, setMap] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [logMember, setLogMember] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await getCluster();
      setMap(data);
      setError('');
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('cluster.unreachable'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  const forget = useCallback(async (memberId) => {
    try {
      await forgetMember(memberId);
      load();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    }
  }, [load]);

  const members = map?.members || [];
  const queue = map?.queue || {};
  const outbox = map?.outbox || {};
  const hosts = map?.hosts || [];

  const loadText = (m) => Object.entries(m.load || {})
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${typeof v === 'boolean' ? (v ? 'yes' : 'no') : v}`)
    .join(', ');

  return (
    <PageContainer>
      <PageHeader
        icon={Waypoints}
        title={t('cluster.title')}
        description={t('cluster.subtitle')}
        badges={map?.self && (
          <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full border bg-indigo-50 text-indigo-700 border-indigo-200">
            {t('cluster.thisProcess')}: {map.self.member_id} ({map.self.role})
          </span>
        )}
        actions={(
          <button onClick={load}
                  className="inline-flex items-center px-3 py-2 text-xs font-semibold text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">
            <RefreshCw className="w-3.5 h-3.5 mr-1" /> {t('cluster.refresh')}
          </button>
        )}
      />

      {error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {error}
        </div>
      )}

      {loading ? (
        <PageLoader label={t('cluster.loading')} />
      ) : (
        <div className="grid grid-cols-1 gap-4">
          <Card icon={Server} title={t('cluster.members')} hint={t('cluster.membersHint')}>
            <Table
              empty={t('cluster.noMembers')}
              rows={members.map((m) => ({ ...m, key: m.member_id }))}
              columns={[
                { key: 'member_id', label: t('cluster.member'), render: (m) => (
                  <span className="font-mono text-xs">
                    {m.member_id}{m.self && <span className="ml-1 text-indigo-600">({t('cluster.you')})</span>}
                  </span>
                ) },
                { key: 'role', label: t('cluster.role') },
                { key: 'host', label: t('cluster.host'), render: (m) => <span className="font-mono text-xs">{m.host || t('cluster.unknownHost')}</span> },
                { key: 'status', label: t('cluster.status'), render: (m) => <StatusPill status={m.status} /> },
                { key: 'beat', label: t('cluster.beat'), render: (m) => agoLabel(t, m.heartbeat_age_seconds) },
                { key: 'uptime', label: t('cluster.uptime'), render: (m) => uptimeLabel(m.uptime_seconds) },
                { key: 'leases', label: t('cluster.leases'), render: (m) => (m.leases || []).join(', ') || '—' },
                { key: 'load', label: t('cluster.load'), render: (m) => <span className="text-xs text-gray-600">{loadText(m) || '—'}</span> },
                { key: 'version', label: t('cluster.version'), render: (m) => <span className="font-mono text-xs">{m.version || '—'}</span> },
                { key: 'actions', label: '', render: (m) => (
                  <span className="inline-flex gap-1">
                    <button onClick={() => setLogMember(m)} title={t('cluster.viewLogs')}
                            className="p-1 rounded hover:bg-gray-100 text-gray-600">
                      <ScrollText className="w-4 h-4" />
                    </button>
                    <button onClick={() => forget(m.member_id)} disabled={m.status === 'live'}
                            title={t('cluster.forgetHint')}
                            className="p-1 rounded hover:bg-gray-100 text-gray-600 disabled:opacity-30 disabled:cursor-not-allowed">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </span>
                ) },
              ]}
            />
          </Card>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card icon={Repeat} title={t('cluster.queue')}>
              <div className="flex flex-wrap gap-4 text-sm mb-3">
                {['queued', 'leased', 'running', 'failed'].map((k) => (
                  <span key={k}><span className="text-gray-500">{t(`cluster.${k}`)}:</span> <b>{queue[k] ?? 0}</b></span>
                ))}
                <span><span className="text-gray-500">{t('cluster.oldestQueued')}:</span> <b>{Math.round(queue.oldest_queued_seconds || 0)}s</b></span>
                <span><span className="text-gray-500">{t('cluster.outboxPending')}:</span> <b>{outbox.pending ?? 0}</b>
                  {outbox.dead ? <span className="text-red-600"> ({outbox.dead} {t('cluster.outboxDead')})</span> : null}</span>
              </div>
              <Table
                empty={t('cluster.noQueue')}
                rows={(queue.items || []).map((q) => ({ ...q, key: q.run_id }))}
                columns={[
                  { key: 'run_id', label: 'run', render: (q) => <span className="font-mono text-xs">{String(q.run_id).slice(0, 8)}</span> },
                  { key: 'kind', label: t('cluster.queueKind') },
                  { key: 'status', label: t('cluster.status') },
                  { key: 'workspace', label: t('cluster.workspace') },
                  { key: 'lease_owner', label: t('cluster.queueWorker'), render: (q) => <span className="font-mono text-xs">{q.lease_owner || '—'}</span> },
                  { key: 'attempts', label: t('cluster.queueAttempts') },
                ]}
              />
            </Card>

            <Card icon={Box} title={t('cluster.hosts')} hint={t('cluster.hostsHint')}>
              <Table
                empty={t('cluster.noHosts')}
                rows={hosts.map((h) => ({ ...h, key: h.host }))}
                columns={[
                  { key: 'host', label: t('cluster.host'), render: (h) => <span className="font-mono text-xs">{h.host}</span> },
                  { key: 'members', label: t('cluster.members'), render: (h) => h.members.length },
                  { key: 'runs', label: t('cluster.runs') },
                  { key: 'flow_runs', label: t('cluster.flowRuns') },
                  { key: 'instances', label: t('cluster.instances'), render: (h) => h.instances ?? h.nodes ?? 0 },
                  { key: 'containers', label: t('cluster.containers') },
                ]}
              />
            </Card>
          </div>

          <Card icon={Repeat} title={t('cluster.activeRuns')}>
            <Table
              empty={t('cluster.noRuns')}
              rows={(map?.runs || []).map((r) => ({ ...r, key: r.run_id }))}
              columns={[
                { key: 'run_id', label: 'run', render: (r) => <span className="font-mono text-xs">{String(r.run_id).slice(0, 8)}</span> },
                { key: 'agent_id', label: t('cluster.agent') },
                { key: 'workspace', label: t('cluster.workspace') },
                { key: 'status', label: t('cluster.status') },
                { key: 'host', label: t('cluster.host'), render: (r) => <span className="font-mono text-xs">{r.host || t('cluster.unknownHost')}</span> },
                { key: 'heartbeat', label: t('cluster.heartbeat'), render: (r) => agoLabel(t, r.heartbeat_age_seconds) },
                { key: 'checkpoint', label: t('cluster.checkpoint'), render: (r) => (
                  <span className="text-xs text-gray-600">
                    {r.checkpoint_step ? t('cluster.step', { n: r.checkpoint_step }) : '—'}
                    {r.resume_attempts ? `, ${t('cluster.resumed', { n: r.resume_attempts })}` : ''}
                  </span>
                ) },
              ]}
            />
          </Card>

          {map?.entity_runs && (
            <Card
              icon={Layers}
              title={t('cluster.entityRuns', { defaultValue: 'Flow / loop / team / scenario runs' })}
              hint={t('cluster.entityRunsHint', {
                defaultValue: 'Every kind that shares the common/entity_runs.py table, in one place.',
              })}
            >
              <div className="flex flex-wrap gap-4 text-sm mb-3">
                {Object.entries(map.entity_runs.counts_by_kind || {}).map(([kind, n]) => (
                  <span key={kind}><span className="text-gray-500 capitalize">{kind}:</span> <b>{n}</b></span>
                ))}
              </div>
              <Table
                empty={t('cluster.noEntityRuns', { defaultValue: 'No active flow, loop, team or scenario runs.' })}
                rows={(map.entity_runs.active || []).map((r) => ({ ...r, key: r.run_id }))}
                columns={[
                  { key: 'run_id', label: 'run', render: (r) => <span className="font-mono text-xs">{String(r.run_id).slice(0, 8)}</span> },
                  { key: 'kind', label: t('cluster.queueKind') },
                  { key: 'workspace', label: t('cluster.workspace') },
                  { key: 'status', label: t('cluster.status') },
                  { key: 'host', label: t('cluster.host'), render: (r) => <span className="font-mono text-xs">{r.host || t('cluster.unknownHost')}</span> },
                  { key: 'heartbeat', label: t('cluster.heartbeat'), render: (r) => agoLabel(t, r.heartbeat_age_seconds) },
                  { key: 'resume_attempts', label: t('cluster.resumeAttempts', { defaultValue: 'resumes' }), render: (r) => r.resume_attempts || 0 },
                ]}
              />
            </Card>
          )}

          {(map?.loops || []).length > 0 && (
            <Card icon={Repeat} title={t('cluster.activeLoops')}>
              <Table
                empty=""
                rows={map.loops.map((l) => ({ ...l, key: l.loop_run_id }))}
                columns={[
                  { key: 'loop_id', label: t('cluster.loop') },
                  { key: 'workspace', label: t('cluster.workspace') },
                  { key: 'iterations_done', label: t('cluster.iteration') },
                  { key: 'owner', label: t('cluster.owner'), render: (l) => <span className="font-mono text-xs">{l.owner || '—'}</span> },
                  { key: 'heartbeat', label: t('cluster.heartbeat'), render: (l) => agoLabel(t, l.heartbeat_age_seconds) },
                ]}
              />
            </Card>
          )}

          {(map?.services || []).length > 0 && (
            <Card icon={Cpu} title={t('cluster.services')} hint={t('cluster.servicesHint')}>
              <Table
                empty="—"
                rows={map.services.map((s) => ({ ...s, key: s.service_id }))}
                columns={[
                  { key: 'name', label: t('cluster.service'), render: (s) => (
                    <Link to={`/services/${s.service_id}`} className="text-indigo-600 hover:underline">{s.name}</Link>
                  ) },
                  { key: 'agent_id', label: t('cluster.agent'), render: (s) => s.agent_id || t('cluster.runner') },
                  { key: 'workspace', label: t('cluster.workspace') },
                  { key: 'status', label: t('cluster.status') },
                  { key: 'replicas', label: t('cluster.replicas'), render: (s) => (
                    `${s.replicas?.live ?? 0} / ${s.replicas_min}–${s.replicas_max}`
                  ) },
                ]}
              />
            </Card>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card icon={Server} title={t('cluster.activeInstances')}>
              <Table
                empty="—"
                rows={(map?.instances || map?.nodes || []).map((n) => ({ ...n, key: n.instance_id || n.node_id }))}
                columns={[
                  { key: 'label', label: t('cluster.agent'), render: (n) => (
                    <Link to={`/instances/${n.instance_id || n.node_id}`} className="text-indigo-600 hover:underline">
                      {n.label || n.agent_id}
                    </Link>
                  ) },
                  { key: 'workspace', label: t('cluster.workspace') },
                  { key: 'state', label: t('cluster.status'), render: (n) => n.state || n.status },
                  { key: 'carrier_mode', label: t('cluster.carrierMode'), render: (n) => n.carrier_mode || '—' },
                  { key: 'host', label: t('cluster.host'), render: (n) => <span className="font-mono text-xs">{n.host || t('cluster.unknownHost')}</span> },
                ]}
              />
            </Card>
            <Card icon={Box} title={t('cluster.activeContainers')}>
              <Table
                empty="—"
                rows={(map?.containers || []).map((c) => ({ ...c, key: c.name }))}
                columns={[
                  { key: 'name', label: 'name', render: (c) => <span className="font-mono text-xs">{c.name}</span> },
                  { key: 'agent_id', label: t('cluster.agent') },
                  { key: 'state', label: t('cluster.status'), render: (c) => c.state || c.status },
                  { key: 'host', label: t('cluster.host'), render: (c) => (
                    <span className="font-mono text-xs">{c.host || t('cluster.unknownHost')}
                      {c.remote && <span className="ml-1 text-amber-700">({t('cluster.remote')})</span>}</span>
                  ) },
                ]}
              />
            </Card>
          </div>
        </div>
      )}

      {logMember && <MemberLogModal member={logMember} onClose={() => setLogMember(null)} />}
    </PageContainer>
  );
}
