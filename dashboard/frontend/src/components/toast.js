/**
 * Transient, non-blocking feedback for things that failed (or worked) while the
 * user was looking somewhere else.
 *
 * Split the way the i18n layer is split — the context and its hook live in this
 * plain module so `ToastProvider.jsx` exports nothing but components and keeps
 * fast refresh working.
 *
 * This is for *reporting*, not for flow control: a toast never blocks, never
 * asks a question, and disappears on its own. Anything the user must answer
 * still belongs in a dialog.
 */
import { createContext, useContext } from 'react';

export const ToastContext = createContext({
  error: () => {},
  success: () => {},
  dismiss: () => {},
});

/** `const toast = useToast(); toast.error(t('…'), errorDetail(e));` */
export const useToast = () => useContext(ToastContext);

/**
 * The most specific thing an axios failure can tell the user: FastAPI's
 * `detail` when the server answered, the transport error when it did not.
 * Returns undefined when there is nothing worth showing.
 */
export function errorDetail(err) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  // FastAPI validation errors arrive as a list of {loc, msg, type}.
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => d?.msg).filter(Boolean);
    if (msgs.length) return msgs.join('; ');
  }
  const message = err?.message;
  return typeof message === 'string' && message.trim() ? message.trim() : undefined;
}
