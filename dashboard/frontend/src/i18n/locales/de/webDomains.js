// Die eigenen Domainlisten eines Agenten für die Web-Tools (AgentWebDomainsCard).
export default {
  title: 'Web-Domains',
  intro: 'Welche Hosts web_search, fetch_url und der Browser für diesen Agenten erreichen dürfen. Gesperrte Hosts kommen zur Sperrliste des Workspaces hinzu. Erlaubte Hosts schränken den Agenten, wenn gesetzt, zusätzlich zu dem ein, was der Workspace erlaubt. Eine Sperre gewinnt, und ein Host deckt seine Subdomains ab.',
  allowed: 'Erlaubte Domains',
  allowedHint: 'Ein Host pro Zeile. Leer heißt: alles, was der Workspace erlaubt.',
  blocked: 'Gesperrte Domains',
  blockedHint: 'Ein Host pro Zeile. Für diesen Agenten immer abgelehnt.',
  save: 'Domains speichern',
  saved: 'Domainlisten gespeichert.',
  saveFailed: 'Die Domainlisten konnten nicht gespeichert werden.',
  loadFailed: 'Die Domainlisten konnten nicht geladen werden.',
  workspaceBlocked: 'Der Workspace sperrt außerdem:',
  workspaceAllowed: 'Der Workspace erlaubt:',
  anyHost: 'jeden Host',
  none: 'keine',
};
