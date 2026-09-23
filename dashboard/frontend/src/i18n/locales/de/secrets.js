// Workspace-Secrets (common/secrets.py, docs/secrets.md).
export default {
  title: 'Secrets',
  description: 'Verschlüsselte Werte, die ein Agentenlauf als Umgebungsvariablen erhält, und nur unter den Namen, die dieser Agent deklariert. Ein Wert lässt sich ersetzen oder löschen, aber nie wieder auslesen.',
  loadFailed: 'Die Secrets konnten nicht geladen werden.',
  saveFailed: 'Das Secret konnte nicht gespeichert werden.',
  deleteFailed: 'Das Secret konnte nicht gelöscht werden.',
  empty: 'In diesem Workspace gibt es noch keine Secrets.',
  noKey: 'Zum Speichern von Secrets wird ein Schlüssel gebraucht. Setze AGENTS_HUB_SECRET_KEY in .env (erzeugen mit ah secrets keygen) und starte das Backend neu.',
  columns: {
    name: 'Name',
    scope: 'Geltungsbereich',
    hint: 'Wert',
    updated: 'Aktualisiert',
  },
  scope: {
    workspace: 'Ganzer Workspace',
    agent: 'Agent {{agent}}',
    user: 'Benutzer {{user}}',
    agentUser: 'Agent {{agent}}, Benutzer {{user}}',
  },
  fields: {
    name: 'Name, z. B. GITHUB_TOKEN',
    value: 'Wert',
    anyAgent: 'Jeder Agent',
    anyUser: 'Jeder Benutzer',
  },
  add: 'Secret speichern',
  delete: 'Secret löschen',
  nameHint: 'Großbuchstaben, Ziffern und Unterstriche, beginnend mit einem Buchstaben.',
};
