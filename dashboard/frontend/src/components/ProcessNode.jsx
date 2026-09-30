import { useLayoutEffect, useRef, useState } from 'react';
import { Maximize2 } from 'lucide-react';
import ProcessStepModal, { ToolCallDetail } from './ProcessStepModal';
import { useI18n } from '../i18n';

// One process step (input, output, thought, tool call) as a card: its label,
// then at most four lines of its content, faded out when there is more. A
// click opens the whole step in a modal; a tool call (`tool`) is shown there
// with its input and output parsed. Shared by the Chat/Task ProcessGraph, the
// message page and the session page, so every surface shows steps alike.
//
// `children` is the content, shown clamped on the card and in full in the
// modal; `detail` replaces it in the modal when the full form differs; `hint`
// stands in on the card when there are no children.
export default function ProcessNode({
  label, labelColor, borderColor, bgColor, hint, children, detail, tool,
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [clipped, setClipped] = useState(false);
  const previewRef = useRef(null);

  // Whether the four lines cut anything off: that decides the fade, and is
  // measured again when the card is resized (the side panel is resizable).
  useLayoutEffect(() => {
    const el = previewRef.current;
    if (!el) return undefined;
    const measure = () => setClipped(el.scrollHeight > el.clientHeight + 1);
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  });

  const preview = children ?? (hint ? <div className="text-[11px] text-gray-600">{hint}</div> : null);
  const modalBody = tool ? <ToolCallDetail tc={tool} /> : (detail ?? children ?? hint);

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        // A link or a button in the content (a markdown link, a code block's
        // copy button) does its own thing instead of opening the modal.
        onClick={(e) => { if (!e.target.closest('a, button')) setOpen(true); }}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(true); } }}
        title={t('processGraph.openFull')}
        className={`group rounded-lg border ${borderColor} ${bgColor} px-2.5 py-2 cursor-pointer transition-shadow hover:shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300`}
        data-testid="process-node"
      >
        <div className="flex items-center justify-between gap-2">
          <span className={`text-[11px] font-semibold truncate ${labelColor}`}>{label}</span>
          <Maximize2 className="w-3 h-3 text-gray-400 opacity-0 group-hover:opacity-100 shrink-0" />
        </div>
        {preview && (
          <div
            ref={previewRef}
            className="mt-1 leading-4 max-h-16 overflow-hidden"
            style={clipped ? { WebkitMaskImage: 'linear-gradient(to bottom, #000 70%, transparent)', maskImage: 'linear-gradient(to bottom, #000 70%, transparent)' } : undefined}
            data-testid="process-node-preview"
          >
            {preview}
          </div>
        )}
      </div>
      {open && (
        <ProcessStepModal title={label} labelColor={labelColor} onClose={() => setOpen(false)}>
          <div className="process-step-modal-body space-y-2">{modalBody}</div>
        </ProcessStepModal>
      )}
    </>
  );
}
