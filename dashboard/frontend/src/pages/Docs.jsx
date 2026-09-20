import { useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import {
  Rocket,
  Boxes,
  Sparkles,
  GraduationCap,
  Terminal,
  HelpCircle,
  Copy,
  Check,
  ArrowRight,
  Folder,
  FolderGit2,
  CheckSquare,
  Users,
  Network,
  Server,
  Radio,
  Factory,
  Database,
  MessageCircle,
  Store,
  Wrench,
  Globe,
  Settings as SettingsIcon,
  Download,
  Images,
  CalendarClock,
  UsersRound,
  Repeat,
  FlaskConical,
  Gamepad2,
  Brain,
  DollarSign,
  Box,
  Send,
  GitBranch,
  BookOpen,
  HardDriveDownload,
  Webhook,
} from 'lucide-react';
import OnboardingChecklist from '../components/docs/OnboardingChecklist';
import {
  SyntheticChat,
  WorkspaceModelExample,
  ProjectCardExample,
  TaskLifecycleExample,
  AgentDefinitionExample,
  ImportedAgentExample,
  MarketplaceExample,
  FlowDiagramExample,
  ToolExample,
  MemoryExample,
  NodeSessionExample,
  OrchestratorExample,
  ProviderExample,
} from '../components/docs/ExampleWidgets';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
// ---------------------------------------------------------------------------
// Docs — an in-app documentation hub with its own left-hand section nav.
// Content is authored as JSX (rather than markdown files) so interactive
// widgets can be embedded directly inside the prose. Those widgets are
// synthetic by design (see components/docs/ExampleWidgets) — they teach a
// feature without depending on, or exposing, the reader's live instance. The
// one live element is OnboardingChecklist, which probes the running backend.
//   /docs                  -> Getting Started
//   /docs/:section         -> a specific section
// ---------------------------------------------------------------------------

// ---- small presentational helpers -----------------------------------------

function CodeBlock({ children, label }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(children).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <div className="relative group my-3">
      {label && <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-400 mb-1">{label}</p>}
      <pre className="bg-gray-900 text-gray-100 rounded-xl p-4 text-xs overflow-x-auto leading-relaxed">
        <code>{children}</code>
      </pre>
      <button
        onClick={copy}
        className="absolute top-2 right-2 p-1.5 rounded-lg bg-white/10 text-gray-300 hover:bg-white/20 opacity-0 group-hover:opacity-100 transition-opacity"
        title={t('docs.copy')}
      >
        {copied ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
      </button>
    </div>
  );
}

function Callout({ children, tone = 'info' }) {
  const tones = {
    info: 'bg-indigo-50 border-indigo-200 text-indigo-900',
    warn: 'bg-amber-50 border-amber-200 text-amber-900',
    tip: 'bg-emerald-50 border-emerald-200 text-emerald-900',
  };
  return <div className={`border rounded-xl px-4 py-3 text-sm my-3 ${tones[tone]}`}>{children}</div>;
}

function H2({ children, id }) {
  return <h2 id={id} className="text-xl font-bold text-gray-900 mt-8 mb-3 first:mt-0">{children}</h2>;
}
function H3({ children }) {
  return <h3 className="text-base font-semibold text-gray-900 mt-5 mb-2">{children}</h3>;
}
function P({ children }) {
  return <p className="text-sm text-gray-600 leading-relaxed my-2">{children}</p>;
}

// Prose in this page comes from the locale files, so a paragraph has to be one
// translation key rather than a dozen fragments glued together by JSX (which is
// how half of it stopped following the language switcher). `Rich` renders the
// small inline vocabulary those strings are allowed to use:
//   `code`   **bold**   *emphasis*   [label](/route)   [label](https://…)
const RICH_TOKEN = /(\[[^\]]+\]\([^)\s]+\)|`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g;

function Rich({ children }) {
  const text = typeof children === 'string' ? children : String(children ?? '');
  return (
    <>
      {text.split(RICH_TOKEN).filter(Boolean).map((part, i) => {
        const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part);
        if (link) {
          const [, label, href] = link;
          return href.startsWith('http')
            ? <a key={i} className="text-indigo-600 underline" href={href} target="_blank" rel="noreferrer">{label}</a>
            : <Link key={i} className="text-indigo-600 underline" to={href}>{label}</Link>;
        }
        if (part.startsWith('`') && part.endsWith('`')) {
          return <code key={i} className="bg-gray-100 px-1 rounded text-[0.92em]">{part.slice(1, -1)}</code>;
        }
        if (part.startsWith('**') && part.endsWith('**')) return <strong key={i}>{part.slice(2, -2)}</strong>;
        if (part.startsWith('*') && part.endsWith('*')) return <em key={i}>{part.slice(1, -1)}</em>;
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

function FeatureCard({ icon, title, to, children }) {
  const Icon = icon;
  const navigate = useNavigate();
  return (
    <button
      onClick={() => navigate(to)}
      className="text-left p-4 rounded-xl border border-gray-200 bg-white hover:border-indigo-300 hover:shadow-sm transition-all group"
    >
      <div className="flex items-center gap-2 mb-1.5">
        <Icon className="w-4 h-4 text-indigo-500" />
        <span className="font-semibold text-sm text-gray-900">{title}</span>
        <ArrowRight className="w-3.5 h-3.5 text-gray-300 group-hover:text-indigo-500 ml-auto transition-colors" />
      </div>
      <p className="text-xs text-gray-500 leading-relaxed">{children}</p>
    </button>
  );
}

function Walkthrough({ title, steps }) {
  return (
    <div className="my-4 rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-sm font-semibold text-gray-800">{title}</div>
      <ol className="divide-y divide-gray-100">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-3 px-4 py-3">
            <span className="w-6 h-6 rounded-full bg-indigo-100 text-indigo-600 text-xs font-bold flex items-center justify-center shrink-0">
              {i + 1}
            </span>
            <div className="text-sm text-gray-600">{s}</div>
          </li>
        ))}
      </ol>
    </div>
  );
}

// ---- section content -------------------------------------------------------

function GettingStarted() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.nav.gettingStarted')}</H2>
      <P><Rich>{t('docs.start.lead')}</Rich></P>
      <div className="my-4">
        <OnboardingChecklist />
      </div>

      <Walkthrough
        title={t('docs.start.firstRun')}
        steps={[0, 1, 2, 3].map((i) => <Rich key={i}>{t(`docs.start.firstRun${i}`)}</Rich>)}
      />

      <H2>{t('docs.installRun')}</H2>
      <P>{t('docs.ifTheBackendIsntOnline')}</P>
      <H3>{t('docs.backendFastapi')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`python -m venv .venv
source .venv/bin/activate
pip install -r dashboard/backend/requirements.txt
pip install -r requirements-agents.txt
python -m uvicorn dashboard.backend.main:app --host 0.0.0.0 --port 8000 --reload`}</CodeBlock>
      <H3>{t('docs.frontendReactVite')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`cd dashboard/frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173`}</CodeBlock>
      <P>{t('docs.backendHttpLocalhost8000Frontend')}</P>
      <H3>{t('docs.orWithDocker')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`docker compose up --build`}</CodeBlock>
      <P>{t('docs.dockerDashboard8080')}</P>

      <H2>{t('docs.configureAProvider')}</H2>
      <P><Rich>{t('docs.start.providersLead')}</Rich></P>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><Rich>{t('docs.start.settingsHolds')}</Rich></li>
        <li><Rich>{t('docs.start.modelsHolds')}</Rich></li>
      </ul>
      <P><Rich>{t('docs.start.bothPagesWrite')}</Rich></P>
      <CodeBlock label=".env">{`DEFAULT_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5
LLM_TEMPERATURE=0.0
TASK_ASSIGNMENT_MODE=any
AGENT_EXECUTION_MODE=local`}</CodeBlock>
      <Callout tone="tip"><Rich>{t('docs.start.pickerTip')}</Rich></Callout>

      <H2>{t('docs.start.whereNext')}</H2>
      <P><Rich>{t('docs.start.whereNextLead')}</Rich></P>
      <div className="grid sm:grid-cols-2 gap-3 my-3">
        <FeatureCard icon={Radio} title={t('docs.instancesDoc.title')} to="/instances">
          {t('docs.start.nextInstances')}
        </FeatureCard>
        <FeatureCard icon={Images} title={t('docs.viewsStudio')} to="/views">
          {t('docs.start.nextViews')}
        </FeatureCard>
        <FeatureCard icon={CalendarClock} title={t('docs.nav.plan')} to="/plan">
          {t('docs.start.nextPlan')}
        </FeatureCard>
        <FeatureCard icon={UsersRound} title={t('docs.teams')} to="/teams">
          {t('docs.start.nextTeams')}
        </FeatureCard>
        <FeatureCard icon={Repeat} title={t('docs.loops')} to="/loops">
          {t('docs.start.nextLoops')}
        </FeatureCard>
        <FeatureCard icon={FlaskConical} title={t('docs.evals')} to="/evals">
          {t('docs.start.nextEvals')}
        </FeatureCard>
        <FeatureCard icon={GraduationCap} title={t('docs.skillsCatalog')} to="/skills">
          {t('docs.start.nextSkills')}
        </FeatureCard>
        <FeatureCard icon={Download} title={t('docs.nav.importingAgents')} to="/agents">
          {t('docs.start.nextImported')}
        </FeatureCard>
        <FeatureCard icon={DollarSign} title={t('docs.nav.costs')} to="/costs">
          {t('docs.start.nextCosts')}
        </FeatureCard>
        <FeatureCard icon={Send} title={t('docs.nav.connectors')} to="/settings/telegram">
          {t('docs.start.nextConnectors')}
        </FeatureCard>
      </div>
    </div>
  );
}

function Concepts() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.coreConcepts')}</H2>
      <P>{t('docs.aHandfulOfObjectsMake')}</P>

      <H3><Folder className="inline w-4 h-4 text-blue-500 mr-1" /> {t('docs.workspaces')}</H3>
      <P><Rich>{t('docs.conceptsDoc.workspaces')}</Rich></P>

      <H3><FolderGit2 className="inline w-4 h-4 text-blue-500 mr-1" /> {t('docs.projects')}</H3>
      <P><Rich>{t('docs.conceptsDoc.projects')}</Rich></P>

      <H3><CheckSquare className="inline w-4 h-4 text-emerald-500 mr-1" /> {t('docs.tasks')}</H3>
      <P><Rich>{t('docs.conceptsDoc.tasks')}</Rich></P>

      <H3><Users className="inline w-4 h-4 text-indigo-500 mr-1" /> {t('docs.agents')}</H3>
      <P><Rich>{t('docs.conceptsDoc.agents')}</Rich></P>
      <Callout><Rich>{t('docs.conceptsDoc.agentsCallout')}</Rich></Callout>

      <H3><Network className="inline w-4 h-4 text-indigo-500 mr-1" /> {t('docs.nodesRuns')}</H3>
      <P>
        <strong>{t('docs.node')}</strong> {t('docs.isALongRunningAgent')} <strong>{t('docs.run')}</strong> {t('docs.runCapturesASingleExecution')}
      </P>

      <H3><Radio className="inline w-4 h-4 text-indigo-500 mr-1" /> {t('docs.instancesDoc.title')}</H3>
      <P>{t('docs.instancesDoc.lead')}</P>
      <P>{t('docs.instancesDoc.points2')}</P>
      <Callout>
        {t('docs.instancesDoc.points3')}{' '}
        <Link className="text-indigo-600 underline" to="/instances">{t('docs.instancesDoc.openPage')}</Link>
      </Callout>

      <H3><Factory className="inline w-4 h-4 text-indigo-500 mr-1" /> {t('docs.flowsLoopsTeams')}</H3>
      <P>
        {t('docs.threeWaysIntro')} <strong>{t('docs.flow')}</strong> {t('docs.flowIsADag')}{' '}
        <strong>{t('docs.loop')}</strong> {t('docs.loopWrapsAFlow')} <strong>{t('docs.team')}</strong>{' '}
        {t('docs.teamIsAFixedRoster')}
      </P>

      <H3><Images className="inline w-4 h-4 text-indigo-500 mr-1" /> {t('docs.views')}</H3>
      <P><Rich>{t('docs.conceptsDoc.views')}</Rich></P>

      <H3><CalendarClock className="inline w-4 h-4 text-indigo-500 mr-1" /> {t('docs.scheduledJobs')}</H3>
      <P><Rich>{t('docs.conceptsDoc.scheduledJobs')}</Rich></P>

      <H3><Database className="inline w-4 h-4 text-indigo-500 mr-1" /> {t('docs.memory')}</H3>
      <P>{t('docs.agentsHaveFiveComplementaryMemory')}</P>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><strong>{t('docs.shared')}</strong> {t('docs.notesStructuredSlotsAndA')}<code className="bg-gray-100 px-1 rounded">recall</code> / <code className="bg-gray-100 px-1 rounded">remember</code> / <code className="bg-gray-100 px-1 rounded">forget</code>).</li>
        <li><strong>{t('docs.episodic')}</strong> {t('docs.discreteEventsInteractionTaskDecision')}</li>
        <li><strong>{t('docs.procedural')}</strong> {t('docs.reusableSkillsSurfacedByRelevance')}</li>
        <li><strong>{t('docs.knowledgeGraph')}</strong> {t('docs.typedEntitiesAndLabelledRelations')}</li>
        <li><strong>{t('docs.rag')}</strong> {t('docs.retrievalOverIndexedWorkspaceDocuments')}</li>
      </ul>
    </div>
  );
}

function Features() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.featuresAtAGlance')}</H2>
      <P><Rich>{t('docs.featuresDoc.lead')}</Rich></P>

      <H2>{t('docs.exploreTheSurfaces')}</H2>
      <div className="grid sm:grid-cols-2 gap-3 my-3">
        <FeatureCard icon={MessageCircle} title={t('docs.chat')} to="/chat">
          {t('docs.talkToAnyAgentDirectly')}
        </FeatureCard>
        <FeatureCard icon={CheckSquare} title={t('docs.tasks')} to="/tasks">
          {t('docs.featuresDoc.tasksCard')}
        </FeatureCard>
        <FeatureCard icon={Users} title={t('docs.agents')} to="/agents">
          {t('docs.editLayeredInstructionsSwapModels')}
        </FeatureCard>
        <FeatureCard icon={Store} title={t('docs.marketplace')} to="/marketplace">
          {t('docs.browseAndCloneReadyMade')}
        </FeatureCard>
        <FeatureCard icon={Factory} title={t('docs.agentFlows')} to="/flows">
          {t('docs.buildMultiAgentGraphBased')}
        </FeatureCard>
        <FeatureCard icon={Database} title={t('docs.sharedMemory')} to="/memory">
          {t('docs.curateNotesSlotsEpisodesSkills')}
        </FeatureCard>
        <FeatureCard icon={Radio} title={t('docs.instancesDoc.title')} to="/instances">
          {t('docs.instancesDoc.points0')}
        </FeatureCard>
        <FeatureCard icon={Server} title={t('docs.nodes')} to="/nodes">
          {t('docs.manageLongRunningAgentProcesses')}
        </FeatureCard>
        <FeatureCard icon={Network} title={t('docs.orchestrator')} to="/orchestrator">
          {t('docs.configureRoutingSoTasksAre')}
        </FeatureCard>
        <FeatureCard icon={Images} title={t('docs.viewsStudio')} to="/views">
          {t('docs.chartsGraphs3dScenesAnd')}
        </FeatureCard>
        <FeatureCard icon={CalendarClock} title={t('docs.plan')} to="/plan">
          {t('docs.featuresDoc.planCard')}
        </FeatureCard>
        <FeatureCard icon={UsersRound} title={t('docs.teams')} to="/teams">
          {t('docs.aBoundedRosterOfAgents')}
        </FeatureCard>
        <FeatureCard icon={Repeat} title={t('docs.loops')} to="/loops">
          {t('docs.reRunAFlowUntil')}
        </FeatureCard>
        <FeatureCard icon={FlaskConical} title={t('docs.evals')} to="/evals">
          {t('docs.datasetsGradersAndModelSweeps')}
        </FeatureCard>
        <FeatureCard icon={Gamepad2} title={t('docs.playground')} to="/playground">
          {t('docs.dropAgentsIntoASimulated')}
        </FeatureCard>
        <FeatureCard icon={Brain} title={t('docs.models')} to="/models">
          {t('docs.curateTheModelCatalogIts')}
        </FeatureCard>
        <FeatureCard icon={DollarSign} title={t('docs.costs')} to="/costs">
          {t('docs.tokenAndUsdSpendBy')}
        </FeatureCard>
      </div>
    </div>
  );
}

function TryIt() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.chat')}</H2>
      <P><Rich>{t('docs.chatDoc.lead')}</Rich></P>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><Rich>{t('docs.chatDoc.modes')}</Rich></li>
        <li>{t('docs.attachFilesAndOptionallyStore')}</li>
        <li><Rich>{t('docs.attachHubEntities')}</Rich></li>
        <li>{t('docs.toolCallsAndRetrievedSkills')}</li>
        <li><Rich>{t('docs.chatDoc.viewInline')}</Rich></li>
        <li>{t('docs.eachConversationIsScopedTo')}</li>
      </ul>
      <Callout tone="tip">{t('docs.interactiveExampleBelowAScripted')}</Callout>
      <div className="my-4">
        <SyntheticChat />
      </div>
      <P>
        {t('docs.whenYoureReadyOpen')} <Link className="text-indigo-600 underline" to="/chat">{t('docs.chat')}</Link> {t('docs.pageToTalkToYourOwn')}
      </P>
    </div>
  );
}

function Tutorials() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.guidedWalkthroughs')}</H2>
      <P>{t('docs.shortEndToEndRecipes')}</P>

      <Walkthrough
        title={t('docs.runYourFirstTask')}
        steps={[
          <>{t('docs.open')} <Link className="text-indigo-600 underline" to="/workspaces">{t('docs.workspaces')}</Link> {t('docs.andCreateOneOrUse')} <code className="bg-gray-100 px-1 rounded">{t('docs.default')}</code>{t('docs.thenSelectItInThe')}</>,
          <>{t('docs.goTo')} <Link className="text-indigo-600 underline" to="/tasks">{t('docs.tasks')}</Link> {t('docs.andClick')} <strong>{t('docs.newTask')}</strong>{t('docs.giveItAClearInstruction')}</>,
          <>{t('docs.assignAnAgentOrLet')} <code className="bg-gray-100 px-1 rounded">{t('docs.taskAssignmentMode')}</code> {t('docs.isSetToAuto')}</>,
          <>{t('docs.runItAndOpenThe')}</>,
        ]}
      />

      <Walkthrough
        title={t('docs.createYourOwnAgent')}
        steps={[
          <>{t('docs.open')} <Link className="text-indigo-600 underline" to="/agents">{t('docs.agents')}</Link> → <strong>{t('docs.create')}</strong>{t('docs.orCloneOneFromThe')} <Link className="text-indigo-600 underline" to="/marketplace">{t('docs.marketplace')}</Link>.</>,
          <>{t('docs.editTheThreePromptLayers')} <code className="bg-gray-100 px-1 rounded">{t('docs.instructionsMd')}</code>, <code className="bg-gray-100 px-1 rounded">{t('docs.capabilitiesMd')}</code>, <code className="bg-gray-100 px-1 rounded">{t('docs.usageMd')}</code>.</>,
          <>{t('docs.pickAModelProviderBind')}</>,
          <>{t('docs.testItFrom')} <Link className="text-indigo-600 underline" to="/chat">{t('docs.chat')}</Link>{t('docs.thenAssignItToA')}</>,
        ]}
      />

      <Walkthrough
        title={t('docs.buildAndRunAFlow')}
        steps={[
          <>{t('docs.open')} <Link className="text-indigo-600 underline" to="/flows">{t('docs.agentFlows')}</Link> → <strong>{t('docs.newFlow')}</strong>.</>,
          <>{t('docs.dragNodesFromThe')} <Link className="text-indigo-600 underline" to="/registry">{t('docs.registry')}</Link> {t('docs.paletteOntoTheCanvasAgents')}</>,
          <>{t('docs.configureEachNodesAgentAnd')}</>,
          <>{t('docs.runItFromChatIn')}</>,
        ]}
      />

      <Walkthrough
        title={t('docs.keepReRunningAFlow')}
        steps={[
          <>{t('docs.open')} <Link className="text-indigo-600 underline" to="/loops">{t('docs.loops')}</Link> → <strong>{t('docs.newLoop')}</strong> {t('docs.andPickTheFlowTo')}</>,
          <>{t('docs.writeTheExitCriterion')}</>,
          <>{t('docs.setTheBoundsThatStop')}</>,
          <><strong>{t('docs.estimate')}</strong>{t('docs.thenRunAndReadThe')}</>,
        ]}
      />

      <Walkthrough
        title={t('docs.getAChartInsteadOf')}
        steps={[
          <>{t('docs.bindTheVisualizationTools')} <code className="bg-gray-100 px-1 rounded">{t('docs.createView')}</code>{t('docs.toTheAgentOnIts')} <Link className="text-indigo-600 underline" to="/agents">{t('docs.agents')}</Link> {t('docs.page')}</>,
          <>{t('docs.askItSomethingWorthDrawing')} <Link className="text-indigo-600 underline" to="/chat">{t('docs.chat')}</Link> {t('docs.theViewRendersInlineIn')}</>,
          <>{t('docs.openItFromThe')} <Link className="text-indigo-600 underline" to="/views">{t('docs.views')}</Link> {t('docs.galleryOrPress')} <strong>{t('docs.studio')}</strong> {t('docs.toKeepEditingItBy')}</>,
          <>{t('docs.in')} <Link className="text-indigo-600 underline" to="/studio">{t('docs.studio')}</Link>{t('docs.selectAnObjectInThe')}</>,
        ]}
      />

      <Walkthrough
        title={t('docs.proveAPromptChangeActually')}
        steps={[
          <>{t('docs.findARunThatWent')} <Link className="text-indigo-600 underline" to="/messages">{t('docs.messages')}</Link> {t('docs.andTurnItIntoAn')}</>,
          <>{t('docs.addGradersOnThe')} <Link className="text-indigo-600 underline" to="/evals">{t('docs.evals')}</Link> {t('docs.pageSubstringOrJsonChecks')} <code className="bg-gray-100 px-1 rounded">llm_judge</code> {t('docs.onlyWhereARuleCannot')}</>,
          <>{t('docs.press')} <strong>{t('docs.estimate')}</strong> {t('docs.toSeeWhatTheSweep')}</>,
          <>{t('docs.changeThePromptReRun')}</>,
        ]}
      />

      <Walkthrough
        title={t('docs.putACapOnSpend')}
        steps={[
          <>{t('docs.priceTheModelsYouUse')} <Link className="text-indigo-600 underline" to="/models">{t('docs.models')}</Link> {t('docs.catalogCostNumbersAreOnly')}</>,
          <>{t('docs.open')} <Link className="text-indigo-600 underline" to="/costs">{t('docs.costs')}</Link> {t('docs.andReadTheBreakdownBy')}</>,
          <>{t('docs.setASoftLimitTo')}</>,
          <>{t('docs.aRunThatWouldCross')}</>,
        ]}
      />
    </div>
  );
}

function CliApi() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.apiSurface')}</H2>
      <P>{t('docs.theBackendIsOrganisedBy')} <code className="bg-gray-100 px-1 rounded">{t('docs.api')}</code>:</P>
      <div className="flex flex-wrap gap-1.5 my-3">
        {['agents', 'agent-import', 'marketplace', 'tasks', 'plan', 'flows', 'flow-entities', 'loops',
          'teams', 'stats', 'models', 'costs', 'evals', 'playground', 'shared-memory', 'skills',
          'web-logs', 'workspaces', 'tools', 'sessions', 'messages', 'chat', 'nodes', 'external',
          'projects', 'containers', 'settings', 'telegram', 'git', 'views', 'stream', 'health'].map((r) => (
          <span key={r} className="text-xs font-mono px-2 py-1 rounded-md bg-gray-100 text-gray-700">/{r}</span>
        ))}
      </div>
      <P>{t('docs.twoEndpointsAreWorthKnowing')}</P>
      <CodeBlock>{`curl http://localhost:8000/           # API banner + the route domains it serves
curl http://localhost:8000/api/health # DB reachability, row counts, background services, state size`}</CodeBlock>
      <P>
        <code className="bg-gray-100 px-1 rounded">{t('docs.apiHealth')}</code> {t('docs.apiHealthExplains')}
      </P>
      <H3>{t('docs.liveUpdates')}</H3>
      <P>
        {t('docs.dashboardHoldsASingle')} <code className="bg-gray-100 px-1 rounded">{t('docs.eventsource')}</code> {t('docs.onto')}{' '}
        <code className="bg-gray-100 px-1 rounded">{t('docs.apiStream')}</code> {t('docs.streamExplains')}
      </P>

      <H2>{t('docs.executionModes')}</H2>
      <P>
        <code className="bg-gray-100 px-1 rounded">AGENT_EXECUTION_MODE=local</code> {t('docs.runsAgentsAsHostSubprocesses')}{' '}
        <code className="bg-gray-100 px-1 rounded">{t('docs.docker')}</code> {t('docs.runsThemInManagedContainers')}{' '}
        <Link className="text-indigo-600 underline" to="/docs/containers">{t('docs.executionContainers')}</Link>.
      </P>
      <Callout><Rich>{t('docs.apiDoc.cliPointer')}</Rich></Callout>
    </div>
  );
}

function Faq() {
  const { t } = useI18n();
  // Each entry resolves to `docs.faq.<id>.q` / `.a`.
  const items = [
    'backendWontStart', 'frontendCantReach', 'runsFailImmediately', 'dockerCompose',
    'budgetExceeded', 'zeroCost', 'modelMissing', 'textNotChart', 'nothingStreams',
    'jobNeverFired', 'reopenOnboarding',
  ].map((id) => ({ q: t(`docs.faq.${id}.q`), a: t(`docs.faq.${id}.a`) }));
  return (
    <div>
      <H2>{t('docs.troubleshootingFaq')}</H2>
      <div className="space-y-2 my-3">
        {items.map((it, i) => (
          <details key={i} className="group border border-gray-200 rounded-xl bg-white">
            <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-gray-800 flex items-center justify-between">
              {it.q}
              <ArrowRight className="w-4 h-4 text-gray-300 group-open:rotate-90 transition-transform" />
            </summary>
            <div className="px-4 pb-3 text-sm text-gray-600">{it.a}</div>
          </details>
        ))}
      </div>
    </div>
  );
}

// ---- per-feature pages -----------------------------------------------------
// Each renders a short explainer plus a live, interactive widget bound to the
// real backend (and the active workspace where relevant).

function FeatureDoc({ title, lead, points, widget }) {
  const { t } = useI18n();
  return (
    <div>
      <H2>{title}</H2>
      <P>{lead}</P>
      {points && (
        <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
          {points.map((p, i) => <li key={i}>{p}</li>)}
        </ul>
      )}
      <Callout tone="tip">{t('docs.interactiveExampleBelowIllustrativeSample')}</Callout>
      <div className="my-4">{widget}</div>
    </div>
  );
}

function WorkspacesDoc() {
  const { t } = useI18n();
  return (
    <FeatureDoc
      title={t('docs.workspaces')}
      lead={t('docs.feature.workspaces.lead')}
      points={[
        t('docs.feature.workspaces.points0'),
        t('docs.feature.workspaces.points1'),
        t('docs.feature.workspaces.points2'),
      ]}
      widget={<WorkspaceModelExample />}
    />
  );
}

function ProjectsDoc() {
  const { t } = useI18n();
  return (
    <FeatureDoc
      title={t('docs.projects')}
      lead={t('docs.feature.projects.lead')}
      points={[
        t('docs.feature.projects.points0'),
        t('docs.feature.projects.points1'),
      ]}
      widget={<ProjectCardExample />}
    />
  );
}

function TasksDoc() {
  const { t } = useI18n();
  return (
    <FeatureDoc
      title={t('docs.tasks')}
      lead={t('docs.feature.tasks.lead')}
      points={[
        t('docs.feature.tasks.points0'),
        t('docs.feature.tasks.points1'),
        t('docs.feature.tasks.points2'),
      ]}
      widget={<TaskLifecycleExample />}
    />
  );
}

function AgentsDoc() {
  const { t } = useI18n();
  return (
    <FeatureDoc
      title={t('docs.agents')}
      lead={t('docs.feature.agents.lead')}
      points={[
        t('docs.feature.agents.points0'),
        t('docs.feature.agents.points1'),
        t('docs.feature.agents.points2'),
      ]}
      widget={<AgentDefinitionExample />}
    />
  );
}

function ImportingAgentsDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.importingAnAgentFromIts')}</H2>
      <P><Rich>{t('docs.importingDoc.lead')}</Rich></P>
      <Callout><Rich>{t('docs.importingDoc.callout')}</Rich></Callout>

      <H2>{t('docs.whatTheRepositoryMustProvide')}</H2>
      <P><Rich>{t('docs.importingDoc.contractLead')}</Rich></P>
      <CodeBlock label={t('docs.agentHubJsonRepositoryRoot')}>{`{
  "schema": "agents-hub/agent-manifest@1",
  "id": "aider",
  "name": "Aider",
  "description": "AI pair programmer that edits code in a local git repo.",
  "domain": "Software Engineering",
  "runtime": {
    "kind": "http",
    "port": 8410,
    "run_path": "/run",
    "stream_path": "/run/stream",
    "health_path": "/health",
    "timeout": 1800,
    "docker": { "dockerfile": "Dockerfile.agenthub" },
    "env": [
      { "name": "OPENAI_API_KEY", "required": true,
        "description": "Model key the agent uses for its own completions." }
    ]
  }
}`}</CodeBlock>
      <CodeBlock label={t('docs.theHttpContract')}>{`POST <run_path>                                                required
  {"prompt": "add retry handling to fetch_user", "run_id": "…", "workspace": "/work"}
  -> {"ok": true, "output": "Applied edits to api/users.py", "error": null}

GET  <health_path>   ->  any 2xx/3xx status                     recommended`}</CodeBlock>

      <H2>{t('docs.streaming')}</H2>
      <P><Rich>{t('docs.importingDoc.streamingLead')}</Rich></P>
      <CodeBlock label={t('docs.postStreamPathNdjsonOr')}>{`{"type": "thinking",   "message": "scanning the repo"}
{"type": "tool_start", "name": "aider", "input": "aider --message …"}
{"type": "token",      "token": "Applied edits to "}
{"type": "token",      "token": "api/users.py"}
{"type": "tool_end",   "name": "aider", "output": "edited 2 files"}
{"type": "usage",      "prompt_tokens": 4120, "completion_tokens": 380}
{"type": "done",       "ok": true, "output": "Applied edits to api/users.py"}`}</CodeBlock>
      <Callout tone="tip"><Rich>{t('docs.importingDoc.usageTip')}</Rich></Callout>
      <P><Rich>{t('docs.importingDoc.streamingWhen')}</Rich></P>

      <H2>{t('docs.workedExampleAider')}</H2>
      <P><Rich>{t('docs.importingDoc.aiderLead')}</Rich></P>
      <div className="my-4">
        <ImportedAgentExample />
      </div>

      <H3>{t('docs.theAdapterInFull')}</H3>
      <P><Rich>{t('docs.importingDoc.adapterLead')}</Rich></P>
      <CodeBlock label={t('docs.serverPyAbridged')}>{`@app.post("/run")
def run(req: RunRequest):
    workdir = _resolve_workdir(req.workspace)
    cmd = ["aider", "--message", req.prompt, "--yes-always", "--no-pretty", "--no-stream"]
    result = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True,
                            timeout=RUN_TIMEOUT)
    if result.returncode != 0:
        return {"ok": False, "output": result.stdout, "error": result.stderr}
    return {"ok": True, "output": result.stdout, "error": None,
            "changed_files": _changed_files(workdir)}


def _stream_aider(prompt, workdir):
    """Forward aider's stdout line by line, then one authoritative done frame."""
    proc = subprocess.Popen(cmd, cwd=str(workdir), env=_child_env(),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    collected = []
    for line in proc.stdout:
        collected.append(line)
        yield json.dumps({"type": "token", "token": line}) + "\\n"
    proc.wait(timeout=RUN_TIMEOUT)
    yield json.dumps({"type": "done", "ok": proc.returncode == 0,
                      "output": "".join(collected),
                      "changed_files": _changed_files(workdir)}) + "\\n"`}</CodeBlock>
      <Callout><Rich>{t('docs.importingDoc.unbufferedCallout')}</Rich></Callout>

      <H3>{t('docs.tryItEndToEnd')}</H3>
      <Walkthrough
        title={t('docs.fromRepositoryToARunning')}
        steps={[0, 1, 2, 3].map((i) => <Rich key={i}>{t(`docs.importingDoc.step${i}`)}</Rich>)}
      />
      <Callout tone="tip"><Rich>{t('docs.importingDoc.exampleTip')}</Rich></Callout>

      <H2>{t('docs.importingAnAgentThatIsnt')}</H2>
      <P>
        {t('docs.importNeverRefusesBefore')} <strong>{t('docs.needsSetup')}</strong>{t('docs.importNeverRefusesMiddle')}{' '}
        <strong>{t('docs.saveAndReCheck')}</strong> {t('docs.importNeverRefusesAfter')}
      </P>

      <H2>{t('docs.whatYouGiveUp')}</H2>
      <P>{t('docs.importingDoc.giveUpLead')}</P>
      <ul className="list-disc pl-5 space-y-1 text-sm text-gray-600 my-2">
        {[0, 1, 2].map((i) => <li key={i}><Rich>{t(`docs.importingDoc.giveUp${i}`)}</Rich></li>)}
      </ul>
    </div>
  );
}

function MarketplaceDoc() {
  const { t } = useI18n();
  return (
    <FeatureDoc
      title={t('docs.marketplace')}
      lead={t('docs.feature.marketplace.lead')}
      points={[
        t('docs.feature.marketplace.points0'),
        t('docs.feature.marketplace.points1'),
      ]}
      widget={<MarketplaceExample />}
    />
  );
}

function SkillsDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.skillsCatalog')}</H2>
      <P><Rich>{t('docs.skillsDoc.lead')}</Rich></P>
      <H3>{t('docs.ownershipWorksLikeTheMarketplace')}</H3>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li>{t('docs.aSkillBelongsToThe')}</li>
        <li><strong>{t('docs.publish')}</strong> {t('docs.putsItInTheGlobal')}</li>
        <li><Rich>{t('docs.skillsDoc.install')}</Rich></li>
      </ul>
      <H3>{t('docs.attachedVersusCatalogEntry')}</H3>
      <P>{t('docs.skillAttachExplains')}</P>
      <Callout tone="tip">
        <Rich>{t('docs.skillsDoc.runtime')}</Rich>
      </Callout>
    </div>
  );
}

function WebLogsDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.webRequests')}</H2>
      <P><Rich>{t('docs.webLogsDoc.lead')}</Rich></P>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><strong>{t('docs.didTheToolRead')}</strong> {t('docs.extractionIsLossy')}</li>
        <li><strong>{t('docs.wasTheResponseTrying')}</strong> {t('docs.eachCallIsScanned')}</li>
      </ul>
      <P>{t('docs.webLogsDoc.refusals')}</P>
      <Callout tone="warn"><Rich>{t('docs.webLogsDoc.callout')}</Rich></Callout>
    </div>
  );
}

function FlowsDoc() {
  const { t } = useI18n();
  return (
    <FeatureDoc
      title={t('docs.agentFlows')}
      lead={t('docs.feature.flows.lead')}
      points={[
        t('docs.feature.flows.points0'),
        t('docs.feature.flows.points1'),
        t('docs.feature.flows.points2'),
        t('docs.feature.flows.points3'),
      ]}
      widget={<FlowDiagramExample />}
    />
  );
}

function ViewsDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.viewsStudio')}</H2>
      <P><Rich>{t('docs.viewsDoc.lead')}</Rich></P>
      <H3>{t('docs.oneEnvelopeEverySurface')}</H3>
      <P><Rich>{t('docs.viewsDoc.envelope')}</Rich></P>
      <CodeBlock label={t('docs.theViewEnvelopeAbridged')}>{`{
  "view_id": "vw_9f3a…",
  "kind": "chart",                  // → which renderer draws it
  "title": "Revenue by quarter",
  "summary": "Q3 dips 12% on churn",
  "spec": { /* declarative, kind-specific: Vega-Lite, a scene, columns… */ },
  "data": { "inline": {…} },        // or {"file": …} / {"url": …} for live data
  "controls": [ /* sliders, toggles the viewer can move */ ],
  "complexity": "inline | expanded | fullscreen"
}`}</CodeBlock>
      <P><Rich>{t('docs.viewsDoc.dataSeparate')}</Rich></P>

      <Callout tone="tip"><Rich>{t('docs.viewsDoc.visualizerTip')}</Rich></Callout>

      <H3>{t('docs.studioEditingAViewBy')}</H3>
      <P><Rich>{t('docs.viewsDoc.studioLead')}</Rich></P>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><strong>{t('docs.theOutliner')}</strong> {t('docs.outlinerExplains')}</li>
        <li><strong>{t('docs.undoRevert')}</strong> {t('docs.walkTheOpLogBackwards')} <strong>{t('docs.checkpoints')}</strong> {t('docs.nameAStateWorthReturning')}</li>
        <li><strong>{t('docs.controls')}</strong> {t('docs.theAgentDeclaresBecomeReal')}</li>
        <li><strong>{t('docs.annotations')}</strong> {t('docs.annotationsExplain')}</li>
      </ul>

      <H3>{t('docs.computeWhenApproximationIsNot')}</H3>
      <P>
        {t('docs.simulationViewsRunInBrowser')}{' '}
        <code className="bg-gray-100 px-1 rounded">view_compute</code> {t('docs.viewComputeExplains')}
      </P>
      <Callout tone="warn">
        <code>view_serve</code> {t('docs.viewServeExplainsBefore')}{' '}
        <strong>{t('docs.localhost')}</strong> {t('docs.viewServeExplainsProxy')}{' '}
        <code>/api/views/{'{id}'}/proxy/*</code>. {t('docs.viewServeExplainsForward')} <em>{t('docs.launching')}</em>{' '}
        {t('docs.viewServeExplainsOptIn')}{' '}
        <code>VIEWS_SERVE_LAUNCH_ENABLED</code>{t('docs.viewServeExplainsDefault')}
      </Callout>
    </div>
  );
}

function PlanDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.planNotifications')}</H2>
      <P><Rich>{t('docs.planDoc.lead')}</Rich></P>
      <H3>{t('docs.threeKindsOfJob')}</H3>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><strong>{t('docs.notification')}</strong> {t('docs.remindTheUserAtA')}</li>
        <li><strong>{t('docs.agentTask')}</strong> {t('docs.startATaskWithA')}</li>
        <li><strong>{t('docs.flow')}</strong> {t('docs.triggerAFlowWithAn')}</li>
      </ul>
      <P><Rich>{t('docs.planDoc.recurrence')}</Rich></P>
      <H3>{t('docs.agentsScheduleToo')}</H3>
      <P><Rich>{t('docs.planDoc.agentTools')}</Rich></P>
      <Callout tone="tip"><Rich>{t('docs.planDoc.inboxTip')}</Rich></Callout>
    </div>
  );
}

function TeamsDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.teams')}</H2>
      <P><Rich>{t('docs.teamsDoc.lead')}</Rich></P>
      <H3>{t('docs.theBoard')}</H3>
      <P><Rich>{t('docs.teamsDoc.board')}</Rich></P>
      <H3>{t('docs.modes')}</H3>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><strong>{t('docs.centralized')}</strong> {t('docs.aLeadReadsTheBoard')}</li>
        <li><strong>{t('docs.autonomous')}</strong> {t('docs.everyMemberActsEachRound')}</li>
        <li><strong>{t('docs.parallel')}</strong> {t('docs.membersWorkTheRoundSide')}</li>
      </ul>
      <Callout tone="warn"><Rich>{t('docs.teamsDoc.callout')}</Rich></Callout>
    </div>
  );
}

function LoopsDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.loops')}</H2>
      <P><Rich>{t('docs.loopsDoc.lead')}</Rich></P>
      <H3>{t('docs.theExitCriterionIsProse')}</H3>
      <P>
        {t('docs.exitCriterionExplainsBefore')} (<code className="bg-gray-100 px-1 rounded">final_agent</code>);{' '}
        {t('docs.exitCriterionExplainsMiddle')}
        <em> {t('docs.and')}</em> {t('docs.exitCriterionExplainsAfter')}
      </P>
      <H3>{t('docs.boundsBecauseALoopCan')}</H3>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li>{t('docs.minimumAndMaximumIterationsAnd')}</li>
        <li>{t('docs.aNoImprovementPatienceCounter')}</li>
        <li>{t('docs.aCostCeilingAndA')}</li>
      </ul>
      <P><Rich>{t('docs.loopsDoc.records')}</Rich></P>
    </div>
  );
}

function EvalsDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.evals')}</H2>
      <P><Rich>{t('docs.evalsDoc.lead')}</Rich></P>
      <H3>{t('docs.graders')}</H3>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><code className="bg-gray-100 px-1 rounded">exact</code>, <code className="bg-gray-100 px-1 rounded">substring</code>, <code className="bg-gray-100 px-1 rounded">regex</code> {t('docs.cheapAndDeterministic')}</li>
        <li><code className="bg-gray-100 px-1 rounded">json_valid</code>, <code className="bg-gray-100 px-1 rounded">json_schema</code> {t('docs.structuralChecksForToolShaped')}</li>
        <li><code className="bg-gray-100 px-1 rounded">assertions</code> {t('docs.aListOfContainsNot')}</li>
        <li><code className="bg-gray-100 px-1 rounded">llm_judge</code> {t('docs.llmJudgeExplains')}</li>
      </ul>
      <P>{t('docs.gradersCarryWeightsSoOne')}</P>
      <Callout tone="warn"><Rich>{t('docs.evalsDoc.sweepCallout')}</Rich></Callout>
      <H3>{t('docs.whereCasesComeFrom')}</H3>
      <P><Rich>{t('docs.evalsDoc.cases')}</Rich></P>
      <H3>{t('docs.promptInjectionAudit')}</H3>
      <P>{t('docs.evalsDoc.injection')}</P>
      <CodeBlock label={t('docs.terminal')}>{`python -m evals.injection --static          # free: static audit of every agent
python -m evals.injection --agent job_scout # live sweep against one agent
python -m evals.injection --list            # show the corpus`}</CodeBlock>
    </div>
  );
}

function PlaygroundDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.agentPlayground')}</H2>
      <P><Rich>{t('docs.playgroundDoc.lead')}</Rich></P>
      <H3>{t('docs.scenarios')}</H3>
      <P><Rich>{t('docs.playgroundDoc.scenarios')}</Rich></P>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><strong>{t('docs.stockExchange')}</strong> {t('docs.agentsTradeAgainstEachOther')}</li>
        <li><strong>{t('docs.socialWorld')}</strong> {t('docs.agentsTalkFormOpinionsAnd')}</li>
      </ul>
      <H3>{t('docs.yourOwnWorlds')}</H3>
      <P><Rich>{t('docs.playgroundDoc.worlds')}</Rich></P>
      <H3>{t('docs.activationTitle')}</H3>
      <P><Rich>{t('docs.playgroundDoc.activation')}</Rich></P>
      <H3>{t('docs.theTickLogIsThe')}</H3>
      <P><Rich>{t('docs.playgroundDoc.tickLog')}</Rich></P>
    </div>
  );
}

function ModelsDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.models')}</H2>
      <P><Rich>{t('docs.modelsDoc.lead')}</Rich></P>
      <H3>{t('docs.catalog2')}</H3>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><strong>{t('docs.discover')}</strong> {t('docs.discoverExplains')}</li>
        <li><strong>{t('docs.enableDisable')}</strong> {t('docs.curatesThatRawListDown')}</li>
        <li><strong>{t('docs.pricing')}</strong> {t('docs.pricingExplains')}</li>
        <li><strong>{t('docs.defaultPerProvider')}</strong> {t('docs.persistsToTheSame')} <code className="bg-gray-100 px-1 rounded">.env</code> {t('docs.keysTheRuntimeReads')}</li>
      </ul>
      <P><Rich>{t('docs.modelsDoc.cached')}</Rich></P>
      <P><Rich>{t('docs.modelsDoc.contextWindow')}</Rich></P>
      <P>{t('docs.modelsDoc.custom')}</P>
      <H3>{t('docs.usage2')}</H3>
      <P><Rich>{t('docs.modelsDoc.usage')}</Rich></P>
      <Callout tone="warn"><Rich>{t('docs.modelsDoc.callout')}</Rich></Callout>
    </div>
  );
}

function CostsDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.costsBudgets')}</H2>
      <P><Rich>{t('docs.costsDoc.lead')}</Rich></P>
      <H3>{t('docs.budgetsAreEnforcedNotJust')}</H3>
      <P><Rich>{t('docs.costsDoc.budgets')}</Rich></P>
      <Callout tone="tip"><Rich>{t('docs.costsDoc.evalTip')}</Rich></Callout>
    </div>
  );
}

function ContainersDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.executionModesContainers')}</H2>
      <P><Rich>{t('docs.containersDoc.lead')}</Rich></P>
      <H3>{t('docs.theContainersPage')}</H3>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li>{t('docs.buildTheSharedBaseImage')}</li>
        <li>{t('docs.previewTheGeneratedDockerfileFor')}</li>
        <li>{t('docs.listRunningAndStoppedContainers')}</li>
      </ul>
      <Callout tone="warn"><Rich>{t('docs.containersDoc.callout')}</Rich></Callout>
    </div>
  );
}

function ConnectorsDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.connectors')}</H2>
      <P><Rich>{t('docs.connectorsDoc.lead')}</Rich></P>
      <H3><Send className="inline w-4 h-4 text-sky-500 mr-1" /> {t('docs.telegram')}</H3>
      <P><Rich>{t('docs.connectorsDoc.telegram')}</Rich></P>
      <H3><GitBranch className="inline w-4 h-4 text-orange-500 mr-1" /> {t('docs.git')}</H3>
      <P><Rich>{t('docs.connectorsDoc.git')}</Rich></P>
      <H3>{t('docs.otherThingsConfiguredInSettings')}</H3>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><strong>{t('docs.apiKeys')}</strong> {t('docs.openaiAnthropicGoogleCredentialsAnd')}</li>
        <li><strong>{t('docs.customBackends')}</strong> {t('docs.anyOpenaiCompatibleEndpointWith')}</li>
        <li><strong>{t('docs.localModels')}</strong> {t('docs.ollamaAndLmStudioEndpoints')}</li>
        <li><strong>{t('docs.observability')}</strong> {t('docs.langfuseTracing')}</li>
        <li><strong>{t('docs.ragVectors')}</strong> {t('docs.ragVectorsExplains')}</li>
        <li><strong>{t('docs.system')}</strong> {t('docs.liveStreamingAgentExecutionMode')}</li>
      </ul>
    </div>
  );
}

function ToolboxDoc() {
  const { t } = useI18n();
  return (
    <FeatureDoc
      title={t('docs.toolbox')}
      lead={t('docs.feature.toolbox.lead')}
      points={[
        t('docs.feature.toolbox.points0'),
        t('docs.feature.toolbox.points1'),
        t('docs.feature.toolbox.points2'),
        t('docs.feature.toolbox.points3'),
      ]}
      widget={<ToolExample />}
    />
  );
}

function MemoryDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.sharedMemory')}</H2>
      <P><Rich>{t('docs.memoryDoc.lead')}</Rich></P>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li>{t('docs.agentsReadWriteSharedMemory')} <code className="bg-gray-100 px-1 rounded">recall</code> / <code className="bg-gray-100 px-1 rounded">remember</code> / <code className="bg-gray-100 px-1 rounded">forget</code> {t('docs.tools')}</li>
        <li>{t('docs.memoryDoc.episodic')}</li>
        <li>{t('docs.onlyNamesAndStatsAre')}</li>
      </ul>
      <Callout tone="tip">{t('docs.interactiveExampleBelowIllustrativeSample')}</Callout>
      <div className="my-4"><MemoryExample /></div>
    </div>
  );
}

function InstancesDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.instancesDoc.title')}</H2>
      <P>{t('docs.instancesDoc.lead')}</P>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li>{t('docs.instancesDoc.points0')}</li>
        <li>{t('docs.instancesDoc.points1')}</li>
        <li>{t('docs.instancesDoc.points2')}</li>
        <li>{t('docs.instancesDoc.points3')}</li>
      </ul>
      <P>
        <Link className="text-indigo-600 underline" to="/instances">{t('docs.instancesDoc.openPage')}</Link>
        {' — '}{t('docs.instancesDoc.openHint')}
      </P>
      <Callout tone="tip">{t('docs.instancesDoc.tip')}</Callout>
    </div>
  );
}

function NodesDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.nodesDoc.title')}</H2>
      <P><Rich>{t('docs.nodesDoc.lead')}</Rich></P>
      <P><Rich>{t('docs.nodesDoc.sessions')}</Rich></P>
      <Callout tone="tip">{t('docs.interactiveExampleBelowIllustrativeSample')}</Callout>
      <div className="my-4">
        <NodeSessionExample />
      </div>
    </div>
  );
}

function OrchestratorDoc() {
  const { t } = useI18n();
  return (
    <FeatureDoc
      title={t('docs.orchestrator')}
      lead={t('docs.feature.orchestrator.lead')}
      points={[
        t('docs.feature.orchestrator.points0'),
        t('docs.feature.orchestrator.points1'),
      ]}
      widget={<OrchestratorExample />}
    />
  );
}

function ProvidersDoc() {
  const { t } = useI18n();
  return (
    <FeatureDoc
      title={t('docs.providers')}
      lead={t('docs.feature.providers.lead')}
      points={[
        t('docs.feature.providers.points0'),
        t('docs.feature.providers.points1'),
        t('docs.feature.providers.points2'),
      ]}
      widget={<ProviderExample />}
    />
  );
}

function Installation() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.nav.installation')}</H2>
      <P><Rich>{t('docs.installDoc.lead')}</Rich></P>

      <H3>{t('docs.installDoc.prereqTitle')}</H3>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><Rich>{t('docs.installDoc.prereqPython')}</Rich></li>
        <li><Rich>{t('docs.installDoc.prereqNode')}</Rich></li>
        <li><Rich>{t('docs.installDoc.prereqDocker')}</Rich></li>
        <li><Rich>{t('docs.installDoc.prereqKey')}</Rich></li>
      </ul>

      <H3>{t('docs.installDoc.installerTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`git clone <repo-url> agents_hub
cd agents_hub
./install.sh --frontend`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.installerBody')}</Rich></P>
      <P><Rich>{t('docs.installDoc.installerFlags')}</Rich></P>
      <CodeBlock label={t('docs.terminal')}>{`ah up`}</CodeBlock>

      <H3>{t('docs.installDoc.byHandTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`python -m venv .venv && source .venv/bin/activate
pip install -e ".[backend,agents]"
cp .env.example .env
cd dashboard/frontend && npm install && cd ../..`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.byHandBody')}</Rich></P>
      <CodeBlock label={t('docs.terminal')}>{`python -m uvicorn dashboard.backend.main:app --host 0.0.0.0 --port 8000 --reload
cd dashboard/frontend && npm run dev -- --host 0.0.0.0 --port 5173`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.byHandNoInstall')}</Rich></P>

      <H3>{t('docs.installDoc.dockerTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`docker compose up --build`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.dockerBody')}</Rich></P>
      <CodeBlock label={t('docs.terminal')}>{`docker compose up --build --scale backend=3`}</CodeBlock>

      <H3>{t('docs.installDoc.configTitle')}</H3>
      <CodeBlock label=".env">{`DEFAULT_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5
AGENT_EXECUTION_MODE=local`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.configBody')}</Rich></P>

      <H3>{t('docs.installDoc.verifyTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`curl http://localhost:8000/api/health
ah config
ah agent list`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.verifyBody')}</Rich></P>

      <H3>{t('docs.installDoc.stateTitle')}</H3>
      <P><Rich>{t('docs.installDoc.stateBody')}</Rich></P>

      <H3>{t('docs.installDoc.upgradeTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`git pull
./install.sh`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.upgradeBody')}</Rich></P>

      <Callout tone="warn"><Rich>{t('docs.installDoc.troubleCallout')}</Rich></Callout>
    </div>
  );
}

function CliGuide() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.nav.cli')}</H2>
      <P><Rich>{t('docs.cliGuide.lead')}</Rich></P>

      <H3>{t('docs.cliGuide.reachTitle')}</H3>
      <P><Rich>{t('docs.cliGuide.reachDirect')}</Rich></P>
      <P><Rich>{t('docs.cliGuide.reachHttp')}</Rich></P>

      <H3>{t('docs.cliGuide.getTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`./install.sh          # …or, with nothing installed:
python -m cli --help`}</CodeBlock>
      <P><Rich>{t('docs.cliGuide.getBody')}</Rich></P>

      <H3>{t('docs.cliGuide.commandsTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`ah agent list
ah agent run swe_agent "fix the failing test"
ah chat main-agent
ah task create "Build a REST API" --decompose
ah task list --status todo
ah workspace list
ah node list
ah config`}</CodeBlock>
      <P><Rich>{t('docs.cliGuide.commandsBody')}</Rich></P>

      <H3>{t('docs.cliGuide.upTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`ah up                 # API on :8000, built dashboard on :5173
ah up --dev           # backend reload + Vite dev server with HMR
ah up --rebuild       # force a fresh bundle
ah up --no-frontend   # just the API`}</CodeBlock>
      <P><Rich>{t('docs.cliGuide.upBody')}</Rich></P>

      <H3>{t('docs.cliGuide.dirsTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`cd ~/code/myapp
ah workspace init

ah workspace create dev --use
cd ~/code/myapp   && ah project add
cd ~/code/backend && ah project add
ah project get`}</CodeBlock>
      <P><Rich>{t('docs.cliGuide.dirsBody')}</Rich></P>

      <H3>{t('docs.cliGuide.selectTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`ah workspace use dev
ah workspace current
ah task create "no -w needed"
ah workspace unuse`}</CodeBlock>
      <P><Rich>{t('docs.cliGuide.selectBody')}</Rich></P>
      <P><Rich>{t('docs.cliGuide.selectCwd')}</Rich></P>

      <H3>{t('docs.cliGuide.shellTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`ah shell-init --install
exec zsh`}</CodeBlock>
      <P><Rich>{t('docs.cliGuide.shellBody')}</Rich></P>

      <H3>{t('docs.cliGuide.remoteTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`export AGENTS_HUB_URL=http://localhost:8000
ah agent list
ah workspace init /host/code/myapp`}</CodeBlock>
      <P><Rich>{t('docs.cliGuide.remoteBody')}</Rich></P>

      <Callout tone="tip"><Rich>{t('docs.cliGuide.tip')}</Rich></Callout>
    </div>
  );
}

// ---- section registry + shell ----------------------------------------------

// Section registry. Labels are translation keys (`docs.nav.*`) resolved at
// render time — a plain string here would keep its language when the switcher
// changes, which is exactly what used to happen.
const GROUPS = [
  {
    key: 'startHere',
    items: [
      { id: 'getting-started', key: 'gettingStarted', icon: Rocket, render: GettingStarted },
      { id: 'installation', key: 'installation', icon: HardDriveDownload, render: Installation },
      { id: 'concepts', key: 'concepts', icon: Boxes, render: Concepts },
      { id: 'features', key: 'overview', icon: Sparkles, render: Features },
    ],
  },
  {
    key: 'workspace',
    items: [
      { id: 'chat', key: 'chat', icon: MessageCircle, render: TryIt },
      { id: 'workspaces', key: 'workspaces', icon: Folder, render: WorkspacesDoc },
      { id: 'projects', key: 'projects', icon: FolderGit2, render: ProjectsDoc },
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
    ],
  },
  {
    key: 'automation',
    items: [
      { id: 'flows', key: 'flows', icon: Factory, render: FlowsDoc },
      { id: 'loops', key: 'loops', icon: Repeat, render: LoopsDoc },
      { id: 'tools', key: 'tools', icon: Wrench, render: ToolboxDoc },
      { id: 'memory', key: 'memory', icon: Database, render: MemoryDoc },
      { id: 'web-logs', key: 'webLogs', icon: Globe, render: WebLogsDoc },
      { id: 'evals', key: 'evals', icon: FlaskConical, render: EvalsDoc },
      { id: 'playground', key: 'playground', icon: Gamepad2, render: PlaygroundDoc },
    ],
  },
  {
    key: 'operations',
    items: [
      { id: 'instances', key: 'instances', icon: Radio, render: InstancesDoc },
      { id: 'nodes', key: 'nodes', icon: Server, render: NodesDoc },
      { id: 'containers', key: 'containers', icon: Box, render: ContainersDoc },
      { id: 'models', key: 'models', icon: Brain, render: ModelsDoc },
      { id: 'costs', key: 'costs', icon: DollarSign, render: CostsDoc },
      { id: 'providers', key: 'providers', icon: SettingsIcon, render: ProvidersDoc },
      { id: 'connectors', key: 'connectors', icon: Send, render: ConnectorsDoc },
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

const SECTIONS = GROUPS.flatMap((g) => g.items);

export default function Docs() {
  const { t } = useI18n();
  const { section } = useParams();
  const navigate = useNavigate();
  const active = SECTIONS.find((s) => s.id === section) || SECTIONS[0];
  const Content = active.render;

  return (
    <PageContainer fill>
      {/* Every other page opens with the same heading row; this one used to
          start straight on its section nav, so the reader had no title telling
          them where they were. */}
      <PageHeader
        icon={BookOpen}
        title={t('docs.documentation')}
        description={t('docs.pageDescription')}
      />

      <div className="flex min-h-0 flex-1 gap-6">
      {/* In-page section nav */}
      <nav className="w-56 shrink-0 hidden md:block overflow-y-auto">
        <div className="pb-8">
          {GROUPS.map((group) => (
            <div key={group.key} className="mb-2">
              <p className="px-3 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-widest text-gray-400">
                {t(`docs.nav.groups.${group.key}`)}
              </p>
              {group.items.map((s) => {
                const Icon = s.icon;
                const isActive = s.id === active.id;
                return (
                  <Link
                    key={s.id}
                    to={`/docs/${s.id}`}
                    className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
                      isActive
                        ? 'bg-indigo-50 text-indigo-600 font-semibold'
                        : 'text-gray-600 hover:bg-gray-100'
                    }`}
                  >
                    <Icon className="w-4 h-4 shrink-0" />
                    {t(`docs.nav.${s.key}`)}
                  </Link>
                );
              })}
            </div>
          ))}
        </div>
      </nav>

      {/* Content */}
      <div className="flex-1 min-w-0 overflow-y-auto">
        <div className="max-w-3xl pb-16">
          {/* Mobile section selector */}
          <div className="md:hidden mb-4">
            <select
              value={active.id}
              onChange={(e) => navigate(`/docs/${e.target.value}`)}
              className="w-full bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-sm"
            >
              {GROUPS.map((g) => (
                <optgroup key={g.key} label={t(`docs.nav.groups.${g.key}`)}>
                  {g.items.map((s) => (
                    <option key={s.id} value={s.id}>{t(`docs.nav.${s.key}`)}</option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>
          <Content />
        </div>
      </div>
      </div>
    </PageContainer>
  );
}
