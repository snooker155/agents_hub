import React, { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { MessagesSquare } from 'lucide-react';
import { usePageChatPanel } from './pageChat/pageChat';

/**
 * The side-column chat layout: the page keeps working on the left, the agent
 * that edits it sits in a column on the right, and the column folds away.
 *
 * It lives here because six surfaces want it, and because the one property that
 * matters is easy to lose: **opening the chat must not move anything on the
 * page except the width of the column beside it.**
 *
 * That rules out the obvious implementation. Binding the page to the viewport
 * height and letting the row own what is left (`h-full` + `flex-1` + an inner
 * scroller) gives a chat that fills the screen, but it also turns the header
 * and the filters into shrinkable flex items, swaps the page's scrollbar for an
 * inner one, and re-runs the whole vertical layout on every toggle. Everything
 * on the page then twitches when the button is pressed.
 *
 * So the page is left alone entirely. The grid adds a second column and nothing
 * else; the chat measures what is left of the screen below it and sticks to the
 * top of the scroller it is in, which gives the same result — a column filling
 * the screen with its composer on the floor — without the page cooperating.
 *
 * Only applies from `lg` up. Below that there is no second column, so the chat
 * follows the page content and everything scrolls the ordinary way.
 */

// This hook and `FILL_COLUMN` below live beside the components because eight
// pages import all four names together from this one module as the chat
// column "kit" (hook, toggle, fill props, column). Splitting them into a
// separate file would be the properly-scoped fix for Fast Refresh, but it
// would ripple into every one of those pages' imports, so it is left as a
// deliberate, commented exception rather than done piecemeal here.
// eslint-disable-next-line react-refresh/only-export-components
export function useChatColumn(defaultOpen = true) {
  const [open, setOpen] = useState(defaultOpen);
  // The floating panel hosts this same conversation when it is open — same
  // endpoint, same thread, same agent — so the column steps aside rather than
  // running a second copy of it.
  const { inlineSuppressed, setOpen: setPanelOpen } = usePageChatPanel();

  // While the panel has the chat, the page's own button is the way back to the
  // column: pressing it moves the conversation here rather than toggling a
  // column the user cannot see.
  const toggle = useCallback(() => {
    if (inlineSuppressed) {
      setPanelOpen(false);
      setOpen(true);
      return;
    }
    setOpen((o) => !o);
  }, [inlineSuppressed, setPanelOpen]);

  return {
    open: open && !inlineSuppressed,
    setOpen,
    toggle,
    // Written out in full, never assembled from parts: Tailwind finds classes
    // by scanning the source text, so an arbitrary value built by interpolation
    // is a class that never gets generated. The column is wide enough for a
    // readable turn and narrow enough that the page stays the main thing.
    //
    // `items-start` keeps the chat at its own height instead of stretching it
    // to match a long page.
    gridClass: open && !inlineSuppressed
      ? 'grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(19rem,25rem)] gap-6 items-start'
      : '',
    // The page's column. `min-w-0` only, so nothing about how the page scrolls
    // changes when the chat appears beside it.
    mainClass: 'min-w-0',
  };
}

/** Header button that folds the column away and brings it back. */
export function ChatToggle({ open, onToggle, label, className = '' }) {
  return (
    <button
      onClick={onToggle}
      aria-pressed={open}
      className={`inline-flex items-center px-3 py-2 text-sm font-medium rounded-lg border ${
        open
          ? 'text-indigo-700 bg-indigo-50 border-indigo-200 hover:bg-indigo-100'
          : 'text-gray-600 bg-white border-gray-300 hover:text-indigo-700 hover:border-indigo-300'
      } ${className}`}
    >
      <MessagesSquare className="w-4 h-4 mr-1.5" /> {label}
    </button>
  );
}

/**
 * Props that make an EntityChat fill this column exactly: no taller than the
 * card, no shorter, composer on the floor.
 *
 * Spread them rather than passing the parts by hand — the three work only
 * together. Without `flex-1` the panel is only as tall as its content, so on a
 * short conversation the composer floats in the middle of the card; without
 * `min-h-0` it refuses to shrink on a long one and pushes the composer out the
 * bottom; `mt-auto` holds the composer down in the first case.
 */
// eslint-disable-next-line react-refresh/only-export-components -- see the note above `useChatColumn`.
export const FILL_COLUMN = {
  heightClass: 'min-h-0 max-h-none',
  className: 'flex-1 min-h-0',
  composerClassName: 'mt-auto',
};

/** Gap left below the column, and the smallest it is allowed to get. */
const BOTTOM_GAP = 24;
const MIN_HEIGHT = 320;

/**
 * The column itself: the rest of the screen from where it starts, pinned while
 * the page scrolls past it.
 *
 * The height is measured rather than written as a fraction of the viewport,
 * and that is deliberate. How much screen is left below the column depends on
 * what sits above it, which differs per page — a header here, a header plus a
 * tab strip there — so any fixed `vh` is right on one page and cuts the
 * composer off the bottom on the next. Measuring is the only version that is
 * correct everywhere, including on pages that do not exist yet.
 *
 * Measured from the *parent*, not from the column: once sticky engages, the
 * column's own top is the sticky offset rather than its place in the flow, and
 * measuring that would grow the column a little further on every recalculation.
 * The parent row never sticks, so its top is stable.
 *
 * Below `lg` none of this applies: there is no column, so it takes a slice of
 * the viewport and sits in the normal flow under the page content.
 */
export function ChatColumn({ children }) {
  const ref = useRef(null);
  const [height, setHeight] = useState(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof window === 'undefined') return undefined;

    const measure = () => {
      const row = el.parentElement;
      if (!row) return;
      const top = row.getBoundingClientRect().top;
      const available = window.innerHeight - Math.max(top, 0) - BOTTOM_GAP;
      // Capped as well as floored: if a recalculation lands while the page is
      // scrolled, the row's top is above the fold and `available` overshoots
      // the screen.
      const capped = Math.min(available, window.innerHeight - BOTTOM_GAP * 2);
      setHeight(Math.max(MIN_HEIGHT, capped));
    };

    measure();
    window.addEventListener('resize', measure);
    // What sits above the column can change height on its own — a banner
    // appears, a filter row wraps — and the column has to follow it.
    const observer = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(measure);
    observer?.observe(document.body);
    return () => {
      window.removeEventListener('resize', measure);
      observer?.disconnect();
    };
  }, []);

  return (
    <aside
      ref={ref}
      className="h-[70vh] lg:h-auto lg:sticky lg:top-6 lg:min-h-0"
      /* Handed over as a custom property rather than an inline `height` so the
         small-screen rule still wins: an inline style would apply at every
         width and override the `70vh` the stacked layout wants. */
      style={height ? { '--chat-column-height': `${height}px` } : undefined}
    >
      <div
        className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm flex flex-col h-full min-h-0 overflow-hidden lg:h-[var(--chat-column-height)]"
      >
        {children}
      </div>
    </aside>
  );
}

export default ChatColumn;
