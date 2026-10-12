// Tabs the playground's scenario page introduced and the flow run page
// shares (pages/playground/scenario-sidebar.jsx re-exports them):
//
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
