// Pure graph helpers: turning a stored flow node into the shape ReactFlow
// renders, and back. No React here, so these are cheap to unit test on their
// own and safe to import from both the canvas wiring and the inspector.

export const DOMAIN_COLORS = {
  management: '#22d3ee',
  analysis: '#fbbf24',
  design: '#a78bfa',
  development: '#34d399',
  testing: '#fb7185',
  operations: '#fb923c',
  flow: '#94a3b8',
  general: '#94a3b8',
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
  'w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white';

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
