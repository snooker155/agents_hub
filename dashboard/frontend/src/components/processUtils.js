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


// Whether a text is written in markdown (a heading, a list, emphasis, a code
// span or fence, a link, a table row, a quote), so a step's output can be
// rendered as such instead of shown as raw text with its markers.
const MARKDOWN_RE = new RegExp([
  /(^|\n) {0,3}#{1,6}\s+\S/.source,
  /(^|\n) {0,3}(?:[-*+]|\d+[.)])\s+\S/.source,
  /(^|\n) {0,3}>\s/.source,
  /(^|\n) {0,3}(?:```|~~~)/.source,
  /(^|\n)\s*\|.+\|\s*(\n|$)/.source,
  /\*\*[^*\n]+\*\*/.source,
  /__[^_\n]+__/.source,
  /`[^`\n]+`/.source,
  /\[[^\]\n]+\]\([^)\s]+\)/.source,
].join('|'));

export function looksLikeMarkdown(v) {
  return MARKDOWN_RE.test(String(v || ''));
}

// Markdown read as plain text, for a one- or two-line preview where the
// rendered form does not fit: markers go, the words stay.
export function plainMarkdown(v) {
  return String(v || '')
    .replace(/```[^\n]*\n?/g, '')
    .replace(/\[([^\]\n]+)\]\([^)\s]+\)/g, '$1')
    .replace(/(\*\*|__)(.+?)\1/g, '$2')
    .replace(/`([^`\n]+)`/g, '$1')
    .replace(/(^|\n) {0,3}(?:#{1,6}|>|[-*+]|\d+[.)])\s+/g, '$1')
    .replace(/(^|\n)\s*\|?\s*:?-{3,}[\s|:-]*(?=\n|$)/g, '$1');
}
