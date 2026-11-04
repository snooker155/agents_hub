import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useI18n } from '../i18n';

/**
 * A question before an action that cannot be taken back lightly: a title, a
 * line or two of what will happen, and the action's own button next to
 * Cancel. Drawn into <body>, so a panel with clipped overflow (the page chat)
 * does not cut it; Escape and the backdrop cancel.
 *
 * @param {string} props.title
 * @param {React.ReactNode} props.children what will happen
 * @param {string} props.confirmLabel the action's button
 * @param {string} props.cancelLabel
 * @param {function} props.onConfirm
 * @param {function} props.onCancel
 * @param {React.ComponentType} [props.icon] drawn on the action's button
 * @param {boolean} [props.danger] the action throws work away: a red button
 */
export default function ConfirmModal({ title, children, confirmLabel, cancelLabel, onConfirm, onCancel, icon: Icon, danger = false }) {
  const { t } = useI18n();
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onCancel(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel]);

  return createPortal(
    <div className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/40 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby="confirm-modal-title"
        className="w-full max-w-md rounded-2xl bg-white shadow-xl">
        <div className="flex items-start justify-between gap-3 px-5 pt-5">
          <h2 id="confirm-modal-title" className="text-base font-bold text-gray-900">{title}</h2>
          <button type="button" onClick={onCancel} aria-label={t('common.close')}
            className="text-gray-400 hover:text-gray-600"><X className="h-5 w-5" /></button>
        </div>
        <div className="px-5 pt-2 pb-5 text-sm leading-relaxed text-gray-600">{children}</div>
        <div className="flex justify-end gap-2 border-t border-gray-100 px-5 py-3">
          <button type="button" onClick={onCancel}
            className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
            {cancelLabel}
          </button>
          <button type="button" onClick={onConfirm} autoFocus
            className={`inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium text-white ${
              danger ? 'bg-red-600 hover:bg-red-700' : 'bg-indigo-600 hover:bg-indigo-700'}`}>
            {Icon && <Icon className="h-4 w-4" />} {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
