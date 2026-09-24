// Pure helpers shared by the 2D (reactflow) and 3D (three) structure views,
// so both draw the same graph from one parse of the API answer.

const KIND_ORDER = { embedding: 0, layer: 1, norm: 2, head: 3 };

/** The categorical keys a dtype or quantisation maps to, in legend order. */
export const DTYPE_KEYS = ['f32', 'f16', 'bf16', 'q8', 'q6', 'q5', 'q4', 'q3', 'q2', 'iq', 'other'];

/** CSS variables behind each dtype key, for useThemeColors. The fallbacks are
 * named colours only so a test DOM without theme.css still gets a value. */
export const DTYPE_COLOR_SPEC = {
  f32: ['--hue-blue-400', 'cornflowerblue'],
  f16: ['--hue-sky-400', 'skyblue'],
  bf16: ['--hue-cyan-400', 'darkturquoise'],
  q8: ['--hue-teal-400', 'lightseagreen'],
  q6: ['--hue-emerald-400', 'mediumseagreen'],
  q5: ['--hue-lime-400', 'yellowgreen'],
  q4: ['--hue-amber-400', 'goldenrod'],
  q3: ['--hue-orange-400', 'darkorange'],
  q2: ['--hue-red-400', 'indianred'],
  iq: ['--hue-pink-400', 'hotpink'],
  other: ['--neutral-400', 'darkgray'],
};

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

/** The dtype a block is coloured by: its quantisation, else the dtype that
 * holds most of its bytes. */
export function blockDtype(block) {
  if (block?.quantization) return block.quantization;
  const byDtype = {};
  for (const t of block?.tensors || []) {
    if (!t?.dtype) continue;
    byDtype[t.dtype] = (byDtype[t.dtype] || 0) + num(t.bytes || t.parameters || 1);
  }
  let best = null;
  for (const [dtype, bytes] of Object.entries(byDtype)) {
    if (best == null || bytes > byDtype[best]) best = dtype;
  }
  return best;
}

/**
 * `{nodes, edges}` in reading order: embedding, layers by index, norm, head.
 * `other` blocks are kept (after the main chain) with `side: true` and no
 * edge, so the renderers hang them off to the side.
 */
export function buildGraph(structure) {
  const blocks = Array.isArray(structure?.blocks) ? structure.blocks : [];
  const main = [];
  const side = [];
  blocks.forEach((block, position) => {
    if (!block || block.id == null) return;
    const kind = KIND_ORDER[block.kind] != null ? block.kind : 'other';
    const node = {
      id: String(block.id),
      kind,
      label: block.label || String(block.id),
      index: Number.isInteger(block.index) ? block.index : null,
      parameters: num(block.parameters),
      bytes: num(block.bytes),
      quantization: block.quantization || null,
      dtype: blockDtype(block),
      tensors: Array.isArray(block.tensors) ? block.tensors : [],
      side: kind === 'other',
      position,
    };
    (node.side ? side : main).push(node);
  });
  main.sort((a, b) => {
    const k = KIND_ORDER[a.kind] - KIND_ORDER[b.kind];
    if (k) return k;
    const ia = a.index ?? Number.MAX_SAFE_INTEGER;
    const ib = b.index ?? Number.MAX_SAFE_INTEGER;
    if (ia !== ib) return ia - ib;
    return a.position - b.position;
  });
  const edges = [];
  for (let i = 1; i < main.length; i += 1) {
    edges.push({ source: main[i - 1].id, target: main[i].id });
  }
  return { nodes: [...main, ...side], edges };
}

/** `{id: 0..1}` on a log scale of parameters, so a small head is still
 * visible next to a large layer. Every node with no parameters gets 0. */
export function layerScale(nodes) {
  const out = {};
  const logs = (nodes || []).map((n) => [n.id, Math.log10(1 + Math.max(0, num(n.parameters)))]);
  const positive = logs.filter(([, v]) => v > 0).map(([, v]) => v);
  const max = positive.length ? Math.max(...positive) : 0;
  const min = positive.length ? Math.min(...positive) : 0;
  for (const [id, v] of logs) {
    if (v <= 0 || max <= 0) out[id] = 0;
    else if (max === min) out[id] = 1;
    // A floor of 0.15 keeps the smallest block visible; the rest spreads out.
    else out[id] = 0.15 + 0.85 * ((v - min) / (max - min));
  }
  return out;
}

/** Map a dtype or quantisation name (F16, Q4_K_M, IQ3_XXS, torch.bfloat16)
 * to one of DTYPE_KEYS. */
export function dtypeColorKey(dtype) {
  if (!dtype) return 'other';
  const s = String(dtype).toLowerCase().replace(/^torch\./, '');
  if (s.startsWith('iq')) return 'iq';
  if (s.includes('bf16') || s.includes('bfloat16')) return 'bf16';
  if (s === 'f32' || s === 'fp32' || s.includes('float32')) return 'f32';
  if (s === 'f16' || s === 'fp16' || s.includes('float16') || s === 'half') return 'f16';
  const q = s.match(/^(?:q|int|i)(\d)/);
  if (q) {
    const bits = Number(q[1]);
    if (bits >= 8) return 'q8';
    if (bits >= 2 && bits <= 6) return `q${bits}`;
    if (bits === 1) return 'q2';
  }
  if (s.startsWith('f8') || s.includes('float8') || s.startsWith('fp8')) return 'q8';
  return 'other';
}

/** 1.2 GB, 340 MB, 12 KB, 512 B. Binary steps, short decimal units. */
export function formatBytes(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let value = n;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  const digits = i === 0 || value >= 100 ? 0 : 1;
  return `${Number(value.toFixed(digits))} ${units[i]}`;
}

/** 1.2B, 340M, 12K, 512. */
export function formatCount(count) {
  const n = Number(count);
  if (!Number.isFinite(n)) return '';
  const abs = Math.abs(n);
  const steps = [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']];
  for (const [size, suffix] of steps) {
    if (abs >= size) {
      const v = n / size;
      return `${Number(v.toFixed(v >= 100 ? 0 : 1))}${suffix}`;
    }
  }
  return String(Math.round(n));
}
