import { HardDrive } from 'lucide-react';
import { useI18n } from '../../i18n';
import { humanBytes } from '../models/jobs';
import { Card, Table } from './ClusterCard';

/**
 * Machines: how busy each machine under the cluster is.
 *
 * Built from ``machines`` in the cluster map (dashboard/backend/routes/cluster.py),
 * which every member samples with its beat (common/host_metrics.py): the
 * machine's CPU, memory, disk and load with a short history, and the Docker
 * daemon on it with the agent containers it runs. A daemon on no member's
 * machine (Docker Desktop's VM seen from a process on the Mac, a remote
 * DOCKER_HOST) has a row of its own with what the daemon tells: its CPUs and
 * memory, and what the agent containers use of them.
 */

function tone(pct) {
  if (pct === null || pct === undefined) return 'bg-gray-300';
  if (pct >= 90) return 'bg-red-500';
  if (pct >= 70) return 'bg-amber-500';
  return 'bg-emerald-500';
}

/** A usage bar: the whole (``pct``) and, inside it, the agent containers' part. */
function Meter({ pct, part, label, sub }) {
  const whole = pct === null || pct === undefined ? null : Math.max(0, Math.min(100, pct));
  const inner = part === null || part === undefined ? null : Math.max(0, Math.min(100, part));
  return (
    <div className="min-w-[9rem]">
      <div className="h-2 rounded-full bg-gray-100 overflow-hidden relative" aria-hidden="true">
        {whole !== null && <div className={`absolute inset-y-0 left-0 ${tone(whole)} opacity-60`} style={{ width: `${whole}%` }} />}
        {inner !== null && <div className="absolute inset-y-0 left-0 bg-indigo-500" style={{ width: `${inner}%` }} />}
      </div>
      <div className="text-xs text-gray-700 mt-1 tabular-nums">{label}</div>
      {sub && <div className="text-[11px] text-gray-500 tabular-nums">{sub}</div>}
    </div>
  );
}

/** CPU and memory over the last minutes, two lines on one small chart. */
function Trend({ history }) {
  const { t } = useI18n();
  const points = (history || []).filter((p) => Array.isArray(p));
  if (points.length < 2) return <span className="text-xs text-gray-400">—</span>;
  const w = 120;
  const h = 32;
  const path = (idx) => {
    let d = '';
    points.forEach((p, i) => {
      const v = p[idx];
      if (v === null || v === undefined) return;
      const x = (i / (points.length - 1)) * w;
      const y = h - (Math.max(0, Math.min(100, v)) / 100) * (h - 2) - 1;
      d += `${d ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return d;
  };
  const minutes = Math.max(1, Math.round((points[points.length - 1][0] - points[0][0]) / 60));
  return (
    <svg width={w} height={h} role="img" className="block"
         aria-label={t('cluster.machines.trendLabel', { minutes })}>
      <title>{t('cluster.machines.trendLabel', { minutes })}</title>
      <path d={path(2)} fill="none" stroke="currentColor" className="text-sky-500" strokeWidth="1.5" />
      <path d={path(1)} fill="none" stroke="currentColor" className="text-indigo-600" strokeWidth="1.5" />
    </svg>
  );
}

function pct(n) {
  return n === null || n === undefined ? '—' : `${Math.round(n)}%`;
}

function roleCounts(members) {
  const counts = {};
  (members || []).forEach((m) => { counts[m.role || '?'] = (counts[m.role || '?'] || 0) + 1; });
  return Object.entries(counts).map(([r, n]) => (n > 1 ? `${r} ×${n}` : r)).join(', ');
}

function ageSeconds(iso) {
  if (!iso) return null;
  const at = Date.parse(iso);
  return Number.isNaN(at) ? null : Math.max(0, Math.round((Date.now() - at) / 1000));
}

export default function MachinesCard({ machines }) {
  const { t } = useI18n();
  const rows = machines || [];

  const columns = [
    { key: 'name', label: t('cluster.machines.machine'), render: (r) => {
      const docker = r.docker?.[0];
      const m = r.metrics;
      return (
        <div>
          <div className="font-mono text-xs">{r.name}</div>
          <div className="text-[11px] text-gray-500">
            {r.members?.length ? roleCounts(r.members) : t('cluster.machines.dockerOnly')}
            {m?.in_container && ` · ${t('cluster.machines.seenFromContainer')}`}
          </div>
          {docker && (
            <div className="text-[11px] text-gray-500">
              {t('cluster.machines.docker', { name: docker.name || '', version: docker.server_version || '' })}
            </div>
          )}
        </div>
      );
    } },
    { key: 'cpu', label: t('cluster.machines.cpu'), render: (r) => {
      const m = r.metrics;
      const docker = r.docker?.[0];
      const cpus = m?.cpus || docker?.cpus;
      // docker stats counts one busy core as 100%.
      const part = docker && cpus ? (docker.containers_cpu_percent || 0) / cpus : null;
      return (
        <Meter
          pct={m?.cpu_percent ?? null}
          part={part}
          label={m ? t('cluster.machines.cpuOf', { pct: pct(m.cpu_percent), cpus }) : t('cluster.machines.cpus', { cpus: cpus ?? '—' })}
          sub={part !== null ? t('cluster.machines.containersPart', { value: `${part.toFixed(1)}%` }) : null}
        />
      );
    } },
    { key: 'mem', label: t('cluster.machines.memory'), render: (r) => {
      const m = r.metrics;
      const docker = r.docker?.[0];
      const total = m?.mem_total || docker?.mem_total;
      const part = docker && total ? (100 * (docker.containers_mem_used || 0)) / total : null;
      return (
        <Meter
          pct={m?.mem_percent ?? null}
          part={part}
          label={m?.mem_total
            ? t('cluster.machines.used', { used: humanBytes(m.mem_used), total: humanBytes(m.mem_total) })
            : t('cluster.machines.total', { total: humanBytes(total) || '—' })}
          sub={docker ? t('cluster.machines.containersPart', { value: humanBytes(docker.containers_mem_used || 0) }) : null}
        />
      );
    } },
    { key: 'disk', label: t('cluster.machines.disk'), render: (r) => {
      const m = r.metrics;
      if (!m?.disk_total) return <span className="text-xs text-gray-400">—</span>;
      return <Meter pct={m.disk_percent} label={t('cluster.machines.used', { used: humanBytes(m.disk_used), total: humanBytes(m.disk_total) })} />;
    } },
    { key: 'load', label: t('cluster.machines.loadAvg'), render: (r) => (
      <span className="text-xs font-mono text-gray-700">{r.metrics?.load ? r.metrics.load.join(' ') : '—'}</span>
    ) },
    { key: 'containers', label: t('cluster.containers'), render: (r) => {
      const docker = r.docker?.[0];
      if (!docker) return <span className="text-xs text-gray-400">—</span>;
      return (
        <span className="text-xs text-gray-700" title={t('cluster.machines.containersHint')}>
          {docker.agent_containers ?? 0} / {docker.containers_running ?? '—'}
        </span>
      );
    } },
    { key: 'trend', label: t('cluster.machines.trend'), render: (r) => <Trend history={r.metrics?.history} /> },
    { key: 'age', label: t('cluster.machines.sampled'), render: (r) => {
      const age = ageSeconds(r.metrics?.sampled_at || r.docker?.[0]?.sampled_at);
      return <span className="text-xs text-gray-500">{age === null ? '—' : t('cluster.ago', { s: age })}</span>;
    } },
  ];

  return (
    <Card icon={HardDrive} title={t('cluster.machines.title')} hint={t('cluster.machines.hint')}>
      <Table empty={t('cluster.machines.none')} rows={rows.map((r) => ({ ...r, key: r.id }))} columns={columns} />
      {rows.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-4 text-[11px] text-gray-500">
          <span className="inline-flex items-center gap-1"><span className="w-3 h-2 rounded-sm bg-emerald-500 opacity-60" /> {t('cluster.machines.legendMachine')}</span>
          <span className="inline-flex items-center gap-1"><span className="w-3 h-2 rounded-sm bg-indigo-500" /> {t('cluster.machines.legendContainers')}</span>
          <span className="inline-flex items-center gap-1"><span className="w-3 h-0.5 bg-indigo-600" /> CPU <span className="w-3 h-0.5 bg-sky-500 ml-2" /> {t('cluster.machines.memory')}</span>
        </div>
      )}
    </Card>
  );
}
