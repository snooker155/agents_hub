/**
 * A dashboard page drawn alone inside a frame of the dashboard itself: the
 * assistant's "show on screen" panel (components/assistant/ScreenPanel.jsx).
 *
 * The frame's name says so. A name survives navigation inside the frame,
 * unlike a query parameter, so a link followed there still draws the page
 * without the sidebar and the header.
 */
export const EMBED_FRAME_NAME = 'ah-embed';

/** Whether this window is such a frame. */
export function isEmbedded(win = typeof window !== 'undefined' ? window : undefined) {
  try {
    return Boolean(win) && win.self !== win.top && win.name === EMBED_FRAME_NAME;
  } catch {
    // A cross-origin parent: not ours to draw into.
    return false;
  }
}

/** The address of an app route for the frame, under the app's base path. */
export function embedUrl(path, base = import.meta.env.BASE_URL || '/') {
  const root = base.endsWith('/') ? base.slice(0, -1) : base;
  return `${root}${path.startsWith('/') ? path : `/${path}`}`;
}
