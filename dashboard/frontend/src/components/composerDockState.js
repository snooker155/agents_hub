/**
 * What the composer dock (ComposerDock.jsx) shares with the pages around it,
 * kept out of the component file the way `stream.js` sits beside
 * `StreamContext.jsx`: the document-level record of the dock's height and the
 * event that announces a change, which `ChatColumn` measures against.
 */
export const DOCK_HEIGHT_VAR = '--composer-dock-height';
export const DOCK_RESIZE_EVENT = 'composer-dock-resize';

/** The height of the dock on screen right now, in pixels; 0 without one. */
export function currentDockHeight() {
  if (typeof document === 'undefined') return 0;
  const raw = document.documentElement.style.getPropertyValue(DOCK_HEIGHT_VAR);
  const n = parseFloat(raw);
  return Number.isFinite(n) ? n : 0;
}
