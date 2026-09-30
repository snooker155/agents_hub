import React, { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { DOCK_HEIGHT_VAR, DOCK_RESIZE_EVENT } from './composerDockState';

/**
 * The floor of a page that takes messages.
 *
 * A page with a conversation on it (an instance, a team) reads like a chat:
 * what was said above, the box to say more in below. The box is always at
 * the bottom of the screen, however little or much the page holds, and the
 * page scrolls behind it; a spacer of the box's own height closes the page,
 * so the last thing on it can still be scrolled clear of the box.
 *
 * Fixed to the viewport rather than sticky, because sticky only holds a box
 * back from leaving the screen and lets a short page leave it in the middle.
 * Where it goes horizontally is measured, not assumed: the box is centred on
 * the page's scroller (the Layout's `<main>`, found from the spacer), inside
 * the page's gutters, a little under half of that width. Nothing that opens
 * beside or over the page moves it — not the chat column a page may show,
 * not the page chat's panel — because none of those change the scroller.
 * The chat column instead ends above the box: the box publishes its height
 * as `--composer-dock-height` on the document and announces a change with a
 * `composer-dock-resize` event, which `ChatColumn` measures against.
 *
 * Put the dock last in the page (or last in the page's main column when a
 * chat column sits beside it), and only on the tab that takes messages.
 *
 * @param {object} props
 * @param {React.ReactNode} props.children the composer.
 * @param {string} [props.className] extra classes on the box that holds the composer.
 */
//: The PageContainer's side gutter (px-6), so the box lines up with the page.
const GUTTER = 24;

export default function ComposerDock({ children, className = '' }) {
  const spacerRef = useRef(null);
  const dockRef = useRef(null);
  const [span, setSpan] = useState(null);
  const [height, setHeight] = useState(0);

  // Where the scroller is: re-read whenever it or the window changes size
  // (the sidebar folding, say). The spacer only serves to find it.
  useLayoutEffect(() => {
    const spacer = spacerRef.current;
    if (!spacer || typeof window === 'undefined') return undefined;
    const scroller = spacer.closest('main') || spacer.parentElement;
    if (!scroller) return undefined;
    const measure = () => {
      const r = scroller.getBoundingClientRect();
      setSpan({ left: r.left + GUTTER, width: Math.max(0, r.width - GUTTER * 2) });
    };
    measure();
    window.addEventListener('resize', measure);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(scroller);
    return () => {
      window.removeEventListener('resize', measure);
      observer?.disconnect();
    };
  }, []);

  // How tall the box is: the spacer copies it, so the page ends above the
  // box rather than under it, and the document learns it for the chat
  // column. Attachments, a steering row or an estimate change the height
  // while the page is open, so it is watched, not read once.
  useLayoutEffect(() => {
    const el = dockRef.current;
    if (!el || typeof window === 'undefined') return undefined;
    const root = document.documentElement;
    const publish = (h) => {
      root.style.setProperty(DOCK_HEIGHT_VAR, `${h}px`);
      window.dispatchEvent(new Event(DOCK_RESIZE_EVENT));
    };
    const measure = () => {
      const h = el.getBoundingClientRect().height;
      setHeight(h);
      publish(h);
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(el);
    return () => {
      observer?.disconnect();
      publish(0);
    };
  }, []);

  const dock = (
    <div
      ref={dockRef}
      data-testid="composer-dock"
      style={span ? { left: span.left, width: span.width } : undefined}
      className={`fixed bottom-0 z-20 pointer-events-none ${span ? '' : 'inset-x-0'}`}
    >
      <div className={`mx-auto w-full lg:w-[44%] pb-4 pointer-events-auto ${className}`}>
        {children}
      </div>
    </div>
  );

  return (
    <>
      <div ref={spacerRef} aria-hidden="true" style={{ height }} data-testid="composer-dock-spacer" />
      {typeof document === 'undefined' ? dock : createPortal(dock, document.body)}
    </>
  );
}

