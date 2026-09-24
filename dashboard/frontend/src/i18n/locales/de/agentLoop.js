// Schleifeneinstellungen pro Agent (agents/agent_loop.py, vierter Zyklus,
// Stufe 2): Fallback-Modelle, das Ausgabeschema und die beiden
// dreistufigen Schleifenoptionen.
export default {
  title: 'Schleifeneinstellungen',
  intro: 'Fallback-Modelle, die versucht werden, wenn das eigene Modell dieses Agenten ablehnt oder fehlschlägt, ein Schema, dem die endgültige Antwort entsprechen muss, und zwei Umschalter für die eigenen Richtlinien der Schleife.',
  save: 'Speichern',
  saved: 'Schleifeneinstellungen gespeichert.',
  saveFailed: 'Die Schleifeneinstellungen konnten nicht gespeichert werden.',
  loadFailed: 'Die Schleifeneinstellungen konnten nicht geladen werden.',
  schemaEmpty: 'Geben Sie ein JSON-Schema ein oder wechseln Sie zu Freitext.',
  schemaInvalidJson: 'Kein gültiges JSON: {{message}}',
  schemaNotObject: 'Ein JSON-Schema muss ein einzelnes JSON-Objekt sein.',
  fallback: {
    title: 'Fallback-Modelle',
    intro: 'Werden in dieser Reihenfolge versucht, wenn das eigene Modell dieses Agenten ablehnt, ratenlimitiert ist oder mit einem Serverfehler fehlschlägt. Das Modell, das tatsächlich geantwortet hat, wird im Lauf vermerkt.',
    empty: 'Keine Fallback-Modelle. Der Lauf stoppt beim ersten Fehler des eigenen Modells dieses Agenten.',
    addLabel: 'Fallback-Modell hinzufügen',
    addPlaceholder: 'Modell zum Hinzufügen wählen…',
    add: 'Hinzufügen',
    noCatalog: 'Noch keine aktivierten Modelle im Katalog. Aktivieren Sie zuerst welche auf der Modelle-Seite.',
    moveUp: '{{model}} nach oben verschieben',
    moveDown: '{{model}} nach unten verschieben',
    remove: '{{model}} entfernen',
  },
  schema: {
    title: 'Ausgabeschema',
    intro: 'Wenn gesetzt, muss die endgültige Antwort des Agenten ein einzelner JSON-Wert sein, der diesem Schema entspricht. Eine Antwort, die die Prüfung nicht besteht, erhält bis zu zwei automatische Korrekturversuche, bevor der Lauf mit einem Fehler endet.',
    freeText: 'Freitext',
    jsonSchema: 'JSON-Schema',
    placeholder: '{\n  "type": "object",\n  "properties": {\n    "answer": {"type": "string"}\n  },\n  "required": ["answer"]\n}',
  },
  triState: {
    automatic: 'Automatisch',
    on: 'Ein',
    off: 'Aus',
  },
  toolSearch: {
    title: 'Werkzeugsuche',
    hint: 'Automatisch schaltet ein, sobald der Agent mehr Werkzeuge hat als der Schwellenwert des Arbeitsbereichs.',
  },
  compaction: {
    title: 'Kontextverdichtung',
    hint: 'Automatisch folgt der Verdichtungseinstellung des Arbeitsbereichs.',
  },
};
