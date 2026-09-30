// Die GitHub-App-Karte auf der Seite des Git-Connectors (routes/github_app.py,
// docs/github-app.md).
export default {
  title: 'GitHub App',
  description: 'Mit einer GitHub App stellt der Hub kurzlebige Tokens selbst aus. Binden Sie eine Installation an einen Arbeitsbereich, und jeder Lauf dort, dessen Agent GITHUB_TOKEN deklariert, pusht und öffnet Pull Requests als die App.',
  notConfigured: 'Auf diesem Hub nicht eingerichtet.',
  envHint: 'Legen Sie die App auf GitHub an, setzen Sie dann diese Variablen in der Umgebung des Backends und starten Sie es neu:',
  install: 'Installieren',
  sync: 'Synchronisieren',
  noInstallations: 'Noch keine Installationen. Installieren Sie die App in einem Konto oder einer Organisation und synchronisieren Sie dann.',
  account: 'Konto',
  type: 'Typ',
  repositories: 'Repositories',
  workspace: 'Arbeitsbereich',
  unbound: 'Nicht gebunden',
  unbind: 'Lösen',
  bindTo: 'Arbeitsbereich für {{account}}',
  types: {
    Organization: 'Organisation',
    User: 'Benutzer',
  },
  targets: {
    all: 'Alle',
    selected: 'Ausgewählte',
  },
  loadFailed: 'Die GitHub App konnte nicht geladen werden.',
  syncFailed: 'Die Installationen konnten nicht synchronisiert werden.',
  bindFailed: 'Die Bindung konnte nicht geändert werden.',
};
