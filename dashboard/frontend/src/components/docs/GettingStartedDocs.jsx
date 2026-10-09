/**
 * Start-here sections of Docs: getting started, concepts, features, try it, tutorials, CLI and API.
 */
import {
  Brain, CalendarClock, CheckSquare, Compass, Database, DollarSign, Download, Factory,
  FlaskConical, Folder, FolderGit2, Gamepad2, GraduationCap, Images, MessageCircle, Network,
  Radio, Repeat, Send, Store, Users, UsersRound,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { CorpusRefs } from './CorpusDocs';
import { Callout, CodeBlock, FeatureCard, H2, H3, P, Rich, Walkthrough } from './DocsPrimitives';
import { SyntheticChat } from './ExampleWidgets';
import OnboardingChecklist from './OnboardingChecklist';
import { useWelcomeTour } from './WelcomeTour';
import { useI18n } from '../../i18n';

// Next to the checklist rather than inside the FAQ answer that mentions it:
// the answer says where the tour lives, this is the thing itself.
function ReplayTour() {
  const { t } = useI18n();
  const tour = useWelcomeTour();
  return (
    <div className="my-4 flex flex-wrap items-center gap-3 rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3">
      <button
        type="button"
        onClick={() => tour.start()}
        className="flex items-center gap-1.5 shrink-0 text-sm font-semibold px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 transition-colors"
      >
        <Compass className="w-4 h-4" />
        {t('docs.start.replayTour')}
      </button>
      <span className="text-sm text-gray-600">{t('docs.start.replayTourHint')}</span>
    </div>
  );
}

export function GettingStarted() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.nav.gettingStarted')}</H2>
      <P><Rich>{t('docs.start.lead')}</Rich></P>
      <div className="my-4">
        <OnboardingChecklist />
      </div>
      <ReplayTour />

      <Walkthrough
        title={t('docs.start.firstRun')}
        steps={[0, 1, 2, 3].map((i) => <Rich key={i}>{t(`docs.start.firstRun${i}`)}</Rich>)}
      />

      <H2>{t('docs.installRun')}</H2>
      <H3>{t('docs.dockerForOne')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`mkdir agents-hub && cd agents-hub
curl -fsSLO https://raw.githubusercontent.com/snooker155/agents_hub/main/deploy/quickstart/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/snooker155/agents_hub/main/deploy/quickstart/env.example -o .env
docker compose up -d`}</CodeBlock>
      <P><Rich>{t('docs.dockerForOneBody')}</Rich></P>
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
        <FeatureCard icon={Images} title={t('docs.viewsStudio')} to="/artifacts">
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

export function Concepts() {
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

export function Features() {
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
        <FeatureCard icon={Network} title={t('docs.orchestrator')} to="/orchestrator">
          {t('docs.configureRoutingSoTasksAre')}
        </FeatureCard>
        <FeatureCard icon={Images} title={t('docs.viewsStudio')} to="/artifacts">
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
      <CorpusRefs ids={['overview']} />
    </div>
  );
}

export function TryIt() {
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
      <CorpusRefs ids={['chat']} />
    </div>
  );
}

export function Tutorials() {
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
          <>{t('docs.openItFromThe')} <Link className="text-indigo-600 underline" to="/artifacts">{t('docs.views')}</Link> {t('docs.galleryOrPress')} <strong>{t('docs.studio')}</strong> {t('docs.toKeepEditingItBy')}</>,
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

export function CliApi() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.apiSurface')}</H2>
      <P>{t('docs.theBackendIsOrganisedBy')} <code className="bg-gray-100 px-1 rounded">{t('docs.api')}</code>:</P>
      <div className="flex flex-wrap gap-1.5 my-3">
        {['agents', 'agent-import', 'marketplace', 'tasks', 'plan', 'flows', 'flow-entities', 'loops',
          'teams', 'stats', 'models', 'costs', 'evals', 'playground', 'shared-memory', 'skills',
          'web-logs', 'workspaces', 'tools', 'sessions', 'messages', 'chat', 'instances', 'external',
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
