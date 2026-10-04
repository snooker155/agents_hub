// Die Anbieterliste, die das IMAP-Watcher-Formular und der Mail-Kanal teilen
// (components/connectors/MailPresetPicker.jsx, connectors/mail/presets.py).
export default {
  label: 'E-Mail-Anbieter',
  custom: 'Anderer Server (Hosts selbst eintragen)',
  help: 'So wird es erstellt',
  auth: {
    app_password: '{{provider}} akzeptiert das Kontopasswort nicht über IMAP. Erstellen Sie ein App-Passwort in den Sicherheitseinstellungen des Kontos (Bestätigung in zwei Schritten muss aktiv sein) und tragen Sie es als Passwort ein.',
    password: '{{provider}} nimmt das normale Kontopasswort. Prüfen Sie, dass der IMAP-Zugriff in den Postfach-Einstellungen eingeschaltet ist.',
    oauth: '{{provider}} hat die Passwort-Anmeldung für IMAP und SMTP abgeschaltet, ein Passwort wird daher sehr wahrscheinlich abgelehnt. Die Hosts sind für Tenants eingetragen, die es noch erlauben; sonst verbinden Sie das Konto über den Microsoft-Konnektor.',
  },
  useGoogle: 'Oder mit dem verbundenen Google-Konto anmelden.',
  // GmailSignInNote.jsx: whether the Google connector can sign in to Gmail.
  google: {
    unknown: 'Die Google-Verbindung ließ sich nicht prüfen; die Statuskarte zeigt, ob die Anmeldung klappt.',
    ready: 'Meldet sich bei Gmail als {{email}} an.',
    noGmail: 'Google ist als {{email}} verbunden, aber ohne Gmail-Zugriff.',
    notConnected: 'Google ist nicht verbunden.',
    open: 'Google-Tab öffnen und auf Mit Gmail verbinden klicken',
  },
};
