import React, { useMemo, useCallback } from 'react';
import { useRuntime } from '../runtimes/useRuntime';
import TimelineBar from '../TimelineBar';
import { useI18n } from '../../i18n';

// Process renderer — lays a process graph out in left-to-right layers and
// animates token flow along its edges (the `tokens` runtime), highlighting
// active nodes. Structure edits are ordinary ops (add spec.nodes.*/edges.*), so
// the agent or user can reshape the process while it runs — scenario 7.

function asMap(coll) {
  if (Array.isArray(coll)) { const m = {}; coll.forEach((e, i) => { m[e?.id ?? String(i)] = e; }); return m; }
  return coll && typeof coll === 'object' ? coll : {};
}

// Simple longest-path layering for x; siblings stack in y.
function layout(nodes, edges) {
  const ids = Object.keys(nodes);
  const outFrom = {}; const indeg = {};
  ids.forEach((id) => { indeg[id] = 0; });
  Object.values(edges).forEach((e) => {
    (outFrom[e.source] = outFrom[e.source] || []).push(e.target);
    if (indeg[e.target] != null) indeg[e.target] += 1;
  });
  const depth = {};
  const roots = ids.filter((id) => indeg[id] === 0);
  const queue = [...(roots.length ? roots : ids.slice(0, 1))];
  queue.forEach((id) => { depth[id] = 0; });
  let guard = 0;
  while (queue.length && guard++ < 10000) {
    const id = queue.shift();
    (outFrom[id] || []).forEach((t) => {
      const d = (depth[id] || 0) + 1;
      if (depth[t] == null || d > depth[t]) { depth[t] = d; queue.push(t); }
    });
  }
  ids.forEach((id) => { if (depth[id] == null) depth[id] = 0; });
  const cols = {};
  ids.forEach((id) => { (cols[depth[id]] = cols[depth[id]] || []).push(id); });
  const pos = {};
  Object.entries(cols).forEach(([d, list]) => {
    list.forEach((id, i) => { pos[id] = { x: 90 + Number(d) * 150, y: 50 + i * 80 }; });
  });
  return pos;
}

const TYPE_STYLE = {
  start: { fill: '#16a34a', shape: 'circle' },
  end: { fill: '#dc2626', shape: 'circle' },
  gateway: { fill: '#d97706', shape: 'diamond' },
  task: { fill: '#2a4fbd', shape: 'rect' },
};

export default function ProcessView({ view }) {
  // `t` below is the runtime clock — alias the translator.
  const { t: tr } = useI18n();
  const spec = view?.spec || {};
  const nodes = useMemo(() => asMap(spec.nodes), [spec.nodes]);
  const edges = useMemo(() => asMap(spec.edges), [spec.edges]);
  const pos = useMemo(() => layout(nodes, edges), [nodes, edges]);

  const getParams = useCallback(() => view?.spec?.params || {}, [view]);
  const { frame, t, playing, setPlaying, speed, setSpeed, stepOnce, reset } = useRuntime({
    runtimeName: 'tokens', spec, getParams, timeline: view?.timeline,
  });
  const active = new Set(frame?.activeNodes || []);
  const tokens = frame?.tokens || [];

  const width = Math.max(400, ...Object.values(pos).map((p) => p.x + 100));
  const height = Math.max(220, ...Object.values(pos).map((p) => p.y + 60));

  if (!Object.keys(nodes).length) {
    return <div className="text-sm text-gray-500 p-4">{tr('viewProcessView.emptyProcessTheAgentWill')}</div>;
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-auto">
        <svg width={width} height={height} className="min-w-full">
          {Object.entries(edges).map(([eid, e]) => {
            const a = pos[e.source]; const b = pos[e.target];
            if (!a || !b) return null;
            return (
              <g key={eid}>
                <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#94a3b8" strokeWidth="1.5" markerEnd="url(#arrow)" />
                {e.label && <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 4} fontSize="9" fill="#64748b" textAnchor="middle">{e.label}</text>}
              </g>
            );
          })}
          {tokens.map((tk, i) => {
            const a = pos[tk.from]; const b = pos[tk.to];
            if (!a || !b) return null;
            const x = a.x + (b.x - a.x) * tk.p; const y = a.y + (b.y - a.y) * tk.p;
            return <circle key={i} cx={x} cy={y} r="4" fill="#f59e0b" />;
          })}
          {Object.entries(nodes).map(([id, n]) => {
            const p = pos[id]; if (!p) return null;
            const st = TYPE_STYLE[n?.type] || TYPE_STYLE.task;
            const on = active.has(id);
            return (
              <g key={id} transform={`translate(${p.x},${p.y})`}>
                {st.shape === 'circle' && <circle r="16" fill={st.fill} opacity={on ? 1 : 0.85} stroke={on ? '#fbbf24' : 'none'} strokeWidth="3" />}
                {st.shape === 'diamond' && <rect x="-14" y="-14" width="28" height="28" transform="rotate(45)" fill={st.fill} opacity={on ? 1 : 0.85} stroke={on ? '#fbbf24' : 'none'} strokeWidth="3" />}
                {st.shape === 'rect' && <rect x="-40" y="-16" width="80" height="32" rx="6" fill={st.fill} opacity={on ? 1 : 0.85} stroke={on ? '#fbbf24' : 'none'} strokeWidth="3" />}
                <text y="34" fontSize="10" fill="#475569" textAnchor="middle">{n?.label || id}</text>
              </g>
            );
          })}
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
              <path d="M0,0 L7,3 L0,6 Z" fill="#94a3b8" />
            </marker>
          </defs>
        </svg>
      </div>
      <TimelineBar t={t} playing={playing} onToggle={() => setPlaying((p) => !p)} onStep={stepOnce}
        onReset={reset} speed={speed} onSpeed={setSpeed} range={view?.timeline?.range} />
    </div>
  );
}
