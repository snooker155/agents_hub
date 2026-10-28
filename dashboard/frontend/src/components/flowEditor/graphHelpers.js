// Pure graph helpers: turning a stored flow node into the shape ReactFlow
// renders, and back. No React here, so these are cheap to unit test on their
// own and safe to import from both the canvas wiring and the inspector.
import { cssVar } from '../../lib/themeColors';

// Read once, at module load (there is no component here to re-render on a
// later palette change — see src/lib/themeColors.js's useThemeColors for the
// reactive version components use). The fallbacks are the literal defaults
// this module always shipped, so nothing changes for anyone who never
// touches Account -> Palette. 'design' has no dedicated hue token (violet is
// a brand hue, remapped navy elsewhere, not one of the standalone chromatic
// hues gen-theme.mjs exposes) and stays a plain constant.
export const DOMAIN_COLORS = {
  management: cssVar('--hue-cyan-400', '#22d3ee'),
  analysis: cssVar('--hue-amber-400', '#fbbf24'),
  design: '#a78bfa',
  development: cssVar('--hue-emerald-400', '#34d399'),
  testing: cssVar('--hue-rose-400', '#fb7185'),
  operations: cssVar('--hue-orange-400', '#fb923c'),
  flow: cssVar('--neutral-400', '#94a3b8'),
  general: cssVar('--neutral-400', '#94a3b8'),
};

export function normalizeNode(node, onRunNode) {
  const d = node.data || {};
  return {
    id: node.id,
    type: 'flowNode',
    position: node.position || { x: 100, y: 100 },
    style: { width: 90, ...(node.style || {}) },
    data: {
      node_id: node.id,
      label: d.label || node.label || 'Flow Agent',
      description: d.description || node.description || '',
      agent_id: d.agent_id || node.agent_id || '',
      // Non-agent entity fields (processor/condition/transform). entity_id
      // identifies the registry entity; category drives inspector rendering.
      entity_id: d.entity_id || node.entity_id || '',
      category: d.category || node.category || (d.agent_id || node.agent_id ? 'agent' : ''),
      input: d.input || node.input || [],
      output: d.output || node.output || [],
      config: d.config || node.config || {},
      // Per-node execution policy the engine reads (flow/engine.py): how many
      // times a failed attempt is repeated, and how long one attempt may take.
      retry: d.retry || node.retry || null,
      timeout_seconds: d.timeout_seconds ?? node.timeout_seconds ?? '',
      domain: d.domain || node.domain || 'general',
      nodeTask: d.nodeTask || node.nodeTask || '',
      // An agent node that picks its next step (flow/branching.py). Carried
      // through, or a save from the editor would drop the node's routing.
      route: d.route ?? node.route ?? null,
      route_default: d.route_default ?? node.route_default ?? null,
      route_hint: d.route_hint ?? node.route_hint ?? '',
      onRunNode,
    },
  };
}

export function serializeNode(node) {
  const d = node.data || {};
  const out = {
    id: node.id,
    position: node.position,
    type: node.type,
    style: { width: 90 },
    data: {
      label: d.label || '',
      description: d.description || '',
      agent_id: d.agent_id || '',
      domain: d.domain || 'general',
      nodeTask: d.nodeTask || '',
    },
  };
  // Persist entity fields only when present, keeping plain agent nodes clean.
  if (d.entity_id) out.data.entity_id = d.entity_id;
  if (d.category) out.data.category = d.category;
  if (Array.isArray(d.input) && d.input.length) out.data.input = d.input;
  if (Array.isArray(d.output) && d.output.length) out.data.output = d.output;
  if (d.config && Object.keys(d.config).length) out.data.config = d.config;
  // Policy fields are written only when set, so a node that never used them
  // keeps the same YAML it had before retries and timeouts existed.
  const retryMax = Number(d.retry?.max) || 0;
  const retryBackoff = Number(d.retry?.backoff_seconds) || 0;
  if (retryMax > 0 || retryBackoff > 0) {
    out.data.retry = { max: retryMax, backoff_seconds: retryBackoff };
  }
  const timeout = Number(d.timeout_seconds);
  if (Number.isFinite(timeout) && timeout > 0) out.data.timeout_seconds = timeout;
  // An agent node that picks its next step (flow/branching.py), only when set.
  if (d.route) {
    out.data.route = d.route === true ? 'one' : d.route;
    if (d.route_default) out.data.route_default = d.route_default;
    if (d.route_hint) out.data.route_hint = d.route_hint;
  }
  return out;
}

export function serializeEdge(edge) {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: edge.type,
  };
}

export const INPUT_CLS =
  'w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition';

// Parse / format a comma-separated list of state keys.
export const parseKeys = (s) => s.split(',').map((x) => x.trim()).filter(Boolean);
export const fmtKeys = (arr) => (Array.isArray(arr) ? arr.join(', ') : '');

// Per-node lifecycle event types. Only these drive a node's running/done status;
// other node-tagged events (notably `flow_state`, a post-node state snapshot the
// chat driver emits with the finished node's id) must NOT mask the real terminal
// event, otherwise a completed node reverts to "pending" the moment its
// flow_state snapshot lands. The printed log stream already filters flow_state.
export const NODE_LIFECYCLE_TYPES = new Set([
  'agent_start', 'agent_finish', 'agent_error', 'agent_stopped', 'node_skip',
]);

// The palette offers one abstract agent node instead of every agent: it is
// dropped with no agent and the inspector picks which one runs there. This id
// marks that palette entry; it is never saved on a node.
export const AGENT_SLOT_ID = '__agent__';

// An agent node nobody has picked an agent for yet. It saves and loads like
// any node, but a run refuses the flow until every such node has one
// (flow/validate.py resolve_entities).
export const isUnchosenAgent = (data) =>
  data?.category === 'agent' && !data?.agent_id && !data?.entity_id;

// The state keys a node can read as a run reaches it: the output keys of every
// node upstream of it, each with the labels of the nodes that write it.
export function upstreamOutputKeys(nodeId, nodes, edges) {
  const preds = {};
  for (const e of edges || []) (preds[e.target] ||= []).push(e.source);
  const seen = new Set();
  const stack = [...(preds[nodeId] || [])];
  while (stack.length) {
    const id = stack.pop();
    if (id === nodeId || seen.has(id)) continue;
    seen.add(id);
    stack.push(...(preds[id] || []));
  }
  const writers = new Map();
  for (const node of nodes || []) {
    if (!seen.has(node.id)) continue;
    for (const key of node.data?.output || []) {
      if (!key) continue;
      if (!writers.has(key)) writers.set(key, []);
      writers.get(key).push(node.data?.label || node.id);
    }
  }
  return [...writers].map(([key, labels]) => ({ key, writers: labels }));
}

const keySlug = (text) => String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');

// A switch's routes as {target, match, conditions: [{key, op, value}]}: its
// `routes`, or the first shape (one `key`, `cases` of {op, value, target}) as
// one route per case on that key (flow/entities/conditions/state_switch.py).
export function switchRoutes(config = {}) {
  if (Array.isArray(config.routes) && config.routes.length) return config.routes;
  if (!Array.isArray(config.cases) || !config.cases.length) return [];
  return config.cases.map((c) => ({
    target: c.target || '',
    match: 'all',
    conditions: [{ key: config.key || '', op: c.op || 'equals', value: c.value ?? '' }],
  }));
}

// An agent node joined to a switch: the switch can only test what the agent
// writes, so an agent that writes nothing to state gets an output key, the
// first one the switch's conditions test or one named after the agent, and
// the conditions that test no key yet test it. Returns `nodes` itself when
// there is nothing to do.
export function wireAgentToSwitch(nodes, sourceId, targetId) {
  const source = nodes.find((n) => n.id === sourceId);
  const target = nodes.find((n) => n.id === targetId);
  if (!source || !target) return nodes;
  if (source.data?.category !== 'agent' || target.data?.entity_id !== 'state_switch') return nodes;
  if ((source.data.output || []).some(Boolean)) return nodes;
  const config = target.data.config || {};
  const routes = switchRoutes(config);
  const tested = routes.flatMap((r) => r.conditions || []).map((c) => c.key).find(Boolean);
  const slug = keySlug(source.data.agent_id) || keySlug(source.data.label);
  const key = tested || (slug ? `${slug}_result` : 'result');
  const blank = routes.some((r) => (r.conditions || []).some((c) => !c.key));
  return nodes.map((n) => {
    if (n.id === sourceId) return { ...n, data: { ...n.data, output: [key] } };
    if (n.id === targetId && blank) {
      const { key: _key, cases: _cases, ...rest } = config;
      const filled = routes.map((r) => ({
        ...r, conditions: (r.conditions || []).map((c) => (c.key ? c : { ...c, key })),
      }));
      return { ...n, data: { ...n.data, config: { ...rest, routes: filled } } };
    }
    return n;
  });
}
