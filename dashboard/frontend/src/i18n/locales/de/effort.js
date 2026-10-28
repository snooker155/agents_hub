// Denkaufwand (reasoning effort, providers/reasoning_profile.py): der Tab
// „Modell“ eines Agenten, die Modellauswahl in der Kopfzeile und die Seite „Modelle“.
export default {
  label: 'Denkaufwand',
  levels: {
    off: 'Aus',
    none: 'Keiner',
    minimal: 'Minimal',
    low: 'Niedrig',
    medium: 'Mittel',
    high: 'Hoch',
    xhigh: 'Sehr hoch',
    max: 'Maximal',
  },
  inherit: 'Erben',
  inheritValue: 'Erben: {{value}} ({{source}})',
  sources: {
    agent: 'dieser Agent',
    workspace: 'Kopfzeile',
    model: 'Standard des Modells',
    provider: 'Standard des Anbieters',
  },
  unsupported: 'Dieses Modell nimmt keinen Denkaufwand an.',
  clamped: '„{{requested}}“ gibt es bei diesem Modell nicht, es läuft mit „{{value}}“.',
  agentHint: 'Wie viel das Modell vor der Antwort nachdenkt. „Erben“ nimmt den Wert aus der Kopfzeile (wenn auch das Modell geerbt wird), dann den Standard des Modells auf der Seite „Modelle“, dann den des Anbieters.',
  headerTitle: 'Denkaufwand',
  headerHint: 'Für Agenten, die das Modell erben und keinen eigenen Denkaufwand setzen.',
  headerAuto: 'Modell',
  headerDefault: 'Standard des Modells ({{value}})',
  modelDefault: 'Anbieter ({{value}})',
  modelHint: 'Der Standard-Denkaufwand dieses Modells. Leer = Standard des Anbieters. Die Wahl in der Kopfzeile und der eigene Wert eines Agenten gehen vor. „Aus“ sendet: {{off}}.',
  offNothing: 'nichts',
};
