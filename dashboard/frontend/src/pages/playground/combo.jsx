import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';

/**
 * Pick one of the names something declared, or write one it has never heard of.
 *
 * Both, because both are real. Nearly every answer is one the world already
 * declared, and hunting for the spelling of one in a plain text field is how
 * the mismatches the validator reports got written in the first place — so the
 * list is one click away and looks like every other select on the page. But a
 * world half-written still has to be editable, and some fields take prose the
 * world could not have listed, so the value stays typeable and anything typed
 * is kept.
 *
 * Shared by the scenario form's role field and the world editor's effect rows:
 * the same question ("which of these, or something else?") deserves the same
 * control in both, and one of them having a dropdown while the other had a
 * bare input was the difference nobody could explain.
 */
export function Combo({
  value, options = [], placeholder, emptyLabel, onChange, className, disabled,
}) {
  const [open, setOpen] = useState(false);
  const box = useRef(null);

  // Pointer down rather than click: a click that lands on something which
  // moves or unmounts never reaches its target, and the list must close all
  // the same.
  useEffect(() => {
    if (!open) return undefined;
    const away = (e) => { if (!box.current?.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', away);
    return () => document.removeEventListener('mousedown', away);
  }, [open]);

  // What has been typed narrows the list — but never to nothing: a half-typed
  // or misspelled name is exactly when the real ones are worth seeing.
  const typed = (value || '').trim().toLowerCase();
  const narrowed = options.filter((o) => String(o).toLowerCase().includes(typed));
  const shown = narrowed.length ? narrowed : options;

  return (
    <div className="relative" ref={box}>
      <input
        value={value ?? ''}
        disabled={disabled}
        onChange={(e) => { onChange(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          // Escape closes the list. Only the list: the dialog around it also
          // closes on Escape, and losing a half-filled row because the
          // dropdown was open is not what anybody meant by that key.
          if (e.key === 'Escape' && open) { e.stopPropagation(); setOpen(false); }
          if (e.key === 'Enter' && open) { e.preventDefault(); setOpen(false); }
        }}
        placeholder={placeholder}
        className={`${className} ${options.length ? 'pr-7' : ''} ${
          disabled ? 'bg-gray-100 text-gray-400 cursor-not-allowed' : ''}`}
      />
      {options.length > 0 && !disabled && (
        <button
          type="button" tabIndex={-1}
          onMouseDown={(e) => { e.preventDefault(); setOpen((v) => !v); }}
          className="absolute inset-y-0 right-0 flex items-center px-1.5 text-gray-400 hover:text-gray-600"
        >
          <ChevronDown className={`w-4 h-4 transition-transform ${open ? 'rotate-180' : ''}`} />
        </button>
      )}

      {open && !disabled && (options.length > 0 || emptyLabel) && (
        <div className="absolute z-20 mt-1 w-full max-h-48 overflow-auto rounded-md border border-gray-200 bg-white py-1 shadow-lg">
          {/* Choosing nothing is a choice with consequences of its own, so it
              is an entry in the list rather than the absence of one — and the
              way to undo a pick without selecting all the text. */}
          {emptyLabel && (
            <button
              type="button"
              onClick={() => { onChange(''); setOpen(false); }}
              className={`w-full text-left px-2 py-1.5 text-sm hover:bg-indigo-50 ${
                typed ? 'text-gray-500' : 'bg-indigo-50 font-semibold text-indigo-700'
              }`}
            >
              {emptyLabel}
            </button>
          )}
          {shown.map((option) => (
            <button
              key={option} type="button"
              onClick={() => { onChange(option); setOpen(false); }}
              className={`w-full text-left px-2 py-1.5 text-sm hover:bg-indigo-50 ${
                String(option).toLowerCase() === typed
                  ? 'bg-indigo-50 font-semibold text-indigo-700' : 'text-gray-700'
              }`}
            >
              {option}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default Combo;
