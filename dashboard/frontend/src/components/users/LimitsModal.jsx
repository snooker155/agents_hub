/**
 * Users, Limits: one person's own limits in place of the hub-wide ones
 * (PATCH /api/auth/users/{id}): the monthly spend (common/user_budget.py),
 * requests per minute and tokens per day (common/rate_limit.py, migration
 * 0056). Empty follows the default shown as the placeholder, 0 is no limit.
 * A personal key's own request and token limits still win over these.
 */
import { useEffect, useState } from 'react';
import { Loader, X } from 'lucide-react';
import { useI18n } from '../../i18n';
import { updateUser } from '../../api';

const inputCls = 'w-full border border-gray-200 rounded-lg px-3 py-2 text-sm '
  + 'focus:outline-none focus:ring-2 focus:ring-indigo-300';

// field: [label key, step, whole numbers only, default from `defaults`]
const FIELDS = [
  ['spend_limit_usd', 'auth.users.limits.spend', '0.01', false],
  ['rate_limit_per_minute', 'auth.users.limits.perMinute', '1', true],
  ['tokens_per_day', 'auth.users.limits.tokensPerDay', '1', true],
];

const asDraft = (value) => (value == null ? '' : String(value));

/** `defaults`: `{ spend_limit_usd, rate_limit_per_minute, tokens_per_day }`, the hub-wide values. */
export default function LimitsModal({ user, defaults = {}, onClose, onSaved }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState(() => Object.fromEntries(
    FIELDS.map(([key]) => [key, asDraft(user[key])]),
  ));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const save = async (event) => {
    event.preventDefault();
    const payload = {};
    for (const [key, , , whole] of FIELDS) {
      const raw = String(draft[key]).trim().replace(',', '.');
      const value = raw === '' ? null : Number(raw);
      if (value !== null && (!Number.isFinite(value) || value < 0 || (whole && !Number.isInteger(value)))) {
        setError(t(whole ? 'auth.users.limits.invalidCount' : 'auth.users.limitInvalid'));
        return;
      }
      payload[key] = value;
    }
    setBusy(true);
    setError('');
    try {
      await updateUser(user.id, payload);
      await onSaved();
      onClose();
    } catch (err) {
      setError(err?.response?.data?.detail || t('auth.users.updateFailed'));
      setBusy(false);
    }
  };

  const placeholder = (key) => {
    const value = Number(defaults[key] || 0);
    if (!value) return t('auth.users.limits.defaultNone');
    return t('auth.users.limits.defaultIs', {
      value: key === 'spend_limit_usd' ? `$${value.toFixed(2)}` : value.toLocaleString(),
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40" onMouseDown={onClose}>
      <form onSubmit={save} onMouseDown={(e) => e.stopPropagation()} data-testid="limits-modal"
        role="dialog" aria-modal="true" aria-labelledby="limits-title"
        className="w-full max-w-md bg-white rounded-2xl shadow-2xl overflow-hidden">
        <div className="flex items-start justify-between px-5 py-4 border-b border-gray-100">
          <div className="min-w-0">
            <h2 id="limits-title" className="text-base font-semibold text-gray-900">{t('auth.users.limits.title')}</h2>
            <p className="text-xs text-gray-500 truncate">{user.username}</p>
          </div>
          <button type="button" onClick={onClose} aria-label={t('common.close')}
            className="p-1.5 rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="px-5 py-4 space-y-3">
          {FIELDS.map(([key, label, step]) => (
            <div key={key}>
              <label htmlFor={`limits-${key}`} className="block text-xs font-medium text-gray-500 mb-1">{t(label)}</label>
              <input id={`limits-${key}`} type="number" min="0" step={step} value={draft[key]}
                placeholder={placeholder(key)} autoComplete="off"
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} className={inputCls} />
            </div>
          ))}
          <p className="text-xs text-gray-500">{t('auth.users.limits.hint')}</p>
          {error && <p className="text-sm text-red-600" role="alert">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-gray-100 bg-gray-50">
          <button type="button" onClick={onClose}
            className="px-3 py-2 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-100">
            {t('common.cancel')}
          </button>
          <button type="submit" disabled={busy} data-testid="limits-save"
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-50">
            {busy && <Loader className="w-4 h-4 animate-spin" />}
            {t('common.save')}
          </button>
        </div>
      </form>
    </div>
  );
}
