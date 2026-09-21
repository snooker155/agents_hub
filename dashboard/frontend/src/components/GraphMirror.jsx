import { useMemo } from 'react';
import { useI18n } from '../i18n';

/**
 * A read-only picture of an external agent's own graph.
 *
 * An imported agent that is internally a graph publishes its shape (manifest
 * `runtime.graph_path`), and the hub draws it here. Deliberately *not* the flow
 * canvas: a flow is something this hub executes, node by node, against agents it
 * owns, and giving a foreign graph the same surface would come with a Run
 * button that cannot work. This is a mirror. It has no controls, and the only
 * live thing about it is which node is lit.
 *
 * Layout is a longest-path layering, top to bottom, which is what the graph
 * frameworks themselves draw and what makes a branch read as a branch. Cycles
 * are expected (an agent loop is a cycle) and are handled by ranking on the
 * edges that make progress and drawing the rest as back edges.
 */

const NODE_W = 132;
const NODE_H = 34;
const GAP_X = 20;
const GAP_Y = 46;
const PAD = 14;

/** Rank every node by its longest path from a root, tolerating cycles. */
function layer(nodes, edges) {
  const ids = nodes.map((n) => n.id);
  const incoming = new Map(ids.map((id) => [id, []]));
  edges.forEach((e) => { if (incoming.has(e.target)) incoming.get(e.target).push(e.source); });

  const rank = new Map(ids.map((id) => [id, 0]));
  // Relaxation rather than a topological sort: a graph with a loop has no
  // topological order at all, and refusing to draw it would hide exactly the
  // graphs worth looking at. Bounded by the node count, so a cycle settles
  // instead of spinning.
  for (let pass = 0; pass < ids.length; pass += 1) {
    let moved = false;
    ids.forEach((id) => {
      const preds = incoming.get(id) || [];
      if (!preds.length) return;
      const best = Math.max(...preds.map((p) => rank.get(p) ?? 0));
      if (best + 1 > (rank.get(id) ?? 0)) { rank.set(id, best + 1); moved = true; }
    });
    if (!moved) break;
  }

  const rows = new Map();
  nodes.forEach((n) => {
    const r = rank.get(n.id) ?? 0;
    if (!rows.has(r)) rows.set(r, []);
    rows.get(r).push(n);
  });

  const width = Math.max(...[...rows.values()].map((row) => row.length * NODE_W + (row.length - 1) * GAP_X), NODE_W);
  const placed = new Map();
  [...rows.keys()].sort((a, b) => a - b).forEach((r) => {
    const row = rows.get(r);
    const rowWidth = row.length * NODE_W + (row.length - 1) * GAP_X;
    row.forEach((n, i) => {
      placed.set(n.id, {
        ...n,
        x: PAD + (width - rowWidth) / 2 + i * (NODE_W + GAP_X),
        y: PAD + r * (NODE_H + GAP_Y),
      });
    });
  });

  return {
    placed,
    width: width + PAD * 2,
    height: PAD * 2 + rows.size * NODE_H + Math.max(0, rows.size - 1) * GAP_Y,
  };
}

/** One edge, as a path that leaves the bottom of its source and enters the top
 *  of its target. A back edge (upwards) is bowed out to the side so it does not
 *  run through the nodes it skips. */
function edgePath(from, to) {
  const x1 = from.x + NODE_W / 2;
  const y1 = from.y + NODE_H;
  const x2 = to.x + NODE_W / 2;
  const y2 = to.y;
  if (y2 <= y1) {
    const side = x2 >= x1 ? 1 : -1;
    const bow = NODE_W * 0.7 * side;
    return `M ${x1} ${y1} C ${x1 + bow} ${y1 + 30}, ${x2 + bow} ${y2 - 30}, ${x2} ${y2}`;
  }
  const mid = (y1 + y2) / 2;
  return `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`;
}

export default function GraphMirror({
  topology, activeNode = null, visitedNodes = [], pausedNode = null,
}) {
  const { t } = useI18n();
  // Memoised off the topology object rather than read inline: the fallback
  // `[]` would be a new array each render and re-layout the graph every time.
  const nodes = useMemo(() => topology?.nodes || [], [topology]);
  const edges = useMemo(() => topology?.edges || [], [topology]);
  const visited = useMemo(() => new Set(visitedNodes), [visitedNodes]);
  const { placed, width, height } = useMemo(() => layer(nodes, edges), [nodes, edges]);

  if (!nodes.length) {
    return (
      <p className="text-sm text-gray-400 italic">
        {topology?.error || t('graphMirror.noTopology')}
      </p>
    );
  }

  return (
    <div className="overflow-auto">
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        className="max-w-full"
        role="img"
        aria-label={t('graphMirror.ariaLabel', { nodes: nodes.length })}
      >
        <defs>
          <marker id="gm-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 Z" className="fill-gray-300" />
          </marker>
        </defs>

        {edges.map((e, i) => {
          const from = placed.get(e.source);
          const to = placed.get(e.target);
          if (!from || !to) return null;
          return (
            <path
              key={i}
              d={edgePath(from, to)}
              fill="none"
              className={e.conditional ? 'stroke-indigo-300' : 'stroke-gray-300'}
              strokeWidth="1.5"
              // A dashed line is how a conditional edge reads as "may or may
              // not be taken", which is the one thing a static picture of a
              // graph has to convey.
              strokeDasharray={e.conditional ? '4 3' : undefined}
              markerEnd="url(#gm-arrow)"
            />
          );
        })}

        {[...placed.values()].map((n) => {
          const isPaused = pausedNode === n.id;
          const isActive = activeNode === n.id;
          const isTerminal = n.kind === 'terminal';
          const wasVisited = visited.has(n.id);
          // A paused node is not a running one and not a finished one: it is
          // where a person's answer is missing, which is the whole reason
          // anybody opens this picture while a run is parked.
          const fill = isPaused ? 'fill-amber-100'
            : isActive ? 'fill-indigo-500'
            : wasVisited ? 'fill-indigo-50'
            : isTerminal ? 'fill-gray-50' : 'fill-white';
          const stroke = isPaused ? 'stroke-amber-500'
            : isActive ? 'stroke-indigo-600'
            : wasVisited ? 'stroke-indigo-300' : 'stroke-gray-200';
          return (
            <g key={n.id}>
              <rect
                x={n.x} y={n.y} width={NODE_W} height={NODE_H}
                rx={isTerminal ? NODE_H / 2 : 8}
                className={`${fill} ${stroke}`}
                strokeWidth="1.5"
              />
              <text
                x={n.x + NODE_W / 2}
                y={n.y + NODE_H / 2 + 4}
                textAnchor="middle"
                className={`text-[11px] ${
                  isActive ? 'fill-white font-semibold'
                  : isPaused ? 'fill-amber-800 font-semibold'
                  : isTerminal ? 'fill-gray-400' : 'fill-gray-700'}`}
              >
                {n.label.length > 18 ? `${n.label.slice(0, 17)}…` : n.label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
