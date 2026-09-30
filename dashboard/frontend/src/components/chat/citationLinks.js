/**
 * Plain helpers for citations (see Citations.jsx): where a source links to,
 * the DOM id its list entry carries, and the event a clicked `[n]` marker
 * sends so the list can highlight the entry.
 */

export const CITATION_FOCUS_EVENT = 'agents-hub:citation-focus';

export function citationDomId(anchor, n) {
  return `${anchor || 'reply'}-cite-${n}`;
}

/** Where a citation points: its workspace file, else the memory page. */
export function citationHref(c) {
  if (c?.workspace_file_id) return `/files?file=${encodeURIComponent(c.workspace_file_id)}`;
  return '/memory';
}

/** Scroll the list entry for `[n]` into view and ask it to highlight itself. */
export function focusCitation(anchor, n) {
  const el = typeof document !== 'undefined' ? document.getElementById(citationDomId(anchor, n)) : null;
  if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  window.dispatchEvent(new CustomEvent(CITATION_FOCUS_EVENT, { detail: { anchor, n } }));
}
