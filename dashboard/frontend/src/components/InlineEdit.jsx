import { useEffect, useRef, useState } from 'react';
import { Check, Edit2, X } from 'lucide-react';
import { useI18n } from '../i18n';

/**
 * Text that is read until you click it, and a field once you do.
 *
 * The pattern a page header wants for the one thing the page is named after:
 * no dialog, no edit mode for the whole page, and no form that has to be saved
 * as a whole. Enter commits, Escape reverts, and a value that did not change
 * never reaches ``onSave`` — so clicking a title and clicking away is not a
 * write.
 *
 * ``onSave`` may be async and may throw: the caller reports the failure the way
 * that page reports failures, and the editor closes either way, leaving the
 * value the caller's state actually holds.
 */
export default function InlineEdit({ value, onSave, multiline = false, className = '' }) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  // What `draft` was last derived from, so a change is noticed without an
  // effect: this is state derived from a prop during render (the pattern
  // React recommends over an effect for "adjust state when a prop changes"),
  // and calling setState here re-renders immediately with the new draft
  // instead of committing a stale one first.
  const [syncedValue, setSyncedValue] = useState(value);
  const ref = useRef(null);

  // The stored value can change under an editor that is not open — another tab,
  // a chat that edits the same entity — and the next click should start from
  // what is stored rather than from what was there when the page loaded.
  if (!editing && value !== syncedValue) {
    setSyncedValue(value);
    setDraft(value);
  }

  useEffect(() => { if (editing) ref.current?.focus(); }, [editing]);

  const commit = async () => {
    if (draft !== value) await onSave(draft);
    setEditing(false);
  };

  const cancel = () => { setDraft(value); setEditing(false); };

  if (!editing) {
    return (
      <span
        className={`group cursor-pointer hover:bg-gray-50 rounded px-1 -mx-1 transition-colors ${className}`}
        onClick={() => setEditing(true)}
        title={t('components.inlineEdit.clickToEdit')}
      >
        {value || <span className="text-gray-400 italic">{t('components.inlineEdit.clickToAdd')}</span>}
        <Edit2 className="inline w-3 h-3 ml-1 text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity" />
      </span>
    );
  }

  return (
    <span className="flex items-start gap-1">
      {multiline ? (
        <textarea
          ref={ref}
          className={`border border-indigo-300 rounded px-2 py-1 text-sm resize-none w-full focus:outline-none focus:ring-1 focus:ring-indigo-400 ${className}`}
          rows={4}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Escape') cancel(); if (e.key === 'Enter' && e.metaKey) commit(); }}
        />
      ) : (
        <input
          ref={ref}
          type="text"
          className={`border border-indigo-300 rounded px-2 py-1 text-sm w-full focus:outline-none focus:ring-1 focus:ring-indigo-400 ${className}`}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') cancel(); }}
        />
      )}
      <button onClick={commit} className="p-1 text-green-600 hover:bg-green-50 rounded mt-0.5"><Check className="w-3.5 h-3.5" /></button>
      <button onClick={cancel} className="p-1 text-gray-400 hover:bg-gray-100 rounded mt-0.5"><X className="w-3.5 h-3.5" /></button>
    </span>
  );
}
