/**
 * Holds the live toast list and renders it in the corner of the page.
 *
 * Auto-dismissal is driven by a timer started in the push handler rather than by
 * an effect, so a toast never needs a render pass to schedule its own removal.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Bell, CheckCircle2, X } from 'lucide-react';
import { ToastContext } from './toast';
import { useI18n } from '../i18n';

const DISMISS_MS = 6000;
const MAX_VISIBLE = 3;   // older toasts drop off rather than filling the screen

// A solid tone with white text, like the buttons of the same colours: a
// tinted box with text in a darker shade of the same hue blended into it.
const TONES = {
  error: { icon: AlertTriangle, box: 'bg-red-600' },
  success: { icon: CheckCircle2, box: 'bg-green-600' },
  // Something arrived (a new notification), neither a failure nor a result.
  info: { icon: Bell, box: 'bg-indigo-600' },
};

function Toast({ toast, onDismiss }) {
  const { t } = useI18n();
  const tone = TONES[toast.tone] || TONES.error;
  const Icon = tone.icon;
  // A long message shows its first lines only; the whole of it is on hover.
  // The text colour is set once, on the fill itself: the neutral dark theme
  // turns the label on a light brand fill dark (theme.css), and the children
  // inherit whichever it is.
  return (
    <div
      className={`pointer-events-auto flex items-start gap-2.5 rounded-xl ${tone.box} px-3.5 py-3 shadow-xl text-white`}
      title={[toast.message, toast.detail].filter(Boolean).join('\n\n')}
    >
      <Icon className="w-4 h-4 shrink-0 mt-0.5" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold break-words line-clamp-2">{toast.message}</p>
        {toast.detail && (
          <p className="mt-0.5 text-xs break-words opacity-90 line-clamp-3">{toast.detail}</p>
        )}
      </div>
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        aria-label={t('common.dismiss')}
        className="shrink-0 rounded p-0.5 opacity-70 hover:opacity-100"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

export default function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(0);
  const timers = useRef(new Set());

  const dismiss = useCallback((id) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback((tone, message, detail) => {
    if (!message) return undefined;
    nextId.current += 1;
    const id = nextId.current;
    setToasts((prev) => [...prev, { id, tone, message, detail }].slice(-MAX_VISIBLE));
    const timer = setTimeout(() => {
      timers.current.delete(timer);
      dismiss(id);
    }, DISMISS_MS);
    timers.current.add(timer);
    return id;
  }, [dismiss]);

  // Leaving the page mid-countdown must not leave timers pointing at a dead tree.
  useEffect(() => {
    const pending = timers.current;
    return () => { pending.forEach(clearTimeout); pending.clear(); };
  }, []);

  const value = useMemo(() => ({
    error: (message, detail) => push('error', message, detail),
    success: (message, detail) => push('success', message, detail),
    info: (message, detail) => push('info', message, detail),
    dismiss,
  }), [push, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* Top right, above everything: the largest z-index a browser keeps. */}
      <div
        aria-live="polite"
        data-testid="toasts"
        className="pointer-events-none fixed top-4 right-4 z-[2147483647] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
      >
        {toasts.map((toast) => <Toast key={toast.id} toast={toast} onDismiss={dismiss} />)}
      </div>
    </ToastContext.Provider>
  );
}
