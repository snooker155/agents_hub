import { useLayoutEffect, useRef, useState } from 'react';

// Tabs the pages share:
//
// - TabRow: a page's underlined tab row with its controls on the right; the
//   controls drop under the tabs once the two no longer fit on one line.
// - PaneTab: one segment of a switch set in a gray pill
//   (`inline-flex items-center gap-1 p-0.5 rounded-lg bg-gray-100`), for
//   the readings of one card.
// - ColumnTab: half of a sidebar card's header (`grid grid-cols-2
//   border-b`), the switch that is also the column's title.

/** One of the two readings of a run. A switch rather than two panes, so the
    column belongs to whichever one you asked for — and set at the weight of
    the panel headings it replaced, since that is what it is: the title of what
    you are looking at, which happens to be clickable. */
export function PaneTab({ active, onClick, icon: Icon, label, badge = 0 }) {
  return (
    <button
      type="button" onClick={onClick}
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-bold uppercase tracking-wide transition-colors ${
        active ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
      }`}
    >
      <Icon className={`w-3.5 h-3.5 ${active ? 'text-indigo-500' : 'text-gray-400'}`} />
      {label}
      {badge > 0 && (
        <span className="px-1.5 rounded-full bg-indigo-50 text-indigo-600 text-[10px] font-bold">
          {badge > 99 ? '99+' : badge}
        </span>
      )}
    </button>
  );
}


/** The sidebar card's header: the switch *is* the title, in two halves across
    the card's full width. A pill above the card plus a heading inside it named
    the same panel twice and left the card looking untitled — this is one
    header that happens to be clickable, which is what a tab is. */
export function ColumnTab({ active, onClick, icon: Icon, label }) {
  return (
    <button
      type="button" onClick={onClick}
      className={`flex items-center justify-center gap-1.5 px-3 py-3 text-xs font-bold uppercase tracking-wide border-b-2 transition-colors ${
        active
          ? 'bg-white border-indigo-500 text-gray-800'
          : 'bg-gray-50 border-transparent text-gray-400 hover:text-gray-600 hover:bg-gray-100'
      }`}
    >
      <Icon className={`w-4 h-4 shrink-0 ${active ? 'text-indigo-500' : 'text-gray-400'}`} />
      <span className="truncate">{label}</span>
    </button>
  );
}


// The width a row's children ask for laid out on one line, whatever the row
// does with them now: stacked, the tabs wrap, so the row's own width says
// nothing about whether they would fit beside the controls.
function oneLineWidth(el) {
  const kids = [...el.children].filter((c) => c.offsetWidth > 0);
  if (!kids.length) return 0;
  const gap = parseFloat(getComputedStyle(el).columnGap) || 0;
  return kids.reduce((sum, c) => sum + c.offsetWidth, 0) + gap * (kids.length - 1);
}

/** A page's tab row: the tabs on the left, the open tab's controls on the
    right, on the line under both. When the screen is too narrow for the two,
    the controls move to a line of their own under the tabs, kept to the right, rather than
    squeezing the tabs into several short rows beside them. */
export function TabRow({ tabs, actions, navClassName = 'gap-2', className = '' }) {
  const rowRef = useRef(null);
  const navRef = useRef(null);
  const actionsRef = useRef(null);
  const [stacked, setStacked] = useState(false);
  const hasActions = !!actions;

  useLayoutEffect(() => {
    const row = rowRef.current;
    const nav = navRef.current;
    const act = actionsRef.current;
    if (!row || !nav || !act) { setStacked(false); return undefined; }
    const measure = () => {
      const controls = oneLineWidth(act);
      // Controls that render nothing yet (a toolbar portalled in later) leave
      // the tabs alone. 12 is the row's gap-3.
      setStacked(controls > 0 && oneLineWidth(nav) + 12 + controls > row.clientWidth);
    };
    measure();
    // Without ResizeObserver (jsdom) the row measures once and stays put.
    if (typeof ResizeObserver === 'undefined') return undefined;
    const resize = new ResizeObserver(measure);
    [row, nav, act].forEach((el) => resize.observe(el));
    // Controls come and go with the open tab, and once stacked they can
    // change width without anything here resizing.
    const mutate = new MutationObserver(measure);
    [nav, act].forEach((el) => mutate.observe(el, { childList: true, subtree: true, characterData: true }));
    return () => { resize.disconnect(); mutate.disconnect(); };
  }, [hasActions]);

  return (
    <div ref={rowRef} className={`${stacked ? '' : 'flex items-end justify-between gap-3 border-b border-gray-200'} ${className}`}>
      <div className={stacked ? 'border-b border-gray-200' : 'min-w-0'}>
        <nav ref={navRef} className={`flex flex-wrap -mb-px ${navClassName}`}>{tabs}</nav>
      </div>
      {hasActions && (
        <div
          ref={actionsRef}
          className={stacked ? 'flex flex-wrap items-center justify-end gap-2 pt-2 pb-4' : 'flex items-center gap-2 self-center shrink-0'}
        >
          {actions}
        </div>
      )}
    </div>
  );
}
