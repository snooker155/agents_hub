export default {
  notifications: {
    title: 'Benachrichtigungen',
    tooltip: 'Benachrichtigungen',
    markReadFailed: 'Konnte nicht als gelesen markiert werden',
    markAllRead: 'Alle als gelesen',
    empty: 'Keine Benachrichtigungen',
    viewTask: 'Aufgabe öffnen →',
    viewAllInPlan: 'Alle im Plan anzeigen →',
    justNow: 'gerade eben',
    minutesAgo: 'vor {{count}} Min.',
    hoursAgo: 'vor {{count}} Std.',
    daysAgo: 'vor {{count}} T.',
  },
  inlineEdit: {
    clickToEdit: 'Zum Bearbeiten klicken',
    clickToAdd: 'Zum Hinzufügen klicken…',
  },
  runOrigin: {
    external: 'Extern',
    imported: 'importiert',
    partial: 'unvollständig',
    externalHint: 'Von der Verbindung {{connection}} gemeldet; dieser Hub hat sie nicht ausgeführt, daher gibt es kein eigenes Log und keinen Live-Stream.',
    importedHint: 'Aus OpenTelemetry-Spans aufgezeichnet, nachdem der Lauf beendet war; eine Live-Ansicht dazu gibt es nicht.',
    partialHint: 'Der Root-Span ist nie eingetroffen, daher können Eingabe, Antwort und Ergebnis fehlen.',
  },
};
