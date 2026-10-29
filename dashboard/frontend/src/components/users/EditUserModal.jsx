/**
 * Users, Edit: an account's first and last name, email and language
 * (common/identity.py, migration 0051). With a name the display name is the
 * two together; the language is the one the account's welcome opens in.
 */
import { useEffect, useState } from 'react';
import { Loader, X } from 'lucide-react';
import { LANGUAGES, useI18n } from '../../i18n';
import { updateUser } from '../../api';

const inputCls = 'w-full border border-gray-200 rounded-lg px-3 py-2 text-sm '
  + 'focus:outline-none focus:ring-2 focus:ring-indigo-300';

/** The language choice both the create form and this window offer. */
export function LanguageSelect({ id, value, onChange, className = inputCls }) {
  return (
    <select id={id} value={value || 'en'} onChange={(e) => onChange(e.target.value)} className={className}>
      {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{`${l.flag} ${l.label}`}</option>)}
    </select>
  );
}

export default function EditUserModal({ user, onClose, onSaved }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState({
    first_name: user.first_name || '',
    last_name: user.last_name || '',
    email: user.email || '',
    language: user.language || 'en',
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const save = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await updateUser(user.id, draft);
      await onSaved();
      onClose();
    } catch (err) {
      setError(err?.response?.data?.detail || t('auth.users.updateFailed'));
      setBusy(false);
    }
  };

  const field = (key, label, props = {}) => (
    <div>
      <label htmlFor={`edit-user-${key}`} className="block text-xs font-medium text-gray-500 mb-1">{label}</label>
      <input id={`edit-user-${key}`} value={draft[key]} autoComplete="off"
        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} className={inputCls} {...props} />
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40" onMouseDown={onClose}>
      <form onSubmit={save} onMouseDown={(e) => e.stopPropagation()} data-testid="edit-user-modal"
        role="dialog" aria-modal="true" aria-labelledby="edit-user-title"
        className="w-full max-w-md bg-white rounded-2xl shadow-2xl overflow-hidden">
        <div className="flex items-start justify-between px-5 py-4 border-b border-gray-100">
          <div className="min-w-0">
            <h2 id="edit-user-title" className="text-base font-semibold text-gray-900">{t('auth.users.editTitle')}</h2>
            <p className="text-xs text-gray-500 truncate">{user.username}</p>
          </div>
          <button type="button" onClick={onClose} aria-label={t('common.close')}
            className="p-1.5 rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="px-5 py-4 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {field('first_name', t('auth.fields.firstName'))}
            {field('last_name', t('auth.fields.lastName'))}
          </div>
          {field('email', t('auth.sso.email'), { type: 'email' })}
          <div>
            <label htmlFor="edit-user-language" className="block text-xs font-medium text-gray-500 mb-1">
              {t('auth.fields.language')}
            </label>
            <LanguageSelect id="edit-user-language" value={draft.language}
              onChange={(language) => setDraft({ ...draft, language })} />
            <p className="mt-1 text-xs text-gray-400">{t('auth.users.languageHint')}</p>
          </div>
          {error && <p className="text-sm text-red-600" role="alert">{error}</p>}
        </div>
        <div className="px-5 py-3 border-t border-gray-100 bg-gray-50 flex justify-end gap-2">
          <button type="button" onClick={onClose}
            className="px-3 py-2 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-100">
            {t('common.cancel')}
          </button>
          <button type="submit" disabled={busy} data-testid="edit-user-save"
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-50">
            {busy && <Loader className="w-4 h-4 animate-spin" />}
            {t('common.save')}
          </button>
        </div>
      </form>
    </div>
  );
}
