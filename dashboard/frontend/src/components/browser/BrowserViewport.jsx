import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, MousePointer2, Globe } from 'lucide-react';
import { useI18n } from '../../i18n';
import { toViewport, VIEWPORT_WIDTH, VIEWPORT_HEIGHT } from './viewport';

// Keys sent as a key press rather than typed text. Everything else with a
// one-character `key` is typed.
const SPECIAL_KEYS = new Set([
  'Enter', 'Backspace', 'Tab', 'Escape', 'Delete', 'Home', 'End', 'PageUp', 'PageDown',
  'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
]);

// Typed characters are sent in small batches, so a word is one request, not one
// per letter; a special key flushes the batch first to keep the order.
const TYPE_FLUSH_MS = 150;
// Wheel events arrive in bursts; they are summed and sent at most this often.
const WHEEL_FLUSH_MS = 150;

/**
 * One browser session on screen: the last frame scaled to the container's
 * width at the viewport's ratio, and, when `controllable`, the person's
 * clicks, keys and wheel sent back as input in viewport pixels.
 *
 * The previous frame stays up while the next one loads (a small spinner says
 * so), which is what makes a polled picture read as a live page rather than a
 * flicker.
 */
export default function BrowserViewport({
  frame, loading = false, controllable = false, onInput, className = '', emptyText,
}) {
  const { t } = useI18n();
  const ref = useRef(null);
  const typed = useRef('');
  const typeTimer = useRef(null);
  const wheel = useRef({ dx: 0, dy: 0, x: 0, y: 0, timer: null });
  const [focused, setFocused] = useState(false);
  const width = frame?.width || VIEWPORT_WIDTH;
  const height = frame?.height || VIEWPORT_HEIGHT;

  const send = useCallback((input) => { if (onInput) onInput(input); }, [onInput]);

  const point = useCallback((e) => {
    const rect = ref.current?.getBoundingClientRect?.() || { left: 0, top: 0, width: 1, height: 1 };
    return toViewport(e.clientX, e.clientY, rect, width, height);
  }, [width, height]);

  const flushTyped = useCallback(() => {
    if (typeTimer.current) { clearTimeout(typeTimer.current); typeTimer.current = null; }
    if (typed.current) {
      const text = typed.current;
      typed.current = '';
      send({ kind: 'type', text });
    }
  }, [send]);

  useEffect(() => () => {
    if (typeTimer.current) clearTimeout(typeTimer.current);
    if (wheel.current.timer) clearTimeout(wheel.current.timer);
  }, []);

  // A native, non-passive listener: React's onWheel cannot stop the page
  // around the viewport from scrolling along with the remote one.
  useEffect(() => {
    const el = ref.current;
    if (!el || !controllable) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      const acc = wheel.current;
      const p = point(e);
      acc.dx += e.deltaX; acc.dy += e.deltaY; acc.x = p.x; acc.y = p.y;
      if (acc.timer) return;
      acc.timer = setTimeout(() => {
        const { dx, dy, x, y } = wheel.current;
        wheel.current = { dx: 0, dy: 0, x: 0, y: 0, timer: null };
        if (dx || dy) send({ kind: 'scroll', x, y, dx: Math.round(dx), dy: Math.round(dy) });
      }, WHEEL_FLUSH_MS);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [controllable, point, send]);

  const onClick = (e) => {
    if (!controllable) return;
    ref.current?.focus?.();
    // The second click of a double click is the dblclick's to send.
    if (e.detail >= 2) return;
    flushTyped();
    send({ kind: 'click', ...point(e) });
  };

  const onDoubleClick = (e) => {
    if (!controllable) return;
    send({ kind: 'dblclick', ...point(e) });
  };

  const onKeyDown = (e) => {
    if (!controllable) return;
    // Leave the person's own shortcuts (copy, reload, switching tabs) alone.
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (SPECIAL_KEYS.has(e.key)) {
      e.preventDefault();
      flushTyped();
      send({ kind: 'key', key: e.key });
      return;
    }
    if (e.key && e.key.length === 1) {
      e.preventDefault();
      typed.current += e.key;
      if (typeTimer.current) clearTimeout(typeTimer.current);
      typeTimer.current = setTimeout(flushTyped, TYPE_FLUSH_MS);
    }
  };

  return (
    <div
      ref={ref}
      data-testid="browser-viewport"
      tabIndex={controllable ? 0 : -1}
      role={controllable ? 'application' : undefined}
      aria-label={t('browser.viewportLabel')}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      onKeyDown={onKeyDown}
      onFocus={() => setFocused(true)}
      onBlur={() => { setFocused(false); flushTyped(); }}
      className={`relative w-full overflow-hidden rounded-lg border bg-gray-100 outline-none ${
        controllable ? 'cursor-pointer border-indigo-300 focus:ring-2 focus:ring-indigo-500' : 'border-gray-200'
      } ${className}`}
      style={{ aspectRatio: `${width} / ${height}` }}
    >
      {frame?.image ? (
        <img
          src={frame.image}
          alt={frame.title || frame.url || t('browser.frameAlt')}
          draggable={false}
          className="absolute inset-0 h-full w-full select-none object-contain"
        />
      ) : (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm text-gray-400">
          {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : <Globe className="h-6 w-6" />}
          <span>{loading ? t('browser.loadingFrame') : (emptyText ?? t('browser.noFrame'))}</span>
        </div>
      )}
      {frame?.image && loading && (
        <span className="absolute right-2 top-2 rounded-full bg-white/80 p-1 shadow-sm">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-500" />
        </span>
      )}
      {controllable && (
        <span className={`absolute left-2 top-2 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium shadow-sm ${
          focused ? 'border-indigo-300 bg-indigo-600 text-white' : 'border-indigo-200 bg-white/90 text-indigo-700'
        }`}>
          <MousePointer2 className="h-3 w-3" />
          {t('browser.inControl')}
        </span>
      )}
    </div>
  );
}
