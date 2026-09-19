import { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';

// Expandable process step node: collapsed shows the label plus a one-line
// preview hint; expanded reveals the full, untruncated content. Shared by the
// message-page process view and the Chat/Task ProcessGraph so both render steps
// the same way.
export default function ProcessNode({ label, labelColor, borderColor, bgColor, hint, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`rounded-lg border ${borderColor} ${bgColor}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-2.5 py-2 text-left"
      >
        {open ? (
          <ChevronUp className="w-3.5 h-3.5 text-gray-400 shrink-0" />
        ) : (
          <ChevronDown className="w-3.5 h-3.5 text-gray-400 shrink-0" />
        )}
        <span className={`text-[11px] font-semibold shrink-0 ${labelColor}`}>{label}</span>
        {!open && hint && (
          <span className="text-[11px] text-gray-500 truncate">{hint}</span>
        )}
      </button>
      {open && <div className="px-2.5 pb-2.5 space-y-1">{children}</div>}
    </div>
  );
}
