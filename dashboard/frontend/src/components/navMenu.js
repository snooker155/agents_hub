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
 * changes, and a page reached by a link still lights up its own row (see
 * visibleGroups).
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
  MessageSquareCode, Eye, AudioLines,
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
        { name: t('nav.loops'), path: '/loops', icon: Repeat },
        { name: t('nav.teams'), path: '/teams', icon: UsersRound },
        { name: t('nav.orchestrator'), path: '/orchestrator', icon: Network },
        features.playground !== false && { name: t('nav.playground'), path: '/playground', icon: Gamepad2 },
        { name: t('nav.deployments'), path: '/deployments', icon: Rocket },
        { name: t('nav.workspaces'), path: '/workspaces', icon: Folder },
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
        { name: t('nav.toolbox'), path: '/tools', icon: Wrench },
        // Live copies of agents, across every carrier, and the agents kept
        // running as replicas (docs/instances.md, docs/services.md).
        { name: t('nav.instances'), path: '/instances', icon: Activity },
        { name: t('nav.services'), path: '/services', icon: Cpu },
        { name: t('nav.registry'), path: '/registry', icon: Boxes },
        // Who owns each agent and MCP server, and whether it is approved.
        { name: t('nav.agentRegistry'), path: '/agent-registry', icon: BadgeCheck },
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
        { name: t('nav.connectors'), path: '/connectors', icon: Link2 },
        { name: t('nav.connections'), path: '/connections', icon: Share2 },
        { name: t('nav.watchers'), path: '/watchers', icon: Eye },
        { name: t('nav.mcp'), path: '/mcp', icon: Plug },
        { name: t('nav.widgets'), path: '/widgets', icon: MessageSquareCode },
        // The agent's browser on screen, and free browsing on the same service.
        { name: t('nav.browser'), path: '/browser', icon: Globe },
      ],
    },
    {
      key: 'records',
      items: [
        { name: t('nav.sessions'), path: '/sessions', icon: PlayCircle },
        { name: t('nav.messages'), path: '/messages', icon: ScrollText },
        { name: t('nav.runGroups'), path: '/run-groups', icon: Layers },
        { name: t('nav.costs'), path: '/costs', icon: DollarSign },
        { name: t('nav.evals'), path: '/evals', icon: FlaskConical },
        { name: t('nav.webLogs'), path: '/web-logs', icon: Globe },
        // Who did what: outside single mode there is somebody to answer to.
        auth?.features?.audit && { name: t('nav.audit'), path: '/audit', icon: ScrollText },
        { name: t('nav.guardrails'), path: '/guardrails', icon: ShieldCheck },
        { name: t('nav.environments'), path: '/environments', icon: Container },
        { name: t('nav.containers'), path: '/containers', icon: Box },
        // The service looking at itself, for whoever runs it.
        isOperator(auth) && { name: t('nav.health'), path: '/health', icon: Activity },
        // Members, leases and the launch queue: there is something to show only
        // once the hub runs as more than one process (AGENTS_HUB_ROLE api and
        // worker, docs/workers.md).
        isOperator(auth) && features.cluster && { name: t('nav.cluster'), path: '/cluster', icon: Waypoints },
        // Accounts exist only under AUTH_MODE=multi, and only an administrator
        // manages them.
        isAdmin(auth) && { name: t('nav.users'), path: '/users', icon: UserCog },
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
  if (item.path === '/dashboard') return pathname === item.path;
  return under(item.path) || (item.also || []).some(under);
}

/**
 * The groups as the sidebar shows them in `mode`. In the simple menu a group
 * keeps its `simple` items, plus the row of the page on screen when that page
 * is not one of them: someone who followed a link to Connectors still sees
 * where they are. Groups left with nothing are dropped.
 */
export function visibleGroups(groups, mode, pathname) {
  if (mode !== SIMPLE) return groups;
  return groups
    .map((g) => ({ ...g, items: g.items.filter((it) => it.simple || isItemActive(it, pathname)) }))
    .filter((g) => g.items.length > 0);
}

/** How many rows the full menu has beyond the simple one, for the toggle. */
export function hiddenCount(groups, pathname) {
  const all = groups.reduce((n, g) => n + g.items.length, 0);
  const shown = visibleGroups(groups, SIMPLE, pathname).reduce((n, g) => n + g.items.length, 0);
  return all - shown;
}
