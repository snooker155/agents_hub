// The full content of one process step (input, output, thought, tool call),
// opened from its card. Rendered into <body> so a side panel's overflow or
// stacking never clips it; Escape and a click outside close it.
//
// A tool call gets its arguments and its result parsed: JSON (or the Python
// literal a run log sometimes holds) is shown as a tree, anything else as
// text, with the raw string one click away.
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import ObjectTree from './ObjectTree';
import PolicyBadge from './PolicyBadge';
import { parseToolOutput } from './toolFormatters';
import { useI18n } from '../i18n';

function ParsedSection({ title, raw, tone }) {
  const { t } = useI18n();
  const [showRaw, setShowRaw] = useState(false);
  const { value: parsed, truncated } = parseToolOutput(raw);
  const structured = parsed !== null && typeof parsed === 'object';
  return (
    <section>
      <div className="flex items-center justify-between mb-1.5">
        <h4 className={`text-xs font-semibold ${tone}`}>{title}</h4>
        {structured && (
          <button
            type="button"
            onClick={() => setShowRaw((r) => !r)}
            className="text-[11px] text-indigo-600 hover:text-indigo-800"
          >
            {showRaw ? t('processGraph.parsed') : t('processGraph.raw')}
          </button>
        )}
      </div>
      {truncated && !showRaw && (
        <p className="mb-1.5 text-[11px] text-amber-700" data-testid="truncated-note">{t('processGraph.truncatedNote')}</p>
      )}
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 font-mono text-xs leading-5 overflow-x-auto">
        {structured && !showRaw
          ? <ObjectTree value={parsed} />
          : <pre className="whitespace-pre-wrap break-words font-mono">{String(raw ?? '')}</pre>}
      </div>
    </section>
  );
}

// A tool call's input and output, each parsed. think/plan echo their input as
// the output, so only one section is shown for them.
export function ToolCallDetail({ tc }) {
  const { t } = useI18n();
  const echo = tc.tool === 'think' || tc.tool === 'plan';
  return (
    <div className="space-y-4">
      {tc.evaluated_permission && (
        <div className="flex items-center gap-2 text-xs text-gray-500" data-testid="tool-call-policy">
          <span>{t('policyTrail.detail')}</span>
          <PolicyBadge tool={tc} withReason />
        </div>
      )}
      {!echo && tc.input != null && tc.input !== '' && (
        <ParsedSection title={t('processGraph.input')} raw={tc.input} tone="text-gray-700" />
      )}
      {(tc.output || (echo && tc.input)) ? (
        <ParsedSection title={t('processGraph.output')} raw={tc.output || tc.input} tone="text-emerald-700" />
      ) : (
        <p className="text-xs text-gray-400 italic">{t('processGraph.noOutput')}</p>
      )}
    </div>
  );
}

export default function ProcessStepModal({ title, labelColor = 'text-gray-800', onClose, children }) {
  const { t } = useI18n();
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return createPortal(
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4"
      // React events cross the portal into the tree that opened it: stop them
      // here so a click or a key in the modal never reaches a clickable card.
      onClick={(e) => { e.stopPropagation(); onClose(); }}
      onKeyDown={(e) => { e.stopPropagation(); if (e.key === 'Escape') onClose(); }}
      role="dialog"
      aria-modal="true"
      aria-label={title}
      data-testid="process-step-modal"
    >
      <div
        className="bg-white rounded-xl shadow-xl w-full max-w-3xl max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-gray-100">
          <h3 className={`text-sm font-semibold truncate ${labelColor}`}>{title}</h3>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label={t('common.close')}>
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="px-5 py-4 overflow-y-auto text-sm text-gray-800">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
