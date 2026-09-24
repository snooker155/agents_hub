/**
 * Viewport geometry shared by the live view and its tests: kept out of the
 * component file so that one exports only components (fast refresh).
 */

// The service's viewport; a frame names its own size, this is the fallback.
export const VIEWPORT_WIDTH = 1280;
export const VIEWPORT_HEIGHT = 800;

/**
 * Map a point on the rendered image back to viewport pixels. The image is
 * the whole viewport scaled to the element's width, so one ratio does it.
 */
export function toViewport(clientX, clientY, rect, width = VIEWPORT_WIDTH, height = VIEWPORT_HEIGHT) {
  const w = rect.width || 1;
  const h = rect.height || 1;
  const x = Math.round(((clientX - rect.left) * width) / w);
  const y = Math.round(((clientY - rect.top) * height) / h);
  return {
    x: Math.max(0, Math.min(width - 1, x)),
    y: Math.max(0, Math.min(height - 1, y)),
  };
}
