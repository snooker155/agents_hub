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
    allowedButBlocked: 'Gespeichert, aber die Prüfung stoppt ihn weiterhin: In diesem Modus gilt die Ausnahme nur, wenn der Agent in einem Container ohne Netzwerk läuft.',
    noOverride: 'Diese Regel lässt sich für diesen Agenten nicht abschalten.',
    failed: 'Die Ausnahme konnte nicht gespeichert werden',
    details: 'Details',
  },
  retry: 'Erneut versuchen',
  retryHint: 'Dieselbe Nachricht noch einmal senden.',
};
