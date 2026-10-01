// Route → title key. Pure data: kept at module scope so nothing rebuilds forty
// regexes on every render, and in its own module because two surfaces name the
// page the user is on — the shell's document title, and the page chat's header.
export const ROUTE_TITLES = [
  { match: /^\/$/, titleKey: 'layout.titles.chat' },
  { match: /^\/chat(\/.*)?$/, titleKey: 'layout.titles.chat' },
  { match: /^\/dashboard$/, titleKey: 'layout.titles.dashboard' },
  { match: /^\/orchestrator$/, titleKey: 'layout.titles.orchestrator' },
  { match: /^\/tasks$/, titleKey: 'layout.titles.tasks' },
  { match: /^\/tasks\/.+$/, titleKey: 'layout.titles.taskDetails' },
  { match: /^\/plan$/, titleKey: 'layout.titles.plan' },
  { match: /^\/deployments$/, titleKey: 'layout.titles.deployments' },
  { match: /^\/agents$/, titleKey: 'layout.titles.agents' },
  { match: /^\/agents\/.+$/, titleKey: 'layout.titles.agentDetails' },
  { match: /^\/marketplace$/, titleKey: 'layout.titles.marketplace' },
  { match: /^\/marketplace\/.+$/, titleKey: 'layout.titles.marketplaceAgent' },
  { match: /^\/skills$/, titleKey: 'layout.titles.skills' },
  { match: /^\/web-logs$/, titleKey: 'layout.titles.webLogs' },
  { match: /^\/tools$/, titleKey: 'layout.titles.tools' },
  { match: /^\/workspaces$/, titleKey: 'layout.titles.workspaces' },
  { match: /^\/workspaces\/.+$/, titleKey: 'layout.titles.workspaceDetails' },
  { match: /^\/memory$/, titleKey: 'layout.titles.memory' },
  { match: /^\/artifacts(\/.*)?$/, titleKey: 'layout.titles.artifacts' },
  { match: /^\/views\/.+$/, titleKey: 'layout.titles.viewDetail' },
  { match: /^\/studio(\/.*)?$/, titleKey: 'layout.titles.studio' },
  { match: /^\/flows$/, titleKey: 'layout.titles.flows' },
  { match: /^\/flows\/.+$/, titleKey: 'layout.titles.flowEditor' },
  { match: /^\/loops$/, titleKey: 'layout.titles.loops' },
  { match: /^\/teams$/, titleKey: 'layout.titles.teams' },
  { match: /^\/teams\/.+$/, titleKey: 'layout.titles.team' },
  { match: /^\/registry$/, titleKey: 'layout.titles.registry' },
  { match: /^\/sessions$/, titleKey: 'layout.titles.sessions' },
  { match: /^\/sessions\/.+$/, titleKey: 'layout.titles.sessionDetails' },
  { match: /^\/run-groups$/, titleKey: 'layout.titles.runGroups' },
  { match: /^\/messages$/, titleKey: 'layout.titles.messages' },
  { match: /^\/messages\/.+$/, titleKey: 'layout.titles.messageDetails' },
  { match: /^\/services$/, titleKey: 'layout.titles.services' },
  { match: /^\/services\/.+$/, titleKey: 'layout.titles.serviceDetail' },
  { match: /^\/environments$/, titleKey: 'layout.titles.environments' },
  { match: /^\/guardrails$/, titleKey: 'layout.titles.guardrails' },
  { match: /^\/containers$/, titleKey: 'layout.titles.containers' },
  { match: /^\/browser$/, titleKey: 'layout.titles.browser' },
  { match: /^\/health$/, titleKey: 'layout.titles.health' },
  { match: /^\/cluster$/, titleKey: 'layout.titles.cluster' },
  { match: /^\/projects$/, titleKey: 'layout.titles.projects' },
  { match: /^\/projects\/.+$/, titleKey: 'layout.titles.projectDetails' },
  { match: /^\/models$/, titleKey: 'layout.titles.models' },
  { match: /^\/models\/.+$/, titleKey: 'layout.titles.modelDetail' },
  { match: /^\/costs$/, titleKey: 'layout.titles.costs' },
  { match: /^\/evals$/, titleKey: 'layout.titles.evals' },
  { match: /^\/playground$/, titleKey: 'layout.titles.playground' },
  // Before the scenario pattern: a world is not a scenario id.
  { match: /^\/playground\/worlds(\/.+)?$/, titleKey: 'layout.titles.playgroundWorlds' },
  { match: /^\/playground\/.+$/, titleKey: 'layout.titles.playgroundScenario' },
  { match: /^\/settings(\/.*)?$/, titleKey: 'layout.titles.settings' },
  { match: /^\/docs(\/.*)?$/, titleKey: 'layout.titles.docs' },
];

/** The i18n key naming this page, or '' for a route nobody titled. */
export function routeTitleKey(pathname) {
  const entry = ROUTE_TITLES.find((r) => r.match.test(pathname || ''));
  return entry ? entry.titleKey : '';
}

export default ROUTE_TITLES;
