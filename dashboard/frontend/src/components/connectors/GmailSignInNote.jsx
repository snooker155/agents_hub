import { Link } from 'react-router-dom';

// Under "Sign in with Google" in the two mail forms (the IMAP watcher, the
// mail channel): whether that can work, from GET /api/google/gmail/status
// or the same object the watcher kinds carry. Ready says which mailbox it
// reaches; otherwise it says what is missing and links to the Google tab,
// where "Connect with Gmail" asks for the scope.

export default function GmailSignInNote({ status, t }) {
  if (!status) return <p className="text-xs text-gray-500" data-testid="gmail-note">{t('mailPresets.google.unknown')}</p>;
  if (status.gmail) {
    return (
      <p className="text-xs text-emerald-700" data-testid="gmail-note">
        {t('mailPresets.google.ready', { email: status.account_email || '?' })}
      </p>
    );
  }
  const key = status.connected ? 'noGmail' : 'notConnected';
  return (
    <p className="text-xs text-amber-700" data-testid="gmail-note">
      {t(`mailPresets.google.${key}`, { email: status.account_email || '' })}{' '}
      <Link to="/connectors?tab=google" className="text-indigo-600 hover:underline">{t('mailPresets.google.open')}</Link>
    </p>
  );
}
