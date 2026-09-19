// Rendering for structured-memory slot values. A slot's data is free-form
// JSON, so one value can be anything from a word to a nested object — and
// agents often store an object as a JSON *string*. Scalars are truncated to a
// chip (click for the full text), and anything JSON-shaped is rendered as a
// collapsible tree rather than a wall of escaped text.
//
// Nothing here sets a font size: the components inherit it from the caller so
// the same tree fits both the chat cards and the Shared Memory pages.
import { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { SLOT_VALUE_MAX, SLOT_CHILD_LIMIT, coerceSlotValue, isSlotContainer, slotScalarText, slotContainerSummary } from './slotUtils';
import { useI18n } from '../i18n';

function SlotScalar({ label, value, max = SLOT_VALUE_MAX }) {
  const [open, setOpen] = useState(false);
  const text = slotScalarText(value);
  const long = text.length > max;
  const body = long && !open ? `${text.slice(0, max)}…` : text;
  return (
    <span
      onClick={long ? () => setOpen(o => !o) : undefined}
      title={long && !open ? text : undefined}
      className={`font-mono text-gray-700 ${label != null ? 'px-1.5 py-0.5 rounded bg-gray-100' : ''} ${
        long ? 'cursor-pointer hover:bg-gray-200' : ''
      } ${open ? 'block whitespace-pre-wrap break-words' : 'max-w-full'}`}
    >
      {label != null && <span className="text-gray-500">{label}: </span>}
      {body}
    </span>
  );
}

function SlotContainer({ label, value, depth }) {
  const { t } = useI18n();
  // Only the outermost object is expanded, so a deep blob stays list-sized.
  const [open, setOpen] = useState(depth === 0);
  const [showAll, setShowAll] = useState(false);
  const entries = Array.isArray(value)
    ? value.map((item, i) => [String(i), item])
    : Object.entries(value);
  const shown = showAll ? entries : entries.slice(0, SLOT_CHILD_LIMIT);
  const Chevron = open ? ChevronUp : ChevronDown;
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1 font-mono text-gray-700 hover:text-gray-900"
      >
        <Chevron className="w-3 h-3 text-gray-400" />
        {label != null && <span className="text-gray-500">{label}</span>}
        <span className="text-gray-400">{slotContainerSummary(value)}</span>
      </button>
      {open && (
        <div className="mt-0.5 ml-1.5 pl-2 border-l border-gray-200 space-y-1">
          {entries.length === 0 && <div className="font-mono text-gray-400">{t('slotValue.empty')}</div>}
          {shown.map(([k, v]) => <SlotValue key={k} label={k} value={v} depth={depth + 1} />)}
          {entries.length > shown.length && (
            <button
              type="button"
              onClick={() => setShowAll(true)}
              className="text-indigo-600 hover:text-indigo-800"
            >
              +{entries.length - shown.length} more
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// One slot value: a truncatable chip, or a tree when the value is (or encodes)
// JSON. Pass label={null} where the key is already shown by the caller.
export function SlotValue({ label = null, value, max = SLOT_VALUE_MAX, depth = 0 }) {
  const v = coerceSlotValue(value);
  return isSlotContainer(v)
    ? <SlotContainer label={label} value={v} depth={depth} />
    : <SlotScalar label={label} value={v} max={max} />;
}

// A whole slot's data: scalars flow as chips on one row, JSON values get their
// own full-width tree underneath.
export function SlotData({ data, className = 'mt-1 space-y-1' }) {
  const entries = Object.entries(data || {}).map(([k, v]) => [k, coerceSlotValue(v)]);
  if (!entries.length) return null;
  const scalars = entries.filter(([, v]) => !isSlotContainer(v));
  const containers = entries.filter(([, v]) => isSlotContainer(v));
  return (
    <div className={className}>
      {scalars.length > 0 && (
        <div className="flex flex-wrap items-start gap-1">
          {scalars.map(([k, v]) => <SlotValue key={k} label={k} value={v} />)}
        </div>
      )}
      {containers.map(([k, v]) => <SlotValue key={k} label={k} value={v} />)}
    </div>
  );
}
