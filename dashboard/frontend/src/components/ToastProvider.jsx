/**
 * Holds the live toast list and renders it in the corner of the page.
 *
 * Auto-dismissal is driven by a timer started in the push handler rather than by
 * an effect, so a toast never needs a render pass to schedule its own removal.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, X } from 'lucide-react';
import { ToastContext } from './toast';
import { useI18n } from '../i18n';

const DISMISS_MS = 6000;
const MAX_VISIBLE = 3;   // older toasts drop off rather than filling the screen

const TONES = {
  error: {
    icon: AlertTriangle,
    box: 'border-red-200 bg-red-50',
    title: 'text-red-800',
    detail: 'text-red-700',
    iconColor: 'text-red-500',
  },
  success: {
    icon: CheckCircle2,
    box: 'border-green-200 bg-green-50',
    title: 'text-green-800',
    detail: 'text-green-700',
    iconColor: 'text-green-500',
  },
};

function Toast({ toast, onDismiss }) {
  const { t } = useI18n();
  const tone = TONES[toast.tone] || TONES.error;
  const Icon = tone.icon;
  return (
    <div
      className={`pointer-events-auto flex items-start gap-2.5 rounded-xl border ${tone.box} px-3.5 py-3 shadow-lg`}
    >
      <Icon className={`w-4 h-4 shrink-0 mt-0.5 ${tone.iconColor}`} />
      <div className="min-w-0 flex-1">
        <p className={`text-sm font-semibold ${tone.title}`}>{toast.message}</p>
        {toast.detail && (
          <p className={`mt-0.5 text-xs break-words ${tone.detail}`}>{toast.detail}</p>
        )}
      </div>
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        aria-label={t('common.dismiss')}
        className={`shrink-0 rounded p-0.5 opacity-60 hover:opacity-100 ${tone.title}`}
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
    dismiss,
  }), [push, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
      >
        {toasts.map((toast) => <Toast key={toast.id} toast={toast} onDismiss={dismiss} />)}
      </div>
    </ToastContext.Provider>
  );
}
