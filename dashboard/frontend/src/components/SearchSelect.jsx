import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, Search } from 'lucide-react';

/**
 * A form field that picks one item from a list with a search over it: a
 * button with the picked item, and a panel with a search box and the rows.
 * Arrow keys move, Enter picks, Escape closes. With nothing to pick, or with
 * `disabled`, the field is inactive and shows `emptyText` instead.
 *
 * `items` are {id, name, subtitle?, dot?, disabled?, hint?}; the search
 * matches the name, the id and the subtitle. `dot` is a colour class for a
 * small mark before the name; a `disabled` row is shown but cannot be picked,
 * with `hint` as its tooltip. `invalid` draws the field's border in amber, for
 * a pick the form still needs. `singleLine` keeps the field one line high,
 * the picked item's subtitle left to the list, for a field in a row of inputs.
 */
export default function SearchSelect({
  items, value, onChange, placeholder, searchPlaceholder, noMatchText, emptyText = '',
  disabled = false, invalid = false, singleLine = false, testId,
}) {
  const [panelOpen, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const rootRef = useRef(null);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  const inactive = disabled || items.length === 0;
  const open = panelOpen && !inactive;
  const current = items.find((i) => i.id === value) || null;

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((i) => `${i.name} ${i.id} ${i.subtitle || ''}`.toLowerCase().includes(q));
  }, [items, query]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  // Keep the highlighted row in view while arrowing through a long list.
  useEffect(() => {
    if (!open) return;
    const row = listRef.current?.querySelector(`[data-index="${active}"]`);
    if (row && typeof row.scrollIntoView === 'function') row.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  const openPanel = () => {
    if (inactive) return;
    setQuery('');
    setActive(Math.max(0, items.findIndex((i) => i.id === value)));
    setOpen(true);
    queueMicrotask(() => inputRef.current?.focus());
  };

  const pick = (item) => {
    if (item?.disabled) return;
    setOpen(false);
    if (item) onChange(item.id);
  };

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(matches.length - 1, i + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(matches[active]); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setOpen(false); }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        data-testid={testId}
        onClick={() => (open ? setOpen(false) : openPanel())}
        disabled={inactive}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={`w-full flex items-center gap-2 border rounded-md px-3 py-2 text-sm text-left transition-colors ${
          inactive
            ? 'border-gray-200 bg-gray-50 text-gray-400 cursor-not-allowed'
            : open
              ? 'border-indigo-300 bg-white'
              : invalid
                ? 'border-amber-400 bg-white hover:border-amber-500'
                : 'border-gray-300 bg-white hover:border-gray-400'
        }`}
      >
        <span className="flex-1 min-w-0">
          {current ? (
            <>
              <span className="block truncate text-gray-800">{current.name}</span>
              {current.subtitle && !singleLine && <span className="block truncate text-xs text-gray-400">{current.subtitle}</span>}
            </>
          ) : (
            <span className="block truncate text-gray-400">{inactive && emptyText ? emptyText : placeholder}</span>
          )}
        </span>
        <ChevronDown className={`w-4 h-4 text-gray-400 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute left-0 right-0 top-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-50 overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-100">
            <Search className="w-4 h-4 text-gray-400 shrink-0" />
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => { setQuery(e.target.value); setActive(0); }}
              onKeyDown={onKeyDown}
              placeholder={searchPlaceholder}
              aria-label={searchPlaceholder}
              className="flex-1 min-w-0 text-sm bg-transparent outline-none border-0 p-0 focus:ring-0"
            />
            <span className="text-[11px] text-gray-400 tabular-nums">{matches.length}</span>
          </div>

          <div ref={listRef} role="listbox" className="max-h-64 overflow-y-auto py-1">
            {matches.length === 0 && (
              <p className="px-3 py-6 text-center text-xs text-gray-400">{noMatchText}</p>
            )}
            {matches.map((item, i) => {
              const selected = item.id === value;
              return (
                <button
                  key={item.id}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  aria-disabled={item.disabled || undefined}
                  title={item.hint || undefined}
                  data-index={i}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => pick(item)}
                  className={`w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors ${
                    item.disabled ? 'opacity-40 cursor-not-allowed' : ''
                  } ${i === active ? 'bg-gray-50' : ''}`}
                >
                  {item.dot && <span className={`w-2 h-2 rounded-full shrink-0 ${item.dot}`} />}
                  <span className="min-w-0 flex-1">
                    <span className={`block text-sm truncate ${selected ? 'font-semibold text-gray-900' : 'text-gray-800'}`}>
                      {item.name}
                    </span>
                    {item.subtitle && <span className="block text-[11px] text-gray-400 truncate">{item.subtitle}</span>}
                  </span>
                  {selected && <Check className="w-4 h-4 text-indigo-600 shrink-0" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
