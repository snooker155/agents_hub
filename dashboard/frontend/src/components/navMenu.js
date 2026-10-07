/**
 * The sidebar's map: five groups along the object tree of docs/overview.md,
 * and two ways of showing it.
 *
 * - conversation: where a person talks (the assistant, the chat) and the
 *   overview of what is going on.
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
  Waypoints, LayoutDashboard, CheckSquare, UserCog, KeyRound, Folder, Database, Factory, Wrench, Users,
  Activity, PlayCircle, MessageCircle, ScrollText, Settings, Network, Cpu, FolderGit2, Box, Boxes, Store,
  CalendarClock, BookOpen, Brain, DollarSign, Images, FlaskConical, Gamepad2, Repeat, UsersRound,
  GraduationCap, Globe, Share2, Link2, Plug, Layers, Container, Rocket, ShieldCheck, BadgeCheck,
  MessageSquareCode, Eye, AudioLines, Send,
} from 'lucide-react';
import { MULTI, isAdmin } from './auth';

export const MENU_MODE_KEY = 'agents_hub_menu_mode';
export const SIMPLE = 'simple';
export const FULL = 'full';

/**
 * The mode a viewer starts in before choosing one: the full menu for the
 * administrator of a multi-user hub (the person who runs it for others), the
 * simple one for everybody else, including the single operator of a home
 * install.
 */
export function defaultMenuMode(auth) {
  return auth?.mode === MULTI && isAdmin(auth) ? FULL : SIMPLE;
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
 * Who runs this hub, for the rows only an operator needs (Status, Cluster):
 * the one operator of a single or token install, or an administrator of a
 * multi-user one.
 */
const isOperator = (auth) => auth?.mode !== MULTI || isAdmin(auth);

/**
 * The groups, every item in them, with `simple` on the items the simple menu
 * keeps. Items a viewer may not see at all (Accounts without the admin role,
 * Audit outside the modes that keep one, the playground switched off) are
 * left out here rather than hidden later, so both modes agree on them.
 */
export function buildMenu({ t, auth, features = {} }) {
  const groups = [
    {
      key: 'conversation',
      items: [
        // One agent for the whole service, by voice or text (docs/assistant.md).
        { name: t('nav.assistant'), path: '/assistant', icon: AudioLines, simple: true },
        { name: t('nav.chat'), path: '/chat', icon: MessageCircle, simple: true },
        { name: t('nav.dashboard'), path: '/dashboard', icon: LayoutDashboard, simple: true },
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
        features.playground !== false && { name: t('nav.playground'), path: '/playground', icon: Gamepad2, home: '/flows' },
        // Schedules of work, opened from the Plan page.
        { name: t('nav.deployments'), path: '/deployments', icon: Rocket, home: '/plan' },
        // Creating a workspace, its settings and its members: the top bar only
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
        // Live copies of agents, across every carrier, and the agents kept
        // running as replicas (docs/instances.md, docs/services.md).
        { name: t('nav.instances'), path: '/instances', icon: Activity, home: '/agents' },
        { name: t('nav.services'), path: '/services', icon: Cpu, home: '/agents' },
        { name: t('nav.registry'), path: '/registry', icon: Boxes, home: '/marketplace' },
        // Who owns each agent and MCP server, and whether it is approved.
        { name: t('nav.agentRegistry'), path: '/agent-registry', icon: BadgeCheck, home: '/agents' },
      ],
    },
    {
      // Attaching something that is not defined in here. Two directions, one
      // question: something of yours runs elsewhere and reports in
      // (Connections), or this service reaches out to a system you use
      // (Connectors); watchers observe outside state, MCP servers hand over
      // tools nobody here has seen, a widget puts an agent on another site.
      key: 'integrations',
      items: [
        { name: t('nav.connectors'), path: '/connectors', icon: Link2, home: '/settings' },
        { name: t('nav.connections'), path: '/connections', icon: Share2, home: '/settings' },
        { name: t('nav.watchers'), path: '/watchers', icon: Eye, home: '/settings' },
        { name: t('nav.mcp'), path: '/mcp', icon: Plug, home: '/settings' },
        { name: t('nav.widgets'), path: '/widgets', icon: MessageSquareCode, home: '/settings' },
        // The hub where people already work: IDE, notes, Slack, Teams.
        { name: t('nav.distribution'), path: '/distribution', icon: Send, home: '/settings' },
        // The agent's browser on screen, and free browsing on the same service.
        { name: t('nav.browser'), path: '/browser', icon: Globe, home: '/agents' },
      ],
    },
    {
      key: 'records',
      items: [
        // What happened and what it cost: the Dashboard is their summary.
        { name: t('nav.sessions'), path: '/sessions', icon: PlayCircle, home: '/dashboard' },
        { name: t('nav.messages'), path: '/messages', icon: ScrollText, home: '/dashboard' },
        { name: t('nav.runGroups'), path: '/run-groups', icon: Layers, home: '/dashboard' },
        { name: t('nav.costs'), path: '/costs', icon: DollarSign, home: '/dashboard' },
        { name: t('nav.evals'), path: '/evals', icon: FlaskConical, home: '/agents' },
        { name: t('nav.webLogs'), path: '/web-logs', icon: Globe, home: '/dashboard' },
        // Who did what: outside single mode there is somebody to answer to.
        auth?.features?.audit && { name: t('nav.audit'), path: '/audit', icon: ScrollText, home: '/settings' },
        // Administration lives under Settings.
        { name: t('nav.guardrails'), path: '/guardrails', icon: ShieldCheck, home: '/settings' },
        { name: t('nav.environments'), path: '/environments', icon: Container, home: '/settings' },
        { name: t('nav.containers'), path: '/containers', icon: Box, home: '/settings' },
        // The service looking at itself, for whoever runs it.
        isOperator(auth) && { name: t('nav.health'), path: '/health', icon: Activity, home: '/settings' },
        // Members, leases and the launch queue: there is something to show only
        // once the hub runs as more than one process (AGENTS_HUB_ROLE api and
        // worker, docs/workers.md).
        isOperator(auth) && features.cluster && { name: t('nav.cluster'), path: '/cluster', icon: Waypoints, home: '/settings' },
        // Accounts exist only under AUTH_MODE=multi, and only an administrator
        // manages them.
        isAdmin(auth) && { name: t('nav.users'), path: '/users', icon: UserCog, home: '/settings' },
        // The viewer's own sessions and API keys.
        auth?.mode === MULTI && auth?.user && { name: t('nav.account'), path: '/account', icon: KeyRound, simple: true },
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
  const own = item.path === '/dashboard' ? pathname === item.path : under(item.path);
  return own || (item.also || []).some(under);
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
