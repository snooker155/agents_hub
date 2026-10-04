// Die Agentenschleifen-Einstellungen des Workspace (agents/loop_ext/settings.py,
// `settings.loop`): Komprimierung, Werkzeugsuche und die Schalter
// native/strict, die die Erweiterungen der Schleife über `loop_setting`
// lesen.
export default {
  title: 'Agentenschleife',
  intro: 'Wie die Agentenschleife einen langen Lauf in diesem Workspace verwaltet: wann sie alten Kontext faltet und leert, bevor er das Fenster des Modells füllt, wann ein Agent mit vielen Werkzeugen statt aller eine kurze Liste plus Suche sieht, und welche modell­eigenen Funktionen sie nutzt. Ist nichts gesetzt, gilt die Umgebungsvariable, danach ein eingebauter Standardwert.',
  fields: {
    compaction: {
      label: 'Komprimierung',
      hint: 'Den Kontext eines langen Laufs falten und leeren, bevor er das Fenster des Modells füllt.',
    },
    compactionFraction: {
      label: 'Anteil für die Komprimierung',
      hint: 'Welcher Anteil des Kontextfensters des Modells ein Lauf füllen darf, bevor komprimiert wird, von 0,05 bis 0,95.',
    },
    compactionKeep: {
      label: 'Zu behaltende Ergebnisse',
      hint: 'Wie viele der neuesten Werkzeugergebnisse immer vollständig erhalten bleiben, mindestens 1.',
    },
    toolSearchThreshold: {
      label: 'Schwelle für die Werkzeugsuche',
      hint: 'Ab so vielen Werkzeugen sieht ein Agent eine kurze Liste plus search_tools statt jedes Werkzeugs, mindestens 1.',
    },
    native: {
      label: 'Modelleigene Funktionen',
      hint: 'Anthropics eigenes Context Editing und verzögertes Laden von Werkzeugen nutzen, wo das Modell sie unterstützt.',
    },
    strictTools: {
      label: 'Strenge Werkzeugschemata',
      hint: 'OpenAI bitten, das Schema eines Werkzeugs genau durchzusetzen, wenn jedes gebundene Werkzeug dafür geeignet ist.',
    },
    viewFocus: {
      label: 'Fokus auf die Ansichtsart',
      hint: 'Einem Ansichts-Agenten die Werkzeuge der Ansichtsart zeigen, an der er arbeitet, und die der anderen Arten ausblenden.',
    },
    toolOutputSpillChars: {
      label: 'Auslagerungsgrenze für Werkzeugausgaben',
      hint: 'Ein Werkzeugergebnis mit mehr Zeichen wird als Datei des Arbeitsbereichs unter tool-outputs/ gespeichert, und das Modell sieht Anfang, Ende und die Datei. 0 schaltet es ab.',
    },
    advisorMaxCalls: {
      label: 'Beraterabfragen pro Lauf',
      hint: 'Wie oft ein Lauf das Beratermodell des Agenten fragen darf, von 0 bis 50.',
    },
    advisorMaxAnswerChars: {
      label: 'Länge der Beraterantwort',
      hint: 'Die längste Beraterantwort, die der Agent erhält, in Zeichen, von 200 bis 50000.',
    },
  },
  source: {
    workspace: 'dieser Workspace',
    environment: 'die Umgebungsvariable',
    default: 'der Standardwert',
  },
  reset: 'Zurücksetzen',
  resetFor: '{{field}} auf die Umgebungsvariable oder den Standardwert zurücksetzen',
  save: 'Speichern',
  saved: 'Agentenschleifen-Einstellungen gespeichert.',
  saveFailed: 'Die Agentenschleifen-Einstellungen konnten nicht gespeichert werden.',
  loadFailed: 'Die Agentenschleifen-Einstellungen konnten nicht geladen werden.',
};
