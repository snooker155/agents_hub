import { toBlob } from 'html-to-image';

// Turn a rendered view into a PNG. Elements marked `data-view-chrome` (toolbars,
// thumbnails, buttons) are left out of the picture.

export const CHROME_ATTR = 'data-view-chrome';
const SLIDE_W = 1280;
const SLIDE_H = 720;

/** A file name safe on every system: no path characters, never empty. */
export function sanitizeFilename(title, fallback = 'view') {
  const base = String(title || '')
    // eslint-disable-next-line no-control-regex
    .replace(/[\\/:*?"<>|\u0000-\u001f]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^\.+/, '')
    .slice(0, 80)
    .trim();
  return `${base || fallback}.png`;
}

/** html-to-image `filter`: false drops a node (and its subtree) from the picture. */
export function imageFilter(node) {
  return !(node && node.nodeType === 1 && node.hasAttribute?.(CHROME_ATTR));
}

/** The first opaque background from the element up, white when none is set. */
export function solidBackground(el) {
  for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
    const bg = getComputedStyle(n).backgroundColor;
    if (bg && bg !== 'transparent' && !/rgba\(\s*\d+,\s*\d+,\s*\d+,\s*0\s*\)/.test(bg)) return bg;
  }
  return '#ffffff';
}

export class ViewImageError extends Error {
  constructor(code) { super(code); this.code = code; }
}

/**
 * PNG Blob of the rendered view inside `root`. Throws ViewImageError with code
 * 'empty' (nothing rendered), 'iframe' (a sandboxed html view cannot be read
 * from outside) or 'failed'.
 */
export async function captureViewImage(root, { kind } = {}) {
  if (!root) throw new ViewImageError('empty');
  const base = { pixelRatio: 2, cacheBust: true, filter: imageFilter };
  try {
    let blob;
    if (kind === 'slides') {
      // The stage is laid out at 1280x720 and scaled to the frame by a CSS
      // transform: capture the unscaled inner element with the transform off.
      const stage = root.querySelector('[data-testid="slide-stage"]');
      const inner = stage?.firstElementChild?.firstElementChild;
      if (!inner) throw new ViewImageError('empty');
      blob = await toBlob(inner, {
        ...base, width: SLIDE_W, height: SLIDE_H, backgroundColor: '#ffffff',
        style: { transform: 'none', transformOrigin: 'top left' },
      });
    } else {
      if (root.querySelector('iframe')) throw new ViewImageError('iframe');
      blob = await toBlob(root, { ...base, backgroundColor: solidBackground(root) });
    }
    if (!blob || blob.size === 0) throw new ViewImageError('failed');
    return blob;
  } catch (e) {
    if (e instanceof ViewImageError) throw e;
    throw new ViewImageError('failed');
  }
}
