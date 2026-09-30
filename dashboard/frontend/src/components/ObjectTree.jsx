// A parsed value shown in full: objects and arrays as an indented, foldable
// tree, strings whole (line breaks kept), and a string that holds JSON shown
// as the object it encodes. Built for the process step modal, where nothing
// may be cut short; SlotValue is the compact form for cards.
import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { coerceSlotValue, isSlotContainer, slotContainerSummary } from './slotUtils';

// Levels open on first show; deeper ones start folded so a huge blob stays readable.
const OPEN_DEPTH = 3;

function Scalar({ value }) {
  if (value === null || value === undefined) return <span className="text-gray-400">null</span>;
  if (typeof value === 'boolean') return <span className="text-violet-700">{String(value)}</span>;
  if (typeof value === 'number') return <span className="text-sky-700">{value}</span>;
  return <span className="text-gray-800 whitespace-pre-wrap break-words">{String(value)}</span>;
}

function Container({ label, value, depth }) {
  const [open, setOpen] = useState(depth < OPEN_DEPTH);
  const entries = Array.isArray(value) ? value.map((v, i) => [String(i), v]) : Object.entries(value);
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 text-left hover:text-gray-900"
        aria-expanded={open}
      >
        <Chevron className="w-3 h-3 text-gray-400 shrink-0" />
        {label != null && <span className="text-gray-500">{label}</span>}
        <span className="text-gray-400">{slotContainerSummary(value)}</span>
      </button>
      {open && (
        <div className="ml-1.5 pl-3 border-l border-gray-200 space-y-0.5">
          {entries.map(([k, v]) => <ObjectTree key={k} label={k} value={v} depth={depth + 1} />)}
        </div>
      )}
    </div>
  );
}

export default function ObjectTree({ label = null, value, depth = 0 }) {
  const v = coerceSlotValue(value);
  const empty = isSlotContainer(v) && Object.keys(v).length === 0;
  if (isSlotContainer(v) && !empty) return <Container label={label} value={v} depth={depth} />;
  // An empty object or array has nothing to fold: it reads as `{}` / `[]`.
  const shown = empty ? <span className="text-gray-400">{Array.isArray(v) ? '[]' : '{}'}</span> : <Scalar value={v} />;
  if (label == null) return shown;
  return (
    <div className="flex items-start gap-1.5">
      <span className="text-gray-500 shrink-0">{label}:</span>
      {shown}
    </div>
  );
}
