// Das Audit-Protokoll (docs/audit.md, routes/audit.py). Sichtbar im Modus
// token und multi, im Modus single vollständig ausgeblendet, da es dort
// niemanden gibt, der geprüft werden könnte.
export default {
  title: 'Audit-Protokoll',
  description: 'Jede aufgezeichnete Aktion: eine Anmeldung, ein Rollenwechsel, ein gestarteter Lauf, eine genehmigte Werkzeugnutzung, eine geänderte Richtlinie oder ein geändertes Budget, sowie jede Schreibanfrage im Modus token und multi. Ein Administrator sieht alles, alle anderen sehen ihre eigenen Aktionen sowie die Aktionen eines Arbeitsbereichs, den sie besitzen.',
  system: 'System',
  details: 'Details',
  empty: 'Keine Einträge entsprechen diesen Filtern.',
  loadFailed: 'Das Audit-Protokoll konnte nicht geladen werden.',
  exportCsv: 'CSV exportieren',
  exportJsonl: 'JSONL exportieren',

  filters: {
    actor: 'Akteur',
    actorPlaceholder: 'Benutzer-Id oder Name',
    action: 'Aktion',
    allActions: 'Jede Aktion',
    workspace: 'Arbeitsbereich',
    allWorkspaces: 'Jeder Arbeitsbereich',
    result: 'Ergebnis',
    allResults: 'Jedes Ergebnis',
    since: 'Von',
    until: 'Bis',
    text: 'Suche',
    textPlaceholder: 'Pfad, Objekt-Id oder Details',
    clear: 'Filter zurücksetzen',
  },

  results: {
    ok: 'OK',
    denied: 'Abgelehnt',
    error: 'Fehler',
  },

  columns: {
    time: 'Zeit',
    actor: 'Akteur',
    action: 'Aktion',
    object: 'Objekt',
    workspace: 'Arbeitsbereich',
    result: 'Ergebnis',
    ip: 'IP',
  },

  pagination: {
    showing: '{{from}}–{{to}} von {{total}} angezeigt',
    prev: 'Zurück',
    next: 'Weiter',
  },
};
