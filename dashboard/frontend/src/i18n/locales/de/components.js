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
    newNotification: 'Neue Benachrichtigung',
    minutesAgo: 'vor {{count}} Min.',
    hoursAgo: 'vor {{count}} Std.',
    daysAgo: 'vor {{count}} T.',
    messages: {
      taskStarted: {
        title: 'Aufgabe gestartet: {{title}}',
        body: 'Agent „{{agent}}“ hat die Aufgabe begonnen.',
      },
      taskCompleted: {
        title: 'Aufgabe erledigt: {{title}}',
        body: 'Agent „{{agent}}“ hat die Aufgabe abgeschlossen.',
      },
      taskFailed: {
        title: 'Aufgabe fehlgeschlagen: {{title}}',
        body: 'Agent „{{agent}}“ ist fehlgeschlagen: {{error}}',
        bodyNoError: 'Agent „{{agent}}“ ist fehlgeschlagen.',
      },
      routingFailed: {
        title: 'Zuweisung fehlgeschlagen: {{title}}',
        body: 'Der Orchestrator konnte die Aufgabe nicht zuweisen: {{error}}',
        bodyNoError: 'Der Orchestrator konnte die Aufgabe nicht zuweisen.',
      },
      approvalNeeded: {
        title: 'Freigabe nötig: {{title}}',
        body: 'Geben Sie es auf der Aufgabenseite frei, damit Agent „{{agent}}“ startet.',
        bodyWithReason: 'Geben Sie es auf der Aufgabenseite frei, damit Agent „{{agent}}“ startet. Warum dieser Agent: {{reason}}',
      },
      containerCompleted: {
        title: 'Aufgabe erledigt: {{title}}',
        body: 'Alle Teilaufgaben sind erledigt ({{count}}).',
      },
      taskNeedsInput: {
        title: 'Eine Aufgabe braucht Ihre Antwort',
        body: '{{question}}',
        bodyNoQuestion: 'Der Agent wartet auf Ihre Antwort.',
      },
      reviewCycleLimit: {
        title: 'Eine Aufgabe hat das Limit der Prüfrunden erreicht',
        body: 'Das Limit der Prüfrunden ({{limit}}) ist erreicht: Die Aufgabe braucht Ihre Entscheidung statt einer weiteren automatischen Korrektur- und Prüfrunde.',
      },
    },
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
  cronHint: {
    next: 'Nächste: {{times}}',
    checking: 'Prüfung läuft…',
  },
};
