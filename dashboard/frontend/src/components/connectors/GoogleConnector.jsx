import { useState } from 'react';
import { LogOut, ExternalLink } from 'lucide-react';
import { googleOAuthStartUrl, disconnectGoogle } from '../../api';
import { useI18n } from '../../i18n';
import CredentialConnector from './CredentialConnector';

// Google Workspace: a service account key, or an OAuth client plus a
// "Connect Google" step that stores the refresh token (routes/google.py).
export default function GoogleConnector() {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  return (
    <CredentialConnector name="google">
      {({ payload, reload }) => {
        const extra = payload?.extra || {};
        return (
          <div className="space-y-2 border-t border-gray-100 pt-3">
            <p className="text-sm text-gray-600">
              {extra.mode === 'oauth' && extra.account_email
                ? t('connectors.google.connectedAs', { email: extra.account_email })
                : extra.mode === 'service_account'
                  ? t('connectors.google.serviceAccountMode')
                  : t('connectors.google.notConnected')}
            </p>
            <div className="flex flex-wrap gap-2">
              <a
                href={extra.oauth_ready ? googleOAuthStartUrl() : undefined}
                aria-disabled={!extra.oauth_ready}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium ${extra.oauth_ready ? 'bg-indigo-600 hover:bg-indigo-700 text-white' : 'bg-gray-100 text-gray-400 pointer-events-none'}`}
              >
                <ExternalLink className="w-3.5 h-3.5" /> {t('connectors.google.connect')}
              </a>
              {extra.mode === 'oauth' && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={async () => { setBusy(true); try { await disconnectGoogle(); await reload(); } finally { setBusy(false); } }}
                  className="flex items-center gap-1.5 border border-red-200 text-red-700 hover:bg-red-50 px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
                >
                  <LogOut className="w-3.5 h-3.5" /> {t('connectors.google.disconnect')}
                </button>
              )}
            </div>
            {!extra.oauth_ready && <p className="text-xs text-gray-500">{t('connectors.google.oauthHint')}</p>}
          </div>
        );
      }}
    </CredentialConnector>
  );
}
