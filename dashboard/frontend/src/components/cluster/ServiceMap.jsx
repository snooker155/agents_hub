import { useMemo, useState } from 'react';
import ReactFlow, { Background, Controls, Handle, MarkerType, Position } from 'reactflow';
import 'reactflow/dist/style.css';
import { ScrollText, X } from 'lucide-react';
import { useI18n } from '../../i18n';

/**
 * ServiceMap: the whole running service as one diagram.
 *
 * Left to right: people, the dashboard, the API processes, the launch queue,
 * the workers, and the agent containers each worker started (one instance,
 * one container; a run's container too), joined to their worker. Under them, one row of the shared services those processes use
 * (database, Redis, object store, identity provider, browser service, model
 * runtime, egress proxy) and the hub link. Everything comes from the one
 * GET /api/cluster document (members, queue, stack, hub_nodes, hub_link), so
 * the diagram and the tables below it can never disagree.
 *
 * A service that is switched off is not drawn; it waits in a "Not in use" row
 * under the diagram, so a one-process install shows a small, clean map.
 */

// Columns left to right. A column of many members wraps into a grid of at
// most MAX_ROWS rows, and the columns after it move right, so a busy cluster
// grows across the page rather than down it.
const COL_GAP = 250;
const SUB_GAP = 230;
const ROW_H = 100;
const NODE_W = 200;
const MAX_ROWS = 4;
const SERVICE_GAP = 230;
const SERVICES_PER_ROW = 6;

// Which paint each kind of node takes from the graph tokens in index.css.
const PAINT = {
  people: 'frontend', dashboard: 'frontend', api: 'backend', queue: 'backend', worker: 'backend',
  database: 'datastore', broker: 'datastore', blob_store: 'datastore',
  hub: 'project', hub_node: 'project', stale: 'external', container: 'project',
};

// A container's own state as a status dot.
const CONTAINER_STATUS = {
  running: 'ok', active: 'ok', standby: 'ok', starting: 'warn', queued: 'warn', stopping: 'warn',
  failed: 'fail', error: 'fail', exited: 'off', stopped: 'off',
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
      status: MEMBER_STATUS[m.status] || 'unknown', line, logOf: { member: m },
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
  // Live members are drawn one by one; stale ones (the beat stopped without a
  // clean shutdown) fold into one card per column, opened for the list.
  const live = members.filter((m) => m.status !== 'stale');
  const staleOf = (worker) => members.filter((m) => m.status === 'stale' && (m.role === 'worker') === worker);
  const apis = live.filter((m) => m.role !== 'worker');
  const workers = live.filter((m) => m.role === 'worker')
    .sort((a, b) => String(a.host || '').localeCompare(String(b.host || '')));
  const staleApis = staleOf(false);
  const staleWorkers = staleOf(true);
  const stack = map?.stack || [];
  const hubNodes = map?.hub_nodes || [];
  const hubLink = map?.hub_link || {};
  const queue = map?.queue || {};

  const compose = stack.find((c) => c.id === 'compose');
  const frontend = (compose?.detail?.services || []).find((s) => s.service === 'frontend');
  const inUse = stack.filter((c) => c.id !== 'compose' && c.status !== 'off');
  const idle = stack.filter((c) => c.status === 'off' || (c.id === 'compose' && compose.status === 'unknown'));

  // The worker side: one group per host (its backdrop), then the lent hub
  // nodes, then the stale card. Each group wraps on its own.
  const workerGroups = [];
  workers.forEach((m) => {
    const host = m.host || '';
    const last = workerGroups[workerGroups.length - 1];
    if (last && last.host === host) last.items.push({ member: m });
    else workerGroups.push({ host, items: [{ member: m }] });
  });
  if (hubNodes.length) workerGroups.push({ host: null, items: hubNodes.map((h) => ({ hub: h })) });
  if (staleWorkers.length) workerGroups.push({ host: null, items: [{ stale: staleWorkers }] });
  const apiItems = [...apis.map((m) => ({ member: m })), ...(staleApis.length ? [{ stale: staleApis }] : [])];

  const tall = (n) => Math.min(Math.max(n, 1), MAX_ROWS);
  const wide = (n) => Math.max(1, Math.ceil(n / MAX_ROWS));
  const containerCount = (map?.containers || []).filter((c) => workers.some((m) => m.member_id === c.worker)).length;
  const rows = Math.max(tall(apiItems.length), ...workerGroups.map((g) => tall(g.items.length)),
    tall(containerCount), 1);
  const midY = ((rows - 1) * ROW_H) / 2;
  const colY = (count, i) => midY - ((count - 1) * ROW_H) / 2 + i * ROW_H;
  // Where the i-th of n items of a wrapped column sits.
  const cell = (x0, n, i) => {
    const col = Math.floor(i / MAX_ROWS);
    const inCol = Math.min(MAX_ROWS, n - col * MAX_ROWS);
    return { x: x0 + col * SUB_GAP, y: colY(inCol, i % MAX_ROWS) };
  };

  const COL = { people: 0, dashboard: COL_GAP };
  COL.api = frontend ? COL.dashboard + COL_GAP : COL.dashboard;
  COL.queue = COL.api + (wide(apiItems.length) - 1) * SUB_GAP + COL_GAP;
  COL.worker = COL.queue + COL_GAP;

  const nodes = [];
  const edges = [];
  const edge = (source, target, extra = {}) => edges.push({
    id: `${source}>${target}`, source, target,
    markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--graph-external-border)' },
    style: { stroke: 'var(--graph-external-border)', strokeWidth: 1.5, ...(extra.faint ? { opacity: 0.35 } : {}) },
    animated: Boolean(extra.animated), sourceHandle: extra.sourceHandle || 'r', targetHandle: extra.targetHandle || 'l',
  });
  const staleNode = (id, list, x, y) => ({
    id, type: 'svc', position: { x, y },
    data: { kind: 'stale', title: t('cluster.map.staleMembers', { count: list.length }), sub: t('cluster.map.staleSub'),
            status: 'warn', line: '',
            detail: Object.fromEntries(list.map((m) => [m.member_id, [m.role, m.host].filter(Boolean).join(' · ')])) },
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

  apiItems.forEach((item, i) => {
    const { x, y } = cell(COL.api, apiItems.length, i);
    if (item.stale) {
      nodes.push(staleNode('stale:api', item.stale, x, y));
      return;
    }
    const m = item.member;
    const n = memberNode(t, m, 'api', x, y);
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

  let groupX = COL.worker;
  workerGroups.forEach((group) => {
    const n = group.items.length;
    group.items.forEach((item, i) => {
      const { x, y } = cell(groupX, n, i);
      if (item.stale) {
        nodes.push(staleNode('stale:worker', item.stale, x, y));
      } else if (item.hub) {
        // Machines lending this hub their models and sandbox take work like workers.
        const h = item.hub;
        const cap = num(h.capacity);
        const busy = num(h.in_flight) || 0;
        nodes.push({ id: `n:${h.id}`, type: 'svc', position: { x, y },
          data: { kind: 'hub_node', title: h.name || h.id, sub: t('cluster.map.hubNode'),
                  status: h.online ? 'ok' : 'off', line: `${t('cluster.map.runs')} ${busy}${cap !== null ? `/${cap}` : ''}`,
                  load: busy, capacity: cap, detail: { ...h } } });
        edge('queue', `n:${h.id}`, { animated: busy > 0 });
      } else {
        const m = item.member;
        const node = memberNode(t, m, 'worker', x, y);
        nodes.push(node);
        edge('queue', node.id, { animated: (num(m.load?.tracked) || 0) > 0 });
      }
    });
    // One backdrop per host, so workers on the same machine read as a group.
    if (group.host) {
      const cols = wide(n);
      const top = colY(tall(n), 0) - 26;
      nodes.unshift({ id: `host:${group.host}`, type: 'host', position: { x: groupX - 12, y: top },
        data: { label: group.host },
        style: { width: (cols - 1) * SUB_GAP + NODE_W + 24, height: (tall(n) - 1) * ROW_H + 110 },
        selectable: false, draggable: false, zIndex: -1 });
    }
    groupX += wide(n) * SUB_GAP + (group.host ? 20 : 0);
  });
  // The agent containers, in a column right of the workers, each joined to
  // the worker that started it (map.containers[].worker).
  const workerIds = new Set(workers.map((m) => m.member_id));
  const boxes = (map?.containers || []).filter((c) => workerIds.has(c.worker));
  const boxX = (workerGroups.length ? groupX - SUB_GAP : COL.worker) + COL_GAP;
  boxes.forEach((c, i) => {
    const { x, y } = cell(boxX, boxes.length, i);
    const state = String(c.state || c.status || '').toLowerCase();
    const id = `c:${c.name}`;
    nodes.push({ id, type: 'svc', position: { x, y },
      data: { kind: 'container', title: c.agent_id || c.name,
              sub: [c.owner_kind ? t(`cluster.map.owner.${c.owner_kind}`, { defaultValue: c.owner_kind }) : '',
                    c.workspace].filter(Boolean).join(' · '),
              status: CONTAINER_STATUS[state] || 'unknown', line: c.name, logOf: { container: c },
              detail: { container: c.name, worker: c.worker, launched_on: c.launched_on, docker_host: c.host,
                        owner: [c.owner_kind, c.owner_id].filter(Boolean).join(' '), workspace: c.workspace,
                        state: c.state || c.status, image: c.image } } });
    edge(`m:${c.worker}`, id, { animated: state === 'running' || state === 'active' });
  });
  const lastX = boxes.length ? boxX + (wide(boxes.length) - 1) * SUB_GAP
    : (workerGroups.length ? groupX - SUB_GAP : COL.worker);
  const rightEdge = lastX + NODE_W;

  // The shared services, in rows under the columns, centred on them.
  const bottomY = (rows - 1) * ROW_H + 190;
  const remote = hubLink.linked;
  const shared = [...inUse.map((c) => ({ comp: c })), ...(remote ? [{ remote: true }] : [])];
  const perRow = Math.max(SERVICES_PER_ROW, Math.floor((rightEdge - NODE_W) / SERVICE_GAP) + 1);
  shared.forEach((item, i) => {
    const row = Math.floor(i / perRow);
    const inRow = Math.min(perRow, shared.length - row * perRow);
    const startX = Math.max(0, (rightEdge - ((inRow - 1) * SERVICE_GAP + NODE_W)) / 2);
    const x = startX + (i % perRow) * SERVICE_GAP;
    const y = bottomY + row * ROW_H;
    if (item.remote) {
      nodes.push({ id: 'remote', type: 'svc', position: { x, y },
        data: { kind: 'hub', title: t('cluster.map.remoteHub'), sub: hubLink.name || '',
                status: hubLink.last_error ? 'warn' : 'ok', line: hubLink.url || '', shared: true,
                detail: { url: hubLink.url, last_sync_at: hubLink.last_sync_at, last_error: hubLink.last_error, modes: hubLink.modes } } });
      apis.forEach((m) => edge(`m:${m.member_id}`, 'remote', { sourceHandle: 'b', targetHandle: 't', faint: true }));
      return;
    }
    const c = item.comp;
    nodes.push(stackNode(t, c, x, y));
    const users = USERS[c.id] || [];
    const ok = c.status !== 'fail';
    if (users.includes('api')) apis.forEach((m) => edge(`m:${m.member_id}`, `s:${c.id}`, {
      sourceHandle: 'b', targetHandle: 't', faint: true, animated: ok && c.id === 'database' && num(m.load?.running_runs_on_host) > 0 }));
    if (users.includes('worker')) workers.forEach((m) => edge(`m:${m.member_id}`, `s:${c.id}`, {
      sourceHandle: 'b', targetHandle: 't', faint: true, animated: ok && c.id === 'database' && (num(m.load?.tracked) || 0) > 0 }));
  });

  return { nodes, edges, idle };
}

const SELECTED_SHADOW = '0 0 0 3px var(--brand-500), '
  + '0 0 0 9px color-mix(in srgb, var(--brand-500) 25%, transparent), 0 10px 24px rgba(0, 0, 0, 0.18)';

function SvcNode({ data, selected }) {
  const { t } = useI18n();
  const paint = PAINT[data.kind] || 'external';
  const bar = num(data.load) !== null && num(data.capacity) ? Math.min(100, Math.round((data.load / data.capacity) * 100)) : null;
  const hidden = { opacity: 0, width: 6, height: 6 };
  return (
    <div
      style={{ width: NODE_W, background: `var(--graph-${paint}-bg)`,
               border: `1.5px solid ${selected ? 'var(--brand-500)' : `var(--graph-${paint}-border)`}`,
               // The map is drawn scaled down to fit, so the mark is wide: a
               // solid edge and a soft halo, as shadows that move nothing.
               boxShadow: selected ? SELECTED_SHADOW : undefined }}
      className={`rounded-lg px-3 py-2 text-left cursor-pointer transition-shadow ${selected ? '' : 'shadow-sm'}`}
      data-selected={selected ? 'true' : undefined}
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

export default function ServiceMap({ map, onViewLogs }) {
  const { t } = useI18n();
  const [picked, setPicked] = useState(null);
  const graph = useMemo(() => buildGraph(map, t), [map, t]);
  const { idle } = graph;
  // The picked box stays marked across the page's refresh (a new graph each
  // time), and the lines to and from it stand out.
  const nodes = useMemo(() => graph.nodes.map((n) => (n.id === picked ? { ...n, selected: true } : n)),
    [graph, picked]);
  const edges = useMemo(() => graph.edges.map((e) => (
    picked && (e.source === picked || e.target === picked)
      ? { ...e, style: { ...e.style, stroke: 'var(--color-indigo-500, #6366f1)', strokeWidth: 2.5, opacity: 1 }, zIndex: 1 }
      : e)), [graph, picked]);
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
              {onViewLogs && selected.data.logOf && (
                <button onClick={() => onViewLogs(selected.data.logOf)}
                        className="ml-1 inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-2 py-0.5 text-xs font-medium text-gray-700 hover:bg-gray-100">
                  <ScrollText className="h-3.5 w-3.5" /> {t('cluster.viewLogs')}
                </button>
              )}
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
