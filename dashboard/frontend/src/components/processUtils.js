// Small helpers shared by the process-flow views (ProcessGraph, Chat build
// view). Kept out of ProcessGraph.jsx so that file only exports components
// (react-refresh/only-export-components).

export const SKILL_TOOL = 'get_skill';

export function shortText(v, max = 180) {
  const s = String(v || '');
  return s.length > max ? `${s.slice(0, max)}...` : s;
}

// One-line, whitespace-collapsed teaser used as the collapsed hint on
// expandable process nodes.
export function preview(v, max = 120) {
  const s = String(v || '').replace(/\s+/g, ' ').trim();
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

export function fmtDurationMs(ms) {
  const n = Number(ms || 0);
  if (!n) return '0ms';
  if (n < 1000) return `${n}ms`;
  return `${(n / 1000).toFixed(2)}s`;
}

