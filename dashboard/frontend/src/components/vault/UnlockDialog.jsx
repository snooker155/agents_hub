import { useState } from 'react';
import { Eye, EyeOff, Lock, X } from 'lucide-react';

import { useI18n } from '../../i18n';
import { checkVerifier, deriveKey } from '../../lib/syncCrypto';

const INPUT = 'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm';

/**
 * Asks for the passphrase and an explicit consent, derives the key here and
 * checks it against the stored verifier. On success hands { key, keyId } to
 * onUnlocked; the passphrase itself is never passed on or kept.
 */
export default function UnlockDialog({ space, keyRow, onClose, onUnlocked }) {
  const { t } = useI18n();
  const [passphrase, setPassphrase] = useState('');
  const [show, setShow] = useState(false);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const ready = passphrase.length > 0 && consent && !busy;

  const submit = async (e) => {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError('');
    try {
      const { key, keyId } = await deriveKey(passphrase, { salt: keyRow.salt, iterations: keyRow.iterations });
      if (!(await checkVerifier(key, keyRow.verifier))) {
        setError(t('vault.unlock.wrong'));
        setBusy(false);
        return;
      }
      setPassphrase('');
      onUnlocked({ key, keyId });
    } catch {
      setError(t('vault.unlock.failed'));
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label={t('vault.unlock.title')}>
      <form onSubmit={submit} className="bg-white rounded-xl shadow-xl w-full max-w-md max-h-[90vh] overflow-y-auto p-5">
        <div className="flex items-start justify-between gap-3 mb-2">
          <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900"><Lock size={16} />{t('vault.unlock.title')}</h2>
          <button type="button" onClick={onClose} aria-label={t('vault.unlock.cancel')} className="text-gray-400 hover:text-gray-600"><X size={16} /></button>
        </div>
        <p className="text-sm text-gray-600 mb-3">{t('vault.unlock.intro', { space })}</p>
        <label className="block text-xs font-medium text-gray-600 mb-1" htmlFor="vault-passphrase">{t('vault.unlock.passphrase')}</label>
        <div className="relative mb-3">
          <input
            id="vault-passphrase"
            type={show ? 'text' : 'password'}
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            className={`${INPUT} pr-10`}
          />
          <button
            type="button"
            onClick={() => setShow((v) => !v)}
            aria-label={show ? t('vault.unlock.hide') : t('vault.unlock.show')}
            className="absolute inset-y-0 right-0 px-3 text-gray-400 hover:text-gray-600"
          >
            {show ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </div>
        <label className="flex items-start gap-2 text-sm text-gray-700 mb-3">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-1" />
          <span>{t('vault.unlock.consent')}</span>
        </label>
        {error && <p role="alert" className="text-sm text-red-600 mb-3">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">
            {t('vault.unlock.cancel')}
          </button>
          <button type="submit" disabled={!ready} className="px-3 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed">
            {busy ? t('vault.unlock.working') : t('vault.unlock.submit')}
          </button>
        </div>
      </form>
    </div>
  );
}
