import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Check, ChevronDown, FolderOpen, Search, Settings2 } from 'lucide-react';
import { useI18n } from '../i18n';

/**
 * The header's workspace switch: a button with the current workspace, and a
 * panel that lists every workspace with a search over the names. Arrow keys
 * move, Enter picks, Escape closes.
 *
 * `workspaces` is `GET /api/workspaces` ({name, tasks_count, attached});
 * `label(ws)` is the shown name (the default workspace has a caption of its
 * own); `onChange(name)` switches.
 */
export default function WorkspacePicker({ workspaces, value, onChange, label, compact = false }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const rootRef = useRef(null);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  const current = workspaces.find((ws) => ws.name === value) || null;

  // The default workspace first, the rest by name.
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const sorted = [...workspaces].sort((a, b) => (
      (a.name === 'default' ? -1 : b.name === 'default' ? 1 : 0)
      || a.name.localeCompare(b.name)
    ));
    return q ? sorted.filter((ws) => `${ws.name} ${label(ws)}`.toLowerCase().includes(q)) : sorted;
  }, [workspaces, query, label]);

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
    setQuery('');
    setActive(Math.max(0, matches.findIndex((ws) => ws.name === value)));
    setOpen(true);
    queueMicrotask(() => inputRef.current?.focus());
  };

  const pick = (ws) => {
    setOpen(false);
    if (ws && ws.name !== value) onChange(ws.name);
  };

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(matches.length - 1, i + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(matches[active]); }
    else if (e.key === 'Escape') { e.preventDefault(); setOpen(false); }
  };

  return (
    <div ref={rootRef} className="relative min-w-0">
      <button
        type="button"
        onClick={() => (open ? setOpen(false) : openPanel())}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={t('layout.workspaceLabel')}
        className={`flex items-center gap-2 min-w-0 rounded-lg border px-3 py-1.5 text-sm font-semibold transition-colors ${
          open ? 'border-indigo-300 bg-indigo-50/50' : 'border-gray-200 bg-gray-50 hover:bg-white hover:border-gray-300'
        }`}
      >
        <span className={`truncate text-gray-800 ${compact ? 'max-w-[7rem]' : 'max-w-[14rem]'}`}>
          {current ? label(current) : '—'}
        </span>
        <ChevronDown className={`w-3.5 h-3.5 text-gray-400 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="header-sheet absolute left-0 top-full mt-1.5 w-72 bg-white border border-gray-200 rounded-xl shadow-lg z-50 overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-100">
            <Search className="w-4 h-4 text-gray-400 shrink-0" />
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => { setQuery(e.target.value); setActive(0); }}
              onKeyDown={onKeyDown}
              placeholder={t('layout.workspacePicker.search')}
              aria-label={t('layout.workspacePicker.search')}
              className="flex-1 min-w-0 text-sm bg-transparent outline-none border-0 p-0 focus:ring-0"
            />
            <span className="text-[11px] text-gray-400 tabular-nums">{matches.length}</span>
          </div>

          <div ref={listRef} role="listbox" aria-label={t('layout.workspaceLabel')} className="max-h-80 overflow-y-auto py-1">
            {matches.length === 0 && (
              <p className="px-3 py-6 text-center text-xs text-gray-400">
                {t('layout.workspacePicker.noMatch', { q: query.trim() })}
              </p>
            )}
            {matches.map((ws, i) => {
              const selected = ws.name === value;
              return (
                <button
                  key={ws.name}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  data-index={i}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => pick(ws)}
                  className={`w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors ${
                    i === active ? 'bg-gray-50' : ''
                  }`}
                >
                  <span className="min-w-0 flex-1">
                    <span className={`block text-sm truncate ${selected ? 'font-semibold text-gray-900' : 'text-gray-800'}`}>
                      {label(ws)}
                    </span>
                    <span className="flex items-center gap-2 text-[11px] text-gray-400">
                      <span>{t('layout.workspacePicker.tasks', { count: ws.tasks_count ?? 0 })}</span>
                      {ws.attached && (
                        <span className="inline-flex items-center gap-1" title={ws.target || undefined}>
                          <FolderOpen className="w-3 h-3" /> {t('layout.workspacePicker.attached')}
                        </span>
                      )}
                    </span>
                  </span>
                  {selected && <Check className="w-4 h-4 text-indigo-600 shrink-0" />}
                </button>
              );
            })}
          </div>

          <Link
            to="/workspaces"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 px-3 py-2 text-xs font-medium text-gray-600 border-t border-gray-100 hover:bg-gray-50 hover:text-indigo-700"
          >
            <Settings2 className="w-3.5 h-3.5" /> {t('layout.workspacePicker.manage')}
          </Link>
        </div>
      )}
    </div>
  );
}
