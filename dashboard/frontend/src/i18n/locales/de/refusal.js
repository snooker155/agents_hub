// Ein Zug, den die Fähigkeitsprüfung oder ein Ausgabenlimit gestoppt hat
// (chat/refusals.py, components/chat/RefusalCard.jsx).
export default {
  guard: {
    title: 'Dieser Agent darf mit diesen Werkzeugen nicht laufen',
    body: 'Zusammen gilt für diesen Agenten: Er {{caps}}. Diese Kombination erlaubt es Text von außen, den Agenten zum Abfließen Ihrer Daten zu bringen, deshalb hat die Sicherheitsprüfung den Zug gestoppt.',
    capsJoin: ' und ',
    allow: 'Für diesen Agenten erlauben',
    allowing: 'Erlaube...',
    allowed: 'Für diesen Agenten erlaubt. Die Prüfung lässt ihn jetzt laufen.',
    allowedButBlocked: 'Gespeichert, aber im Chat stoppt die Prüfung ihn weiterhin: In diesem Modus gilt die Ausnahme nur für einen Hintergrundlauf in einem Container ohne Netzwerk, und ein Chat-Zug läuft auf dem Hub selbst.',
    noOverride: 'Diese Regel lässt sich für diesen Agenten nicht abschalten.',
    failed: 'Die Ausnahme konnte nicht gespeichert werden',
    details: 'Details',
  },
  guardrail: {
    titleInput: 'Die Nachricht wurde von der Regel „{{name}}“ gestoppt',
    titleOutput: 'Die Antwort des Agenten wurde von der Regel „{{name}}“ gestoppt',
    found: {
      pii: 'Der Text enthält etwas, das wie {{what}} aussieht.',
      keywords: 'Der Text enthält das gesperrte Wort „{{keyword}}“.',
      max_chars: 'Der Text ist {{length}} Zeichen lang, das Limit liegt bei {{limit}}.',
      regex: 'Der Text passt auf das Muster dieser Regel.',
      judge: 'Das prüfende Modell hat den Text beanstandet: {{reason}}',
      judgeUnreachable: 'Das prüfende Modell war nicht erreichbar, und diese Regel stoppt den Zug in diesem Fall.',
    },
    pii: {
      email: 'eine E-Mail-Adresse',
      phone: 'eine Telefonnummer',
      credit_card: 'eine Kreditkartennummer',
      iban: 'eine IBAN',
      api_key: 'ein API-Schlüssel oder Secret',
    },
    warnInput: 'Die Regel „{{name}}“ warnt vor der Nachricht.',
    warnOutput: 'Die Regel „{{name}}“ warnt vor der Antwort.',
    hintInput: 'Entfernen Sie es aus der Nachricht und senden Sie sie erneut.',
    hintOutput: 'Der Agent kann diese Antwort nicht zeigen. Formulieren Sie die Anfrage um.',
  },
  retry: 'Erneut versuchen',
  retryHint: 'Dieselbe Nachricht noch einmal senden.',
};
