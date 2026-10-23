// An agent's own domain lists for the web tools (AgentWebDomainsCard).
export default {
  title: 'Web domains',
  intro: 'Which hosts web_search, fetch_url and the browser may reach for this agent. Blocked hosts join the workspace deny list. Allowed hosts, when set, narrow the agent to them on top of what the workspace allows. A blocked host wins, and a host covers its subdomains.',
  allowed: 'Allowed domains',
  allowedHint: 'One host per line. Empty means whatever the workspace allows.',
  blocked: 'Blocked domains',
  blockedHint: 'One host per line. Always refused for this agent.',
  save: 'Save domains',
  saved: 'Domain lists saved.',
  saveFailed: 'Could not save the domain lists.',
  loadFailed: 'Could not load the domain lists.',
  workspaceBlocked: 'The workspace also blocks:',
  workspaceAllowed: 'The workspace allows:',
  anyHost: 'any host',
  none: 'none',
};
