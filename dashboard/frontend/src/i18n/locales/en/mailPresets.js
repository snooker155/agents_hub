// The mailbox provider pick list shared by the IMAP watcher form and the mail
// channel (components/connectors/MailPresetPicker.jsx, connectors/mail/presets.py).
export default {
  label: 'Mail provider',
  custom: 'Other server (type the hosts yourself)',
  help: 'How to create one',
  auth: {
    app_password: '{{provider}} does not accept the account password over IMAP. Create an app password in the account\'s security settings (two-step verification must be on) and use it as the password.',
    password: '{{provider}} takes the ordinary account password. Make sure IMAP access is switched on in the mailbox settings.',
    oauth: '{{provider}} has retired password sign-in for IMAP and SMTP, so a password will most likely be refused. Hosts are filled in for tenants that still allow it; otherwise connect the account through the Microsoft connector instead.',
  },
  useGoogle: 'Or sign in with the connected Google account instead.',
  // GmailSignInNote.jsx: whether the Google connector can sign in to Gmail.
  google: {
    unknown: 'Could not check the Google connection; the status card shows whether sign in works.',
    ready: 'Signs in to Gmail as {{email}}.',
    noGmail: 'Google is connected as {{email}}, but without Gmail access.',
    notConnected: 'Google is not connected.',
    open: 'Open the Google tab and click Connect with Gmail',
  },
};
