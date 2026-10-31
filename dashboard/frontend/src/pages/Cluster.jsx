import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { runLink } from '../components/runs/runLinks';
import {
  AlertTriangle, Box, Cpu, Layers, RefreshCw, Repeat, ScrollText, Search, Server, Trash2, Waypoints, X,
} from 'lucide-react';
import { forgetMember, getCluster, getMemberLogs } from '../api';
import { useChannel } from '../components/stream';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import PageLoader from '../components/PageLoader';
import ServiceMap from '../components/cluster/ServiceMap';
import WorkersCard from '../components/cluster/WorkersCard';
import MachinesCard from '../components/cluster/MachinesCard';
import ContainerLogModal from '../components/cluster/ContainerLogModal';
import { humanBytes } from '../components/models/jobs';
import { Card, Table } from '../components/cluster/ClusterCard';
import { ListLoadMore, ListPagingControls } from '../components/ListPaging';
import { useListPaging } from '../components/listPagingState';

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

// Members accumulate: every replica and worker that ever started keeps its row
// until it is forgotten. The table filters, sorts and shows them in batches on
// this side, since the whole list arrives in the one cluster document.
const STATUS_ORDER = { live: 0, stale: 1, stopped: 2 };
const MEMBER_SORTS = ['status', 'member', 'role', 'host', 'beat', 'uptime'];

const MEMBER_SORT_VALUE = {
  status: (m) => STATUS_ORDER[m.status] ?? 3,
  member: (m) => m.member_id || '',
  role: (m) => m.role || '',
  host: (m) => m.host || '',
  beat: (m) => m.heartbeat_age_seconds ?? Infinity,
  uptime: (m) => m.uptime_seconds ?? -1,
};

function compareMembers(sort, order) {
  const value = MEMBER_SORT_VALUE[sort] || MEMBER_SORT_VALUE.status;
  const sign = order === 'asc' ? 1 : -1;
  return (a, b) => {
    const x = value(a);
    const y = value(b);
    const c = typeof x === 'string' ? x.localeCompare(y) : (x > y) - (x < y);
    return c * sign || String(a.member_id).localeCompare(String(b.member_id));
  };
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

/** A run's short id, linked to the run's own page when it has one. */
function RunId({ run }) {
  const short = String(run.run_id).slice(0, 8);
  const to = runLink(run);
  if (!to) return <span className="font-mono text-xs">{short}</span>;
  return <Link to={to} title={String(run.run_id)} className="font-mono text-xs text-indigo-600 hover:underline">{short}</Link>;
}

export default function Cluster() {
  const { t } = useI18n();
  const [map, setMap] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [logMember, setLogMember] = useState(null);
  const [logContainer, setLogContainer] = useState(null);

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

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the loader is shared with the interval, Refresh and forget; it sets state after its await
    load();
  }, [load]);
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

  const members = useMemo(() => map?.members || [], [map]);
  const [memberQuery, setMemberQuery] = useState('');
  const [memberRole, setMemberRole] = useState('');
  // Live members only at first: a restarted service leaves its old
  // processes behind as stale or stopped rows.
  const [memberStatus, setMemberStatus] = useState('live');
  const [allHosts, setAllHosts] = useState(false);
  const memberPaging = useListPaging('cluster-members', {
    sorts: MEMBER_SORTS, defaultSort: 'status', defaultOrder: 'asc',
    resetOn: [memberQuery, memberRole, memberStatus],
  });
  const memberRoles = useMemo(
    () => [...new Set(members.map((m) => m.role).filter(Boolean))].sort(),
    [members],
  );
  const filteredMembers = useMemo(() => {
    const q = memberQuery.trim().toLowerCase();
    return members
      .filter((m) => !memberRole || m.role === memberRole)
      .filter((m) => !memberStatus || m.status === memberStatus)
      .filter((m) => !q || [m.member_id, m.host, m.version, ...(m.leases || [])]
        .some((v) => String(v || '').toLowerCase().includes(q)))
      .sort(compareMembers(memberPaging.sort, memberPaging.order));
  }, [members, memberQuery, memberRole, memberStatus, memberPaging.sort, memberPaging.order]);
  const shownMembers = filteredMembers.slice(0, memberPaging.limit);
  const queue = map?.queue || {};
  const outbox = map?.outbox || {};
  const everyHost = map?.hosts || [];
  // A host is active while a live member or some work (a run, an instance, a
  // container) is on it; the rest show on request.
  const hosts = allHosts ? everyHost : everyHost.filter((h) => h.active !== false);
  const hiddenHosts = everyHost.length - everyHost.filter((h) => h.active !== false).length;

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
          <ServiceMap map={map} onViewLogs={({ member, container }) => {
            if (member) setLogMember(member);
            else if (container) setLogContainer(container);
          }} />
          <MachinesCard machines={map?.machines} />
          <WorkersCard onChanged={load} />
          <Card icon={Server} title={t('cluster.members')} hint={t('cluster.membersHint')}>
            {members.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 mb-3">
                <div className="relative">
                  <Search className="w-3.5 h-3.5 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
                  <input
                    value={memberQuery}
                    onChange={(e) => setMemberQuery(e.target.value)}
                    placeholder={t('cluster.memberSearch')}
                    aria-label={t('cluster.memberSearch')}
                    className="pl-8 pr-3 py-1.5 text-sm border border-gray-200 rounded-lg w-64 max-w-full bg-white focus:outline-none"
                  />
                </div>
                <select value={memberRole} onChange={(e) => setMemberRole(e.target.value)}
                        aria-label={t('cluster.role')}
                        className="px-2 py-1.5 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none">
                  <option value="">{t('cluster.allRoles')}</option>
                  {memberRoles.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                <select value={memberStatus} onChange={(e) => setMemberStatus(e.target.value)}
                        aria-label={t('cluster.status')}
                        className="px-2 py-1.5 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none">
                  <option value="">{t('cluster.allStatuses')}</option>
                  {Object.keys(STATUS_ORDER).map((s) => <option key={s} value={s}>{t(`cluster.${s}`)}</option>)}
                </select>
                <ListPagingControls
                  paging={memberPaging}
                  options={MEMBER_SORTS}
                  labels={{
                    status: t('cluster.status'), member: t('cluster.member'), role: t('cluster.role'),
                    host: t('cluster.host'), beat: t('cluster.beat'), uptime: t('cluster.uptime'),
                  }}
                  compact
                />
              </div>
            )}
            <Table
              empty={members.length ? t('cluster.noMembersMatch') : t('cluster.noMembers')}
              rows={shownMembers.map((m) => ({ ...m, key: m.member_id }))}
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
                { key: 'capacity', label: t('cluster.capacity'), render: (m) => (
                  <span className="text-xs text-gray-600">
                    {m.capabilities?.concurrency !== undefined && m.capabilities?.concurrency !== null
                      ? `${m.load?.tracked ?? 0}/${m.capabilities.concurrency}` : '—'}
                  </span>
                ) },
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
            {filteredMembers.length > memberPaging.pageSize && (
              <div className="mt-3">
                <ListLoadMore paging={memberPaging} shown={shownMembers.length} total={filteredMembers.length} />
              </div>
            )}
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
              {hiddenHosts > 0 && (
                <label className="mb-2 inline-flex items-center gap-2 text-xs text-gray-600 cursor-pointer">
                  <input type="checkbox" checked={allHosts} onChange={(e) => setAllHosts(e.target.checked)} />
                  {t('cluster.showInactiveHosts', { count: hiddenHosts })}
                </label>
              )}
              <Table
                empty={t('cluster.noHosts')}
                rows={hosts.map((h) => ({ ...h, key: h.host }))}
                columns={[
                  { key: 'host', label: t('cluster.host'), render: (h) => (
                    <span className={`font-mono text-xs ${h.active === false ? 'text-gray-400' : ''}`}>
                      {h.host}{h.active === false && h.status ? ` (${t(`cluster.${h.status}`, { defaultValue: h.status })})` : ''}
                    </span>
                  ) },
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
                { key: 'run_id', label: 'run', render: (r) => <RunId run={r} /> },
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
                  { key: 'run_id', label: 'run', render: (r) => <RunId run={r} /> },
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
                  { key: 'loop_id', label: t('cluster.loop'), render: (l) => {
                    const to = runLink({ run_id: l.loop_run_id, kind: 'loop', entity_id: l.loop_id });
                    return to ? <Link to={to} className="text-indigo-600 hover:underline">{l.loop_id}</Link> : l.loop_id;
                  } },
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

          {/* Containers carry usage columns now: each card takes the full width. */}
          <div className="grid grid-cols-1 gap-4">
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
                  { key: 'host', label: t('cluster.host'), render: (n) => <span className="font-mono text-xs">{n.carrier_host || n.host || t('cluster.unknownHost')}</span> },
                ]}
              />
            </Card>
            <Card icon={Box} title={t('cluster.activeContainers')}>
              <Table
                empty="—"
                rows={(map?.containers || []).map((c) => ({ ...c, key: c.name }))}
                columns={[
                  { key: 'name', label: 'name', render: (c) => <span className="font-mono text-xs">{c.name}</span> },
                  { key: 'agent_id', label: t('cluster.agent'), render: (c) => {
                    if (c.owner_kind === 'instance' && c.owner_id) {
                      return <Link to={`/instances/${c.owner_id}`} className="text-indigo-600 hover:underline">{c.agent_id || c.owner_id}</Link>;
                    }
                    const to = c.owner_id && runLink({
                      run_id: c.owner_id, kind: c.owner_kind === 'run' ? '' : c.owner_kind, entity_id: c.agent_id });
                    if (to) {
                      return <Link to={to} className="text-indigo-600 hover:underline">{c.agent_id || c.owner_id}</Link>;
                    }
                    return c.agent_id || '—';
                  } },
                  { key: 'state', label: t('cluster.status'), render: (c) => c.state || c.status },
                  { key: 'cpu', label: 'CPU', render: (c) => (
                    <span className="text-xs tabular-nums">{c.stats?.cpu_percent != null ? `${c.stats.cpu_percent.toFixed(1)}%` : '—'}</span>
                  ) },
                  { key: 'mem', label: t('cluster.machines.memory'), render: (c) => (
                    <span className="text-xs tabular-nums" title={c.stats?.mem_percent != null ? `${c.stats.mem_percent}%` : undefined}>
                      {c.stats?.mem_used != null
                        ? `${humanBytes(c.stats.mem_used)}${c.stats.mem_limit ? ` / ${humanBytes(c.stats.mem_limit)}` : ''}`
                        : '—'}
                    </span>
                  ) },
                  { key: 'net', label: t('cluster.machines.net'), render: (c) => (
                    <span className="text-xs tabular-nums text-gray-600">
                      {c.stats?.net_rx != null ? `↓${humanBytes(c.stats.net_rx)} ↑${humanBytes(c.stats.net_tx || 0)}` : '—'}
                    </span>
                  ) },
                  { key: 'pids', label: t('cluster.machines.pids'), render: (c) => (
                    <span className="text-xs tabular-nums">{c.stats?.pids ?? '—'}</span>
                  ) },
                  { key: 'worker', label: t('cluster.queueWorker'), render: (c) => (
                    <span className="font-mono text-xs">{c.worker || c.launched_on || '—'}</span>
                  ) },
                  { key: 'host', label: t('cluster.dockerHost'), render: (c) => (
                    <span className="font-mono text-xs">{c.host || t('cluster.unknownHost')}
                      {c.remote && <span className="ml-1 text-amber-700">({t('cluster.remote')})</span>}</span>
                  ) },
                  { key: 'logs', label: '', render: (c) => (
                    <button onClick={() => setLogContainer(c)} title={t('cluster.viewLogs')}
                            aria-label={t('cluster.viewLogs')}
                            className="p-1 rounded hover:bg-gray-100 text-gray-600">
                      <ScrollText className="w-4 h-4" />
                    </button>
                  ) },
                ]}
              />
            </Card>
          </div>
        </div>
      )}

      {logMember && <MemberLogModal member={logMember} onClose={() => setLogMember(null)} />}
      {logContainer && <ContainerLogModal container={logContainer} onClose={() => setLogContainer(null)} />}
    </PageContainer>
  );
}
