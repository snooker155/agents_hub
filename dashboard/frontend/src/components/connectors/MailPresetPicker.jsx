import { ExternalLink } from 'lucide-react';

// A pick list of the popular mailbox providers (connectors/mail/presets.py,
// served with the watcher kinds and with the mail channel spec). Picking one
// hands its field values to the form through onPick; the picker itself keeps
// no state beyond what the caller passes back as `value`, so the form stays
// the single owner of its fields.
//
// Under the list, a sentence about how the provider takes the password: an
// app password with a link to where it is made, the account password, or a
// warning that the provider has retired password sign-in. For Gmail, when
// the form can sign in with the Google connector (onUseGoogle), a button
// switches to that instead of an app password.

export default function MailPresetPicker({ presets, value, onPick, onUseGoogle, googleActive = false, t, inputCls }) {
  if (!presets?.length) return null;
  const picked = presets.find((p) => p.id === value);
  return (
    <div className="space-y-1">
      <label className="text-sm font-medium text-gray-700 block">{t('mailPresets.label')}</label>
      <select
        className={inputCls}
        value={value || ''}
        aria-label={t('mailPresets.label')}
        data-testid="mail-preset"
        onChange={(e) => {
          const p = presets.find((x) => x.id === e.target.value) || null;
          onPick(p);
        }}
      >
        <option value="">{t('mailPresets.custom')}</option>
        {presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
      </select>
      {picked && googleActive && picked.id === 'gmail' ? null : picked && (
        <p className={`text-xs ${picked.auth === 'oauth' ? 'text-amber-700' : 'text-gray-500'}`} data-testid="mail-preset-hint">
          {t(`mailPresets.auth.${picked.auth}`, { provider: picked.label })}
          {picked.help_url && (
            <>
              {' '}
              <a href={picked.help_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-indigo-600 hover:underline">
                {t('mailPresets.help')}<ExternalLink className="w-3 h-3" />
              </a>
            </>
          )}
          {picked.id === 'gmail' && onUseGoogle && (
            <>
              {' '}
              <button type="button" onClick={onUseGoogle} className="text-indigo-600 hover:underline" data-testid="mail-preset-use-google">
                {t('mailPresets.useGoogle')}
              </button>
            </>
          )}
        </p>
      )}
    </div>
  );
}
