import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, Search, Wrench, X } from 'lucide-react';
import { useI18n } from '../../i18n';
import { toolCategoryLabel, toolDescription, toolLabel } from '../../lib/toolText';

/**
 * Multi select for an agent's tools: the chosen ones as chips, and a panel
 * that lists every tool by group with a search over the tool's name, id and
 * group. A whole group can be picked or cleared at once.
 *
 * The panel opens in the flow of the page, not as a floating layer, so it
 * stays visible inside a scrolling dialog.
 *
 * `tools` is the `/api/agents/tools` list ({name, label, category,
 * description}); `value` is the list of chosen tool ids.
 */
export default function ToolPicker({ tools, value, onChange }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const rootRef = useRef(null);
  const inputRef = useRef(null);

  const selected = useMemo(() => new Set(value || []), [value]);
  // Each tool with its name and description in the interface language.
  const byId = useMemo(() => {
    const out = {};
    (tools || []).forEach((tool) => {
      out[tool.name] = {
        ...tool,
        label: toolLabel(t, tool.name, tool.label),
        description: toolDescription(t, tool.name, tool.description),
      };
    });
    return out;
  }, [tools, t]);

  const categoryLabel = (c) => toolCategoryLabel(t, c);

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = {};
    Object.values(byId).forEach((tool) => {
      const cat = tool.category || 'other';
      const groupName = categoryLabel(cat);
      const hit = !q
        || tool.name.toLowerCase().includes(q)
        || (tool.label || '').toLowerCase().includes(q)
        || cat.toLowerCase().includes(q)
        || groupName.toLowerCase().includes(q);
      if (!hit) return;
      (out[cat] ||= { category: cat, label: groupName, tools: [] }).tools.push(tool);
    });
    return Object.values(out)
      .map((g) => ({ ...g, tools: g.tools.sort((a, b) => (a.label || a.name).localeCompare(b.label || b.name)) }))
      .sort((a, b) => a.label.localeCompare(b.label));
    // categoryLabel follows the language, which t carries.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [byId, query, t]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const toggle = (id) => {
    onChange(selected.has(id) ? value.filter((x) => x !== id) : [...(value || []), id]);
  };

  const setGroup = (ids, on) => {
    if (on) onChange([...(value || []), ...ids.filter((id) => !selected.has(id))]);
    else onChange((value || []).filter((id) => !ids.includes(id)));
  };

  const openPanel = () => {
    setOpen(true);
    queueMicrotask(() => inputRef.current?.focus());
  };

  return (
    <div ref={rootRef} className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {(value || []).length === 0 && (
          <span className="text-xs text-gray-400">{t('toolPicker.none')}</span>
        )}
        {(value || []).map((id) => (
          <span key={id} className="inline-flex items-center gap-1 max-w-full break-words bg-indigo-50 border border-indigo-200 text-indigo-700 text-xs pl-2 pr-1 py-0.5 rounded-full" title={byId[id]?.description || id}>
            {byId[id]?.label || id}
            <button
              type="button"
              onClick={() => toggle(id)}
              aria-label={t('toolPicker.remove', { name: byId[id]?.label || id })}
              className="p-0.5 rounded-full text-indigo-400 hover:text-indigo-700 hover:bg-indigo-100"
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        ))}
      </div>

      <div className={`rounded-xl border bg-white transition-colors ${open ? 'border-indigo-300 shadow-sm' : 'border-gray-300'}`}>
        <div className="flex items-center gap-2 px-3 py-2">
          <Search className="w-4 h-4 text-gray-400 flex-shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
            onFocus={() => setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); }
              if (e.key === 'Enter') {
                e.preventDefault();
                const first = groups[0]?.tools[0];
                if (first) toggle(first.name);
              }
            }}
            placeholder={t('toolPicker.search')}
            className="flex-1 min-w-0 text-sm bg-transparent outline-none border-0 p-0 focus:ring-0"
            aria-label={t('toolPicker.search')}
          />
          {(value || []).length > 0 && (
            <span className="text-[11px] text-gray-400 whitespace-nowrap">{t('toolPicker.selected', { count: value.length })}</span>
          )}
          <button
            type="button"
            onClick={() => (open ? setOpen(false) : openPanel())}
            className="p-1 rounded text-gray-400 hover:text-gray-600"
            aria-label={open ? t('toolPicker.done') : t('toolPicker.add')}
            aria-expanded={open}
          >
            <ChevronDown className={`w-4 h-4 transition-transform ${open ? 'rotate-180' : ''}`} />
          </button>
        </div>

        {open && (
          <div className="border-t border-gray-100">
            <div className="max-h-72 overflow-y-auto py-1" role="listbox" aria-multiselectable="true">
              {groups.length === 0 && (
                <p className="px-3 py-6 text-center text-xs text-gray-400">{t('toolPicker.nothingFound', { q: query.trim() })}</p>
              )}
              {groups.map((g) => {
                const ids = g.tools.map((tool) => tool.name);
                const onCount = ids.filter((id) => selected.has(id)).length;
                const allOn = onCount === ids.length;
                return (
                  <div key={g.category} className="py-1">
                    <div className="sticky top-0 z-[1] flex items-center justify-between gap-2 px-3 py-1 bg-white/95 backdrop-blur">
                      <span className="text-[11px] font-bold uppercase tracking-wide text-gray-500 truncate">
                        {g.label}
                        <span className="ml-1.5 font-medium normal-case tracking-normal text-gray-400">{onCount}/{ids.length}</span>
                      </span>
                      <button
                        type="button"
                        onClick={() => setGroup(ids, !allOn)}
                        className="text-[11px] font-medium text-indigo-600 hover:text-indigo-800 whitespace-nowrap"
                      >
                        {allOn ? t('toolPicker.clearAll') : t('toolPicker.selectAll')}
                      </button>
                    </div>
                    {g.tools.map((tool) => {
                      const on = selected.has(tool.name);
                      return (
                        <button
                          key={tool.name}
                          type="button"
                          role="option"
                          aria-selected={on}
                          onClick={() => toggle(tool.name)}
                          className={`w-full text-left flex items-start gap-2.5 px-3 py-1.5 transition-colors ${on ? 'bg-indigo-50/60' : 'hover:bg-gray-50'}`}
                        >
                          <span className={`mt-0.5 w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 transition-colors ${on ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-gray-300 bg-white'}`}>
                            {on && <Check className="w-3 h-3" />}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="flex flex-wrap items-baseline gap-x-2 min-w-0">
                              <span className={`text-sm break-words min-w-0 ${on ? 'font-semibold text-gray-900' : 'text-gray-800'}`}>{tool.label || tool.name}</span>
                              <span className="text-[10px] font-mono text-gray-400 break-all">{tool.name}</span>
                            </span>
                            {tool.description && (
                              <span className="block text-[11px] text-gray-500 line-clamp-2">{tool.description}</span>
                            )}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                );
              })}
            </div>
            <div className="flex items-center justify-between gap-2 px-3 py-2 border-t border-gray-100">
              <span className="inline-flex items-center gap-1.5 text-[11px] text-gray-400">
                <Wrench className="w-3 h-3" />
                {t('toolPicker.selected', { count: (value || []).length })}
              </span>
              <button
                type="button"
                onClick={() => { setOpen(false); setQuery(''); }}
                className="px-3 py-1 rounded-lg text-xs font-semibold bg-indigo-600 text-white hover:bg-indigo-700"
              >
                {t('toolPicker.done')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
