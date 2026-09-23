import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, Box, Loader, RefreshCw, Repeat, ScrollText, Server, Trash2, Waypoints, X,
} from 'lucide-react';
import { forgetMember, getDeployment, getMemberLogs } from '../api';
import { useChannel } from '../components/stream';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';

/**
 * Deployment — the map of every process and everything they carry.
 *
 * Health answers "is this process healthy"; this page answers "where is
 * everything, and is each place alive". It is built from one document,
 * GET /api/deployment (docs/deployment.md, "The deployment map"): the members
 * (backend replicas and workers, each with its heartbeat, load and the
 * singleton roles it holds), the launch queue, and the runs, flow runs, loops,
 * nodes and containers grouped by host.
 */

function agoLabel(t, seconds) {
  if (seconds === null || seconds === undefined) return t('deployment.never');
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return t('deployment.ago', { s });
  return t('deployment.minutesAgo', { m: Math.round(s / 60) });
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
  const label = { live: t('deployment.live'), stale: t('deployment.stale'), stopped: t('deployment.stopped') }[status] || status;
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
      .then(({ data }) => { if (!cancelled) setLogs(data || t('deployment.noLogs')); })
      .catch(() => { if (!cancelled) setLogs(t('deployment.noLogs')); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [member.member_id, t]);

  // Live tail, same channel shape as a node's log (common/live_state.py).
  useChannel(member.status === 'live' ? `logs:member:${member.member_id}` : null, (ev) => {
    if (ev.type === 'logs') setLogs(ev.content || t('deployment.noLogs'));
  });

  useEffect(() => { bottomRef.current?.scrollIntoView?.(); }, [logs]);

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-950 rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col border border-gray-800">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
          <div className="flex items-center gap-3">
            <StatusPill status={member.status} />
            <span className="text-gray-200 text-sm font-semibold">{t('deployment.logsOf', { member: member.member_id })}</span>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 transition-colors" title={t('deployment.close')}>
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 overflow-auto p-5">
          {loading ? (
            <div className="flex justify-center py-12"><Loader className="w-5 h-5 animate-spin text-indigo-400" /></div>
          ) : (
            <pre className="text-xs text-green-400 whitespace-pre-wrap break-words leading-5">{logs}</pre>
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  );
}

export default function Deployment() {
  const { t } = useI18n();
  const [map, setMap] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [logMember, setLogMember] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await getDeployment();
      setMap(data);
      setError('');
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('deployment.unreachable'));
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
        title={t('deployment.title')}
        description={t('deployment.subtitle')}
        badges={map?.self && (
          <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full border bg-indigo-50 text-indigo-700 border-indigo-200">
            {t('deployment.thisProcess')}: {map.self.member_id} ({map.self.role})
          </span>
        )}
        actions={(
          <button onClick={load}
                  className="inline-flex items-center px-3 py-2 text-xs font-semibold text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">
            <RefreshCw className="w-3.5 h-3.5 mr-1" /> {t('deployment.refresh')}
          </button>
        )}
      />

      {error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {error}
        </div>
      )}

      {loading ? (
        <div className="p-6 text-sm text-gray-500 flex items-center gap-2">
          <Loader className="w-4 h-4 animate-spin" /> {t('deployment.loading')}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4">
          <Card icon={Server} title={t('deployment.members')} hint={t('deployment.membersHint')}>
            <Table
              empty={t('deployment.noMembers')}
              rows={members.map((m) => ({ ...m, key: m.member_id }))}
              columns={[
                { key: 'member_id', label: t('deployment.member'), render: (m) => (
                  <span className="font-mono text-xs">
                    {m.member_id}{m.self && <span className="ml-1 text-indigo-600">({t('deployment.you')})</span>}
                  </span>
                ) },
                { key: 'role', label: t('deployment.role') },
                { key: 'host', label: t('deployment.host'), render: (m) => <span className="font-mono text-xs">{m.host || t('deployment.unknownHost')}</span> },
                { key: 'status', label: t('deployment.status'), render: (m) => <StatusPill status={m.status} /> },
                { key: 'beat', label: t('deployment.beat'), render: (m) => agoLabel(t, m.heartbeat_age_seconds) },
                { key: 'uptime', label: t('deployment.uptime'), render: (m) => uptimeLabel(m.uptime_seconds) },
                { key: 'leases', label: t('deployment.leases'), render: (m) => (m.leases || []).join(', ') || '—' },
                { key: 'load', label: t('deployment.load'), render: (m) => <span className="text-xs text-gray-600">{loadText(m) || '—'}</span> },
                { key: 'version', label: t('deployment.version'), render: (m) => <span className="font-mono text-xs">{m.version || '—'}</span> },
                { key: 'actions', label: '', render: (m) => (
                  <span className="inline-flex gap-1">
                    <button onClick={() => setLogMember(m)} title={t('deployment.viewLogs')}
                            className="p-1 rounded hover:bg-gray-100 text-gray-600">
                      <ScrollText className="w-4 h-4" />
                    </button>
                    <button onClick={() => forget(m.member_id)} disabled={m.status === 'live'}
                            title={t('deployment.forgetHint')}
                            className="p-1 rounded hover:bg-gray-100 text-gray-600 disabled:opacity-30 disabled:cursor-not-allowed">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </span>
                ) },
              ]}
            />
          </Card>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card icon={Repeat} title={t('deployment.queue')}>
              <div className="flex flex-wrap gap-4 text-sm mb-3">
                {['queued', 'leased', 'running', 'failed'].map((k) => (
                  <span key={k}><span className="text-gray-500">{t(`deployment.${k}`)}:</span> <b>{queue[k] ?? 0}</b></span>
                ))}
                <span><span className="text-gray-500">{t('deployment.oldestQueued')}:</span> <b>{Math.round(queue.oldest_queued_seconds || 0)}s</b></span>
                <span><span className="text-gray-500">{t('deployment.outboxPending')}:</span> <b>{outbox.pending ?? 0}</b>
                  {outbox.dead ? <span className="text-red-600"> ({outbox.dead} {t('deployment.outboxDead')})</span> : null}</span>
              </div>
              <Table
                empty={t('deployment.noQueue')}
                rows={(queue.items || []).map((q) => ({ ...q, key: q.run_id }))}
                columns={[
                  { key: 'run_id', label: 'run', render: (q) => <span className="font-mono text-xs">{String(q.run_id).slice(0, 8)}</span> },
                  { key: 'kind', label: t('deployment.queueKind') },
                  { key: 'status', label: t('deployment.status') },
                  { key: 'workspace', label: t('deployment.workspace') },
                  { key: 'lease_owner', label: t('deployment.queueWorker'), render: (q) => <span className="font-mono text-xs">{q.lease_owner || '—'}</span> },
                  { key: 'attempts', label: t('deployment.queueAttempts') },
                ]}
              />
            </Card>

            <Card icon={Box} title={t('deployment.hosts')} hint={t('deployment.hostsHint')}>
              <Table
                empty={t('deployment.noHosts')}
                rows={hosts.map((h) => ({ ...h, key: h.host }))}
                columns={[
                  { key: 'host', label: t('deployment.host'), render: (h) => <span className="font-mono text-xs">{h.host}</span> },
                  { key: 'members', label: t('deployment.members'), render: (h) => h.members.length },
                  { key: 'runs', label: t('deployment.runs') },
                  { key: 'flow_runs', label: t('deployment.flowRuns') },
                  { key: 'nodes', label: t('deployment.nodes') },
                  { key: 'containers', label: t('deployment.containers') },
                ]}
              />
            </Card>
          </div>

          <Card icon={Repeat} title={t('deployment.activeRuns')}>
            <Table
              empty={t('deployment.noRuns')}
              rows={(map?.runs || []).map((r) => ({ ...r, key: r.run_id }))}
              columns={[
                { key: 'run_id', label: 'run', render: (r) => <span className="font-mono text-xs">{String(r.run_id).slice(0, 8)}</span> },
                { key: 'agent_id', label: t('deployment.agent') },
                { key: 'workspace', label: t('deployment.workspace') },
                { key: 'status', label: t('deployment.status') },
                { key: 'host', label: t('deployment.host'), render: (r) => <span className="font-mono text-xs">{r.host || t('deployment.unknownHost')}</span> },
                { key: 'heartbeat', label: t('deployment.heartbeat'), render: (r) => agoLabel(t, r.heartbeat_age_seconds) },
                { key: 'checkpoint', label: t('deployment.checkpoint'), render: (r) => (
                  <span className="text-xs text-gray-600">
                    {r.checkpoint_step ? t('deployment.step', { n: r.checkpoint_step }) : '—'}
                    {r.resume_attempts ? `, ${t('deployment.resumed', { n: r.resume_attempts })}` : ''}
                  </span>
                ) },
              ]}
            />
          </Card>

          {(map?.loops || []).length > 0 && (
            <Card icon={Repeat} title={t('deployment.activeLoops')}>
              <Table
                empty=""
                rows={map.loops.map((l) => ({ ...l, key: l.loop_run_id }))}
                columns={[
                  { key: 'loop_id', label: t('deployment.loop') },
                  { key: 'workspace', label: t('deployment.workspace') },
                  { key: 'iterations_done', label: t('deployment.iteration') },
                  { key: 'owner', label: t('deployment.owner'), render: (l) => <span className="font-mono text-xs">{l.owner || '—'}</span> },
                  { key: 'heartbeat', label: t('deployment.heartbeat'), render: (l) => agoLabel(t, l.heartbeat_age_seconds) },
                ]}
              />
            </Card>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card icon={Server} title={t('deployment.activeNodes')}>
              <Table
                empty="—"
                rows={(map?.nodes || []).map((n) => ({ ...n, key: n.node_id }))}
                columns={[
                  { key: 'label', label: t('deployment.agent'), render: (n) => n.label || n.agent_id },
                  { key: 'workspace', label: t('deployment.workspace') },
                  { key: 'status', label: t('deployment.status') },
                  { key: 'host', label: t('deployment.host'), render: (n) => <span className="font-mono text-xs">{n.host || t('deployment.unknownHost')}</span> },
                ]}
              />
            </Card>
            <Card icon={Box} title={t('deployment.activeContainers')}>
              <Table
                empty="—"
                rows={(map?.containers || []).map((c) => ({ ...c, key: c.name }))}
                columns={[
                  { key: 'name', label: 'name', render: (c) => <span className="font-mono text-xs">{c.name}</span> },
                  { key: 'agent_id', label: t('deployment.agent') },
                  { key: 'state', label: t('deployment.status'), render: (c) => c.state || c.status },
                  { key: 'host', label: t('deployment.host'), render: (c) => (
                    <span className="font-mono text-xs">{c.host || t('deployment.unknownHost')}
                      {c.remote && <span className="ml-1 text-amber-700">({t('deployment.remote')})</span>}</span>
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
