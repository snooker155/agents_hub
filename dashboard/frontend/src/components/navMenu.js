/**
 * The sidebar's map: five groups along the object tree of docs/overview.md,
 * and two ways of showing it.
 *
 * - conversation: where a person talks (the chat).
 * - work: what gets done and when (projects, tasks, flows and the other
 *   forms of joined work, their schedules and what they produced).
 * - agents: who does it and what they know (agents and their live copies,
 *   models, skills, memory, the shelves agents come from).
 * - integrations: what the hub is attached to, in both directions.
 * - records: what happened, what it cost, and the administration.
 *
 * The simple menu shows the items marked `simple` (the first three groups
 * and the two pages nobody can do without, Settings and Docs); the full menu
 * shows everything. No page is removed in either mode: only the map
 * changes. Every other item names its `home`, the simple row it belongs to
 * (Deployments under Plan, Loops under Agent Flows, Connectors under
 * Settings), and a page reached by a link lights that row up, so the menu
 * never changes shape under the person (see visibleGroups).
 *
 * Kept apart from Layout.jsx so the map can be tested without rendering the
 * shell, and so a page that wants to say "this is under Integrations" has
 * one place to ask.
 */
import {
  CheckSquare, Folder, Database, Factory, Wrench, Users,
  Activity, PlayCircle, MessageCircle, ScrollText, Settings, Network, FolderGit2, Box, Store,
  CalendarClock, BookOpen, Brain, Images, Repeat, UsersRound,
  GraduationCap, Globe, Link2, Layers, Rocket, ShieldCheck,
} from 'lucide-react';

export const MENU_MODE_KEY = 'agents_hub_menu_mode';
export const SIMPLE = 'simple';
export const FULL = 'full';

/** The mode a viewer starts in before choosing one: the simple menu. */
export function defaultMenuMode() {
  return SIMPLE;
}

/** The viewer's own choice, when there is a valid one in storage. */
export function readMenuMode() {
  try {
    const v = localStorage.getItem(MENU_MODE_KEY);
    return v === SIMPLE || v === FULL ? v : null;
  } catch {
    return null;
  }
}

export function writeMenuMode(mode) {
  try {
    localStorage.setItem(MENU_MODE_KEY, mode);
  } catch { /* storage unavailable: the choice lasts until reload */ }
}

/**
 * The groups, every item in them, with `simple` on the items the simple menu
 * keeps.
 */
export function buildMenu({ t }) {
  const groups = [
    {
      key: 'conversation',
      items: [
        { name: t('nav.chat'), path: '/chat', icon: MessageCircle, simple: true },
      ],
    },
    {
      key: 'work',
      items: [
        { name: t('nav.projects'), path: '/projects', icon: FolderGit2, simple: true },
        { name: t('nav.tasks'), path: '/tasks', icon: CheckSquare, simple: true },
        { name: t('nav.plan'), path: '/plan', icon: CalendarClock, simple: true },
        // What the agents produced and work with: the views they built and the
        // files the workspace keeps by id, two tabs of one page. The Studio and
        // a view's page are reached from here, so they light this item up.
        { name: t('nav.artifacts'), path: '/artifacts', icon: Images, also: ['/studio', '/views'], simple: true },
        // The forms of joined work, side by side: a flow is a graph, a loop a
        // repeated agent, a team agents talking, the orchestrator one agent
        // handing out work.
        { name: t('nav.flows'), path: '/flows', icon: Factory, simple: true },
        { name: t('nav.loops'), path: '/loops', icon: Repeat, home: '/flows' },
        { name: t('nav.teams'), path: '/teams', icon: UsersRound, home: '/flows' },
        { name: t('nav.orchestrator'), path: '/orchestrator', icon: Network, home: '/flows' },
        // Schedules of work, opened from the Plan page.
        { name: t('nav.deployments'), path: '/deployments', icon: Rocket, home: '/plan' },
        // Creating a workspace and its settings: the top bar only
        // switches between the ones that exist.
        { name: t('nav.workspaces'), path: '/workspaces', icon: Folder, simple: true },
      ],
    },
    {
      key: 'agents',
      items: [
        { name: t('nav.agents'), path: '/agents', icon: Users, simple: true },
        { name: t('nav.models'), path: '/models', icon: Brain, simple: true },
        { name: t('nav.skills'), path: '/skills', icon: GraduationCap, simple: true },
        { name: t('nav.memory'), path: '/memory', icon: Database, simple: true },
        { name: t('nav.marketplace'), path: '/marketplace', icon: Store, simple: true },
        { name: t('nav.toolbox'), path: '/tools', icon: Wrench, home: '/agents' },
        // Live copies of agents, across every carrier (docs/instances.md).
        { name: t('nav.instances'), path: '/instances', icon: Activity, home: '/agents' },
      ],
    },
    {
      // Attaching something that is not defined in here: this service reaches
      // out to a system you use (Connectors).
      key: 'integrations',
      items: [
        { name: t('nav.connectors'), path: '/connectors', icon: Link2, home: '/settings' },
      ],
    },
    {
      key: 'records',
      items: [
        // What happened: they sit under Settings in the simple menu.
        { name: t('nav.sessions'), path: '/sessions', icon: PlayCircle, home: '/settings' },
        { name: t('nav.messages'), path: '/messages', icon: ScrollText, home: '/settings' },
        { name: t('nav.runGroups'), path: '/run-groups', icon: Layers, home: '/settings' },
        { name: t('nav.webLogs'), path: '/web-logs', icon: Globe, home: '/settings' },
        // Administration lives under Settings.
        { name: t('nav.guardrails'), path: '/guardrails', icon: ShieldCheck, home: '/settings' },
        { name: t('nav.containers'), path: '/containers', icon: Box, home: '/settings' },
        // The service looking at itself.
        { name: t('nav.health'), path: '/health', icon: Activity, home: '/settings' },
        { name: t('nav.settings'), path: '/settings', icon: Settings, simple: true },
        { name: t('nav.docs'), path: '/docs', icon: BookOpen, simple: true },
      ],
    },
  ];
  return groups.map((g) => ({
    ...g,
    label: t(`nav.groups.${g.key}`),
    items: g.items.filter(Boolean),
  }));
}

/** Whether `item` is the row of the page at `pathname`. */
export function isItemActive(item, pathname) {
  const under = (path) => pathname === path || pathname.startsWith(path + '/');
  return under(item.path) || (item.also || []).some(under);
}

/**
 * The groups as the sidebar shows them in `mode`. In the simple menu a group
 * keeps its `simple` items only, whatever page is on screen, and each of them
 * also lights up for the pages whose `home` it is: Deployments opened from
 * the Plan page lights up Plan rather than adding a row. Groups left with
 * nothing are dropped.
 */
export function visibleGroups(groups, mode) {
  if (mode !== SIMPLE) return groups;
  const hidden = groups.flatMap((g) => g.items.filter((it) => !it.simple && it.home));
  return groups
    .map((g) => ({
      ...g,
      items: g.items.filter((it) => it.simple).map((it) => {
        const extra = hidden.filter((h) => h.home === it.path).flatMap((h) => [h.path, ...(h.also || [])]);
        return extra.length ? { ...it, also: [...(it.also || []), ...extra] } : it;
      }),
    }))
    .filter((g) => g.items.length > 0);
}

/** How many rows the full menu has beyond the simple one, for the toggle. */
export function hiddenCount(groups) {
  const all = groups.reduce((n, g) => n + g.items.length, 0);
  const shown = visibleGroups(groups, SIMPLE).reduce((n, g) => n + g.items.length, 0);
  return all - shown;
}
