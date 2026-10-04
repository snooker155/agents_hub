import { useState } from 'react';
import { LogOut, ExternalLink, Mail } from 'lucide-react';
import { googleOAuthStartUrl, disconnectGoogle } from '../../api';
import { useI18n } from '../../i18n';
import CredentialConnector from './CredentialConnector';

// Google Workspace: a service account key, or an OAuth client plus a
// "Connect Google" step that stores the refresh token (routes/google.py).
// "Connect with Gmail" runs the same step with the Gmail scope added, so the
// IMAP watcher and the mail channel can sign in to the mailbox without an
// app password (connectors/mail/oauth.py). The credentials themselves go
// through CredentialConnector (name="google"), which already carries the
// per workspace source badge and define/remove switch; this only adds the
// OAuth button and the Gmail status, both scoped to the same workspace.
//
// A workspace that still inherits the default's Google app has no app of
// its own to start an OAuth flow against (the backend answers 400), so the
// buttons give way to a message pointing at "Define for this workspace"
// above, rather than letting the click fail.
export default function GoogleConnector() {
  const { t } = useI18n();
  return (
    <CredentialConnector name="google">
      {({ payload, reload, workspace, readOnly }) => {
        const extra = payload?.extra || {};
        if (readOnly) {
          return (
            <div className="space-y-2 border-t border-gray-100 pt-3">
              <p className="text-sm text-gray-500" data-testid="google-oauth-blocked">
                {t('connectors.google.defineFirst')}
              </p>
            </div>
          );
        }
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
                href={extra.oauth_ready ? googleOAuthStartUrl({ workspace }) : undefined}
                aria-disabled={!extra.oauth_ready}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium ${extra.oauth_ready ? 'bg-indigo-600 hover:bg-indigo-700 text-white' : 'bg-gray-100 text-gray-400 pointer-events-none'}`}
              >
                <ExternalLink className="w-3.5 h-3.5" /> {t('connectors.google.connect')}
              </a>
              <a
                href={extra.oauth_ready ? googleOAuthStartUrl({ gmail: true, workspace }) : undefined}
                aria-disabled={!extra.oauth_ready}
                data-testid="google-connect-gmail"
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border ${extra.oauth_ready ? 'border-indigo-300 text-indigo-700 hover:bg-indigo-50' : 'border-gray-200 text-gray-400 pointer-events-none'}`}
              >
                <Mail className="w-3.5 h-3.5" /> {t('connectors.google.connectGmail')}
              </a>
              {extra.mode === 'oauth' && (
                <GoogleDisconnectButton workspace={workspace} reload={reload} t={t} />
              )}
            </div>
            {extra.mode !== 'none' && (
              <p className="text-xs text-gray-500" data-testid="google-gmail-state">
                {extra.gmail ? t('connectors.google.gmailOn') : t('connectors.google.gmailOff')}
              </p>
            )}
            {!extra.oauth_ready && <p className="text-xs text-gray-500">{t('connectors.google.oauthHint')}</p>}
          </div>
        );
      }}
    </CredentialConnector>
  );
}

function GoogleDisconnectButton({ workspace, reload, t }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      disabled={busy}
      onClick={async () => { setBusy(true); try { await disconnectGoogle(workspace); await reload(); } finally { setBusy(false); } }}
      className="flex items-center gap-1.5 border border-red-200 text-red-700 hover:bg-red-50 px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
    >
      <LogOut className="w-3.5 h-3.5" /> {t('connectors.google.disconnect')}
    </button>
  );
}
