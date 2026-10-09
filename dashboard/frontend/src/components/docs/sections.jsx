/* eslint-disable react-refresh/only-export-components -- a registry, not a component module */
/**
 * Registry of the Docs sections and the corpus id to section lookup.
 */
import {
  Activity, AudioLines, Box, Boxes, Brain, CalendarClock, CheckSquare, ClipboardCheck, Cpu,
  Database, DollarSign, Download, Factory, FileText, FlaskConical, Folder, FolderGit2, Gamepad2,
  Gauge, Globe, GraduationCap, HardDriveDownload, HelpCircle, History, Images, KeyRound, Layers,
  MessageCircle, MessageSquareCode, Navigation, Network, Plug, Radio, Repeat, Rocket,
  ScrollText, Send, Server, ServerCog, SettingsIcon, Share2, ShieldCheck, Sparkles, Store,
  Target, Terminal, Users, UsersRound, Webhook, Wrench,
} from 'lucide-react';
import { ChangelogDoc, Faq, GuideDoc } from './CorpusDocs';
import {
  AgentsDoc, FlowsDoc, ImportingAgentsDoc, MarketplaceDoc, ProjectsDoc, SkillsDoc, TasksDoc,
  ViewsDoc, WebLogsDoc, WorkspacesDoc,
} from './FeatureDocs';
import { CliApi, Concepts, Features, GettingStarted, TryIt, Tutorials } from './GettingStartedDocs';
import {
  ConnectorsDoc, ContainersDoc, CostsDoc, EvalsDoc, HooksDoc, InstancesDoc, LoopsDoc, MemoryDoc,
  ModelsDoc, OrchestratorDoc, PlanDoc, PlaygroundDoc, ProvidersDoc, TeamsDoc, ToolboxDoc,
} from './PlatformDocs';
import { CliGuide, Installation } from './SetupDocs';

// Registry entry for a GuideDoc section: its label is docsGuide.<k>.nav.
const guide = (id, k, icon, refs) => ({
  id, navKey: `docsGuide.${k}.nav`, icon, refs, render: () => <GuideDoc k={k} refs={refs} />,
});

// Section registry. Labels are translation keys (`docs.nav.*`) resolved at
// render time — a plain string here would keep its language when the switcher
// changes, which is exactly what used to happen.
export const GROUPS = [
  {
    key: 'startHere',
    items: [
      { id: 'getting-started', key: 'gettingStarted', icon: Rocket, render: GettingStarted },
      { id: 'installation', key: 'installation', icon: HardDriveDownload, render: Installation },
      { id: 'concepts', key: 'concepts', icon: Boxes, render: Concepts },
      { id: 'features', key: 'overview', icon: Sparkles, render: Features },
      { id: 'changelog', key: 'changelog', icon: ScrollText, render: ChangelogDoc },
    ],
  },
  {
    key: 'workspace',
    items: [
      { id: 'chat', key: 'chat', icon: MessageCircle, render: TryIt },
      guide('assistant', 'assistant', AudioLines, ['assistant']),
      { id: 'workspaces', key: 'workspaces', icon: Folder, render: WorkspacesDoc },
      { id: 'projects', key: 'projects', icon: FolderGit2, render: ProjectsDoc },
      guide('project-deployments', 'projectDeployments', Rocket, ['project-deployments']),
      guide('files', 'files', FileText, ['files']),
      { id: 'tasks', key: 'tasks', icon: CheckSquare, render: TasksDoc },
      { id: 'plan', key: 'plan', icon: CalendarClock, render: PlanDoc },
      { id: 'views', key: 'views', icon: Images, render: ViewsDoc },
    ],
  },
  {
    key: 'agents',
    items: [
      { id: 'agents', key: 'agents', icon: Users, render: AgentsDoc },
      { id: 'importing-agents', key: 'importingAgents', icon: Download, render: ImportingAgentsDoc },
      { id: 'marketplace', key: 'marketplace', icon: Store, render: MarketplaceDoc },
      { id: 'skills', key: 'skills', icon: GraduationCap, render: SkillsDoc },
      { id: 'orchestrator', key: 'orchestrator', icon: Network, render: OrchestratorDoc },
      { id: 'teams', key: 'teams', icon: UsersRound, render: TeamsDoc },
      guide('registry', 'registry', ClipboardCheck, ['registry']),
      guide('agent-loop', 'agentLoop', Gauge, ['agent-loop', 'tool-policy', 'guardrails']),
      guide('steering', 'steering', Navigation, ['steering', 'handoffs', 'page-chat']),
    ],
  },
  {
    key: 'automation',
    items: [
      { id: 'flows', key: 'flows', icon: Factory, render: FlowsDoc },
      { id: 'loops', key: 'loops', icon: Repeat, render: LoopsDoc },
      { id: 'tools', key: 'tools', icon: Wrench, render: ToolboxDoc },
      guide('mcp', 'mcp', Plug, ['mcp']),
      guide('browser', 'browser', Globe, ['browser']),
      { id: 'hooks', key: 'hooks', icon: ShieldCheck, render: HooksDoc },
      { id: 'memory', key: 'memory', icon: Database, render: MemoryDoc },
      { id: 'web-logs', key: 'webLogs', icon: Globe, render: WebLogsDoc },
      { id: 'evals', key: 'evals', icon: FlaskConical, render: EvalsDoc },
      guide('outcomes', 'outcomes', Target, ['outcomes', 'experiments']),
      guide('sessions-runs', 'sessionsRuns', History, ['sessions-and-runs']),
      { id: 'playground', key: 'playground', icon: Gamepad2, render: PlaygroundDoc },
    ],
  },
  {
    key: 'operations',
    items: [
      { id: 'instances', key: 'instances', icon: Radio, render: InstancesDoc },
      guide('services', 'services', Server, ['services']),
      guide('deployments', 'deployments', Layers, ['deployments', 'environments', 'sandboxes']),
      { id: 'containers', key: 'containers', icon: Box, render: ContainersDoc },
      { id: 'models', key: 'models', icon: Brain, render: ModelsDoc },
      guide('local-models', 'localModels', Cpu, ['local-models', 'hub-as-provider', 'model-structure']),
      { id: 'costs', key: 'costs', icon: DollarSign, render: CostsDoc },
      { id: 'providers', key: 'providers', icon: SettingsIcon, render: ProvidersDoc },
      { id: 'connectors', key: 'connectors', icon: Send, render: ConnectorsDoc },
      guide('health', 'health', Activity, ['service-health', 'runbook', 'slo', 'system-workspace']),
      guide('production', 'production', ServerCog, ['deployment', 'scaling', 'workers', 'storage', 'backup']),
    ],
  },
  {
    key: 'access',
    items: [
      guide('accounts', 'accounts', KeyRound, ['identity', 'sso', 'scim', 'api-keys', 'secrets', 'audit']),
      guide('widget', 'widget', MessageSquareCode, ['widget']),
      guide('integrations', 'integrations', Share2, ['notifications', 'telegram', 'github-app', 'a2a', 'connections']),
    ],
  },
  {
    key: 'guides',
    items: [
      { id: 'tutorials', key: 'tutorials', icon: GraduationCap, render: Tutorials },
      { id: 'cli', key: 'cli', icon: Terminal, render: CliGuide },
      { id: 'cli-api', key: 'cliApi', icon: Webhook, render: CliApi },
      { id: 'faq', key: 'faq', icon: HelpCircle, render: Faq },
    ],
  },
];

export const SECTIONS = GROUPS.flatMap((g) => g.items);

// Corpus id -> the section that shows it: a guide section listing it as a
// reference, a hand-written section covering it under another id, else a
// section of the same id.
const CORPUS_SECTION = new Map([
  ['overview', 'features'],
  ['tools-and-capabilities', 'tools'],
  ['imported-agents', 'importing-agents'],
  ['scheduling', 'plan'],
  ['troubleshooting', 'faq'],
  ['system-agents', 'orchestrator'],
]);
SECTIONS.forEach((s) => (s.refs || []).forEach((id) => {
  if (!CORPUS_SECTION.has(id)) CORPUS_SECTION.set(id, s.id);
}));
export function sectionForCorpus(id) {
  return CORPUS_SECTION.get(id) || (SECTIONS.some((s) => s.id === id) ? id : null);
}
