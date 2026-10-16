import { useMemo, useState } from 'react';
import ReactFlow, { Background, Controls, Handle, MarkerType, Position } from 'reactflow';
import 'reactflow/dist/style.css';
import { X } from 'lucide-react';
import { useI18n } from '../../i18n';

/**
 * ServiceMap: the whole running service as one diagram.
 *
 * Left to right: people, the dashboard, the API processes, the launch queue,
 * the workers. Under them, one row of the shared services those processes use
 * (database, Redis, object store, identity provider, browser service, model
 * runtime, egress proxy) and the hub link. Everything comes from the one
 * GET /api/cluster document (members, queue, stack, hub_nodes, hub_link), so
 * the diagram and the tables below it can never disagree.
 *
 * A service that is switched off is not drawn; it waits in a "Not in use" row
 * under the diagram, so a one-process install shows a small, clean map.
 */

const COL = { people: 0, dashboard: 250, api: 500, queue: 750, worker: 1000 };
const ROW_H = 100;
const NODE_W = 200;
const SERVICE_GAP = 230;

// Which paint each kind of node takes from the graph tokens in index.css.
const PAINT = {
  people: 'frontend', dashboard: 'frontend', api: 'backend', queue: 'backend', worker: 'backend',
  database: 'datastore', broker: 'datastore', blob_store: 'datastore',
  hub: 'project', hub_node: 'project',
};

const DOT = {
  ok: 'bg-green-500',
  warn: 'bg-amber-500',
  fail: 'bg-red-500',
  off: 'bg-gray-300',
  unknown: 'border-2 border-gray-400 bg-transparent',
};

const MEMBER_STATUS = { live: 'ok', stale: 'warn', stopped: 'off' };

function StatusDot({ status }) {
  return <span data-testid={`dot-${status}`} className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${DOT[status] || DOT.unknown}`} />;
}

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

function unitLine(t, comp) {
  const unit = comp.unit ? t(`cluster.units.${comp.unit}`) : '';
  const load = num(comp.load);
  const cap = num(comp.capacity);
  if (load !== null && cap !== null) return `${unit} ${load}/${cap}`.trim();
  if (load !== null) return `${unit} ${load}`.trim();
  if (num(comp.latency_ms) !== null) return `${Math.round(comp.latency_ms)} ms`;
  return '';
}

function memberNode(t, m, kind, x, y) {
  const load = m.load || {};
  const cap = num(m.capabilities?.concurrency);
  const tracked = num(load.tracked);
  const isWorker = kind === 'worker';
  const line = isWorker
    ? (cap !== null ? `${t('cluster.map.runs')} ${tracked ?? 0}/${cap}` : `${t('cluster.map.runs')} ${tracked ?? 0}`)
    : `${t('cluster.map.streams')} ${num(load.sse_clients) ?? 0} · ${t('cluster.map.runs')} ${num(load.running_runs_on_host) ?? 0}`;
  return {
    id: `m:${m.member_id}`,
    type: 'svc',
    position: { x, y },
    data: {
      kind, title: m.member_id, sub: `${m.role}${m.host ? ` · ${m.host}` : ''}`,
      status: MEMBER_STATUS[m.status] || 'unknown', line,
      load: isWorker ? tracked ?? 0 : null, capacity: isWorker ? cap : null,
      detail: { member: m.member_id, role: m.role, host: m.host, status: m.status, version: m.version,
                leases: (m.leases || []).join(', '), load: m.load, capabilities: m.capabilities },
    },
  };
}

function stackNode(t, comp, x, y) {
  return {
    id: `s:${comp.id}`,
    type: 'svc',
    position: { x, y },
    data: {
      kind: comp.id, title: t(`cluster.stack.names.${comp.id}`, { defaultValue: comp.label }),
      sub: comp.kind === 'database' && comp.detail?.dialect ? comp.detail.dialect : '',
      status: comp.status, line: unitLine(t, comp) || comp.summary,
      load: num(comp.load), capacity: num(comp.capacity), shared: true,
      detail: { summary: comp.summary, ...(comp.detail || {}), latency_ms: comp.latency_ms },
    },
  };
}

// Which processes talk to which shared service. The Api reads the identity
// provider and the models; workers run browsers, the proxy and the models.
const USERS = {
  database: ['api', 'worker'], broker: ['api', 'worker'], blob_store: ['api', 'worker'],
  oidc: ['api'], browser: ['worker'], models_runtime: ['api', 'worker'], egress_proxy: ['worker'],
  compose: [],
};

/**
 * The nodes, the edges and the "not in use" list for one cluster document.
 * Pure, so the layout is testable without a canvas.
 */
// eslint-disable-next-line react-refresh/only-export-components -- the layout is a pure function the tests call without a canvas
export function buildGraph(map, t) {
  const members = (map?.members || []).filter((m) => m.status !== 'stopped');
  const apis = members.filter((m) => m.role !== 'worker');
  const workers = members.filter((m) => m.role === 'worker')
    .sort((a, b) => String(a.host || '').localeCompare(String(b.host || '')));
  const stack = map?.stack || [];
  const hubNodes = map?.hub_nodes || [];
  const hubLink = map?.hub_link || {};
  const queue = map?.queue || {};

  const compose = stack.find((c) => c.id === 'compose');
  const frontend = (compose?.detail?.services || []).find((s) => s.service === 'frontend');
  const inUse = stack.filter((c) => c.id !== 'compose' && c.status !== 'off');
  const idle = stack.filter((c) => c.status === 'off' || (c.id === 'compose' && compose.status === 'unknown'));

  const rows = Math.max(apis.length, workers.length + hubNodes.length, 1);
  const midY = ((rows - 1) * ROW_H) / 2;
  const colY = (count, i) => midY - ((count - 1) * ROW_H) / 2 + i * ROW_H;
  const nodes = [];
  const edges = [];
  const edge = (source, target, extra = {}) => edges.push({
    id: `${source}>${target}`, source, target,
    markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--graph-external-border)' },
    style: { stroke: 'var(--graph-external-border)', strokeWidth: 1.5, ...(extra.faint ? { opacity: 0.35 } : {}) },
    animated: Boolean(extra.animated), sourceHandle: extra.sourceHandle || 'r', targetHandle: extra.targetHandle || 'l',
  });

  nodes.push({ id: 'people', type: 'svc', position: { x: COL.people, y: colY(1, 0) },
    data: { kind: 'people', title: t('cluster.map.people'), sub: t('cluster.map.peopleSub'), status: 'ok', line: '', detail: {} } });
  let entry = 'people';
  if (frontend) {
    nodes.push({ id: 'dashboard', type: 'svc', position: { x: COL.dashboard, y: colY(1, 0) },
      data: { kind: 'dashboard', title: t('cluster.map.dashboard'), sub: 'nginx',
              status: frontend.running > 0 ? 'ok' : 'fail', line: `${frontend.running}/${frontend.total}`,
              detail: { ...frontend } } });
    edge('people', 'dashboard', { animated: true });
    entry = 'dashboard';
  }

  apis.forEach((m, i) => {
    const n = memberNode(t, m, 'api', COL.api, colY(apis.length, i));
    nodes.push(n);
    const busy = num(m.load?.sse_clients) > 0 || num(m.load?.running_runs_on_host) > 0;
    edge(entry, n.id, { animated: busy });
  });

  const depth = (num(queue.queued) || 0) + (num(queue.leased) || 0);
  const runningNow = num(queue.running) || 0;
  const oldest = Math.round(num(queue.oldest_queued_seconds) || 0);
  nodes.push({ id: 'queue', type: 'svc', position: { x: COL.queue, y: colY(1, 0) },
    data: { kind: 'queue', title: t('cluster.map.queue'), sub: '',
            status: oldest > 300 ? 'warn' : 'ok',
            line: `${t('cluster.map.queued')} ${depth} · ${t('cluster.map.runs')} ${runningNow}${depth ? ` · ${oldest}s` : ''}`,
            load: null, capacity: null, detail: { ...queue, items: undefined } } });
  apis.forEach((m) => edge(`m:${m.member_id}`, 'queue', { animated: depth > 0 || runningNow > 0 }));

  const hostsSeen = [];
  workers.forEach((m, i) => {
    const n = memberNode(t, m, 'worker', COL.worker, colY(workers.length + hubNodes.length, i));
    nodes.push(n);
    edge('queue', n.id, { animated: (num(m.load?.tracked) || 0) > 0 });
    if (!hostsSeen.includes(m.host || '')) hostsSeen.push(m.host || '');
  });
  // Machines lending this hub their models and sandbox take work like workers.
  hubNodes.forEach((h, i) => {
    const cap = num(h.capacity);
    const busy = num(h.in_flight) || 0;
    nodes.push({ id: `n:${h.id}`, type: 'svc', position: { x: COL.worker, y: colY(workers.length + hubNodes.length, workers.length + i) },
      data: { kind: 'hub_node', title: h.name || h.id, sub: t('cluster.map.hubNode'),
              status: h.online ? 'ok' : 'off', line: `${t('cluster.map.runs')} ${busy}${cap !== null ? `/${cap}` : ''}`,
              load: busy, capacity: cap, detail: { ...h } } });
    edge('queue', `n:${h.id}`, { animated: busy > 0 });
  });

  // One backdrop per host, so workers on the same machine read as a group.
  const groupHosts = hostsSeen.filter(Boolean);
  groupHosts.forEach((host) => {
    const idx = workers.map((w, i) => ({ w, i })).filter((x) => (x.w.host || '') === host).map((x) => x.i);
    if (!idx.length) return;
    const top = colY(workers.length + hubNodes.length, idx[0]) - 26;
    const height = (idx.length - 1) * ROW_H + 110;
    nodes.unshift({ id: `host:${host}`, type: 'host', position: { x: COL.worker - 12, y: top },
      data: { label: host }, style: { width: NODE_W + 24, height }, selectable: false, draggable: false, zIndex: -1 });
  });

  // The shared services, one row under the columns.
  const bottomY = (rows - 1) * ROW_H + 190;
  const shared = [...inUse];
  const remote = hubLink.linked;
  const total = shared.length + (remote ? 1 : 0);
  const startX = Math.max(0, (COL.worker + NODE_W - ((total - 1) * SERVICE_GAP + NODE_W)) / 2);
  shared.forEach((c, i) => {
    nodes.push(stackNode(t, c, startX + i * SERVICE_GAP, bottomY));
    const users = USERS[c.id] || [];
    const live = c.status !== 'fail';
    if (users.includes('api')) apis.forEach((m) => edge(`m:${m.member_id}`, `s:${c.id}`, {
      sourceHandle: 'b', targetHandle: 't', faint: true, animated: live && c.id === 'database' && num(m.load?.running_runs_on_host) > 0 }));
    if (users.includes('worker')) workers.forEach((m) => edge(`m:${m.member_id}`, `s:${c.id}`, {
      sourceHandle: 'b', targetHandle: 't', faint: true, animated: live && c.id === 'database' && (num(m.load?.tracked) || 0) > 0 }));
  });
  if (remote) {
    nodes.push({ id: 'remote', type: 'svc', position: { x: startX + shared.length * SERVICE_GAP, y: bottomY },
      data: { kind: 'hub', title: t('cluster.map.remoteHub'), sub: hubLink.name || '',
              status: hubLink.last_error ? 'warn' : 'ok', line: hubLink.url || '', shared: true,
              detail: { url: hubLink.url, last_sync_at: hubLink.last_sync_at, last_error: hubLink.last_error, modes: hubLink.modes } } });
    apis.forEach((m) => edge(`m:${m.member_id}`, 'remote', { sourceHandle: 'b', targetHandle: 't', faint: true }));
  }

  return { nodes, edges, idle };
}

function SvcNode({ data }) {
  const { t } = useI18n();
  const paint = PAINT[data.kind] || 'external';
  const bar = num(data.load) !== null && num(data.capacity) ? Math.min(100, Math.round((data.load / data.capacity) * 100)) : null;
  const hidden = { opacity: 0, width: 6, height: 6 };
  return (
    <div
      style={{ width: NODE_W, background: `var(--graph-${paint}-bg)`, border: `1.5px solid var(--graph-${paint}-border)` }}
      className="rounded-lg px-3 py-2 text-left shadow-sm cursor-pointer"
    >
      <Handle id="l" type="target" position={Position.Left} style={hidden} isConnectable={false} />
      <Handle id="t" type="target" position={Position.Top} style={hidden} isConnectable={false} />
      <div className="flex items-center gap-2">
        <StatusDot status={data.status} />
        <span className="truncate text-xs font-bold text-gray-900" title={data.title}>{data.title}</span>
      </div>
      {data.sub ? <div className="mt-0.5 truncate text-[10px] text-gray-500">{data.sub}</div> : null}
      {data.line ? <div className="mt-1 truncate text-[11px] text-gray-700 tabular-nums" title={data.line}>{data.line}</div> : null}
      {bar !== null && (
        <div className="mt-1 h-1 overflow-hidden rounded-full bg-black/10" aria-label={t('cluster.map.load')}>
          <div className={`h-full ${bar >= 90 ? 'bg-amber-500' : 'bg-green-500'}`} style={{ width: `${bar}%` }} />
        </div>
      )}
      <Handle id="r" type="source" position={Position.Right} style={hidden} isConnectable={false} />
      <Handle id="b" type="source" position={Position.Bottom} style={hidden} isConnectable={false} />
    </div>
  );
}

function HostGroup({ data }) {
  return (
    <div className="h-full w-full rounded-xl border border-dashed border-gray-300 bg-gray-50/40">
      <span className="absolute -top-2 left-3 rounded bg-white px-1.5 text-[10px] font-mono text-gray-500">{data.label}</span>
    </div>
  );
}

const NODE_TYPES = { svc: SvcNode, host: HostGroup };

function flat(detail) {
  const rows = [];
  Object.entries(detail || {}).forEach(([k, v]) => {
    if (v === null || v === undefined || v === '') return;
    rows.push([k, typeof v === 'object' ? JSON.stringify(v) : String(v)]);
  });
  return rows;
}

export default function ServiceMap({ map }) {
  const { t } = useI18n();
  const [picked, setPicked] = useState(null);
  const { nodes, edges, idle } = useMemo(() => buildGraph(map, t), [map, t]);
  const selected = nodes.find((n) => n.id === picked);

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm" data-testid="service-map">
      <h3 className="text-sm font-bold text-gray-800 mb-1">{t('cluster.map.title')}</h3>
      <p className="text-xs text-gray-400 mb-3">{t('cluster.map.hint')}</p>
      <div className="h-[420px] sm:h-[480px] w-full overflow-hidden rounded-lg border border-gray-100">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          panOnDrag
          zoomOnScroll={false}
          preventScrolling={false}
          minZoom={0.2}
          fitView
          fitViewOptions={{ padding: 0.1 }}
          proOptions={{ hideAttribution: true }}
          onNodeClick={(_, node) => { if (node.type === 'svc') setPicked(node.id); }}
          onPaneClick={() => setPicked(null)}
        >
          <Background gap={20} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      {idle.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2" data-testid="not-in-use">
          <span className="text-xs font-semibold text-gray-500">{t('cluster.map.notInUse')}</span>
          {idle.map((c) => (
            <span key={c.id} title={c.summary}
                  className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] text-gray-500">
              <StatusDot status={c.status} /> {t(`cluster.stack.names.${c.id}`, { defaultValue: c.label })}
            </span>
          ))}
        </div>
      )}

      {selected && (
        <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 p-3" data-testid="map-detail">
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="flex items-center gap-2 text-sm font-bold text-gray-800">
              <StatusDot status={selected.data.status} /> {selected.data.title}
              <span className="text-xs font-normal text-gray-500">{t(`cluster.stack.status.${selected.data.status}`)}</span>
            </span>
            <button onClick={() => setPicked(null)} title={t('cluster.close')} className="p-1 text-gray-500 hover:text-gray-800">
              <X className="h-4 w-4" />
            </button>
          </div>
          <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-[max-content_1fr]">
            {flat(selected.data.detail).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="font-mono text-gray-500">{k}</dt>
                <dd className="break-all text-gray-800">{v}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}
