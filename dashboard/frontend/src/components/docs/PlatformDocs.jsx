/**
 * Docs sections for planning, teams, evals, models, memory and platform services.
 */
import { GitBranch, Send } from 'lucide-react';
import { Link } from 'react-router-dom';
import { CorpusRefs } from './CorpusDocs';
import { Callout, CodeBlock, H2, H3, P, Rich } from './DocsPrimitives';
import { MemoryExample, OrchestratorExample, ProviderExample, ToolExample } from './ExampleWidgets';
import { FeatureDoc } from './FeatureDocs';
import { useI18n } from '../../i18n';

export function PlanDoc() {
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

export function TeamsDoc() {
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

export function LoopsDoc() {
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

export function EvalsDoc() {
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

export function PlaygroundDoc() {
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

export function ModelsDoc() {
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
      <CorpusRefs ids={['models']} />
    </div>
  );
}

export function CostsDoc() {
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

export function ContainersDoc() {
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

export function ConnectorsDoc() {
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

export function ToolboxDoc() {
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

export function HooksDoc() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.nav.hooks')}</H2>
      <P><Rich>{t('docs.hooksDoc.lead')}</Rich></P>

      <H3>{t('docs.hooksDoc.configTitle')}</H3>
      <P><Rich>{t('docs.hooksDoc.configBody')}</Rich></P>
      <CodeBlock label=".hooks.json">{`{
  "PreToolUse": [
    {"matcher": "run_shell|delete_file", "type": "command",
     "command": "./scripts/check_tool_call.py", "timeout": 10},
    {"matcher": "apply_unified_diff", "type": "http",
     "url": "http://localhost:9000/review", "fail_closed": true}
  ],
  "PostToolUse": [
    {"matcher": ".*", "type": "command", "command": "./scripts/audit.sh"}
  ]
}`}</CodeBlock>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><Rich>{t('docs.hooksDoc.matcher')}</Rich></li>
        <li><Rich>{t('docs.hooksDoc.type')}</Rich></li>
        <li><Rich>{t('docs.hooksDoc.exempt')}</Rich></li>
      </ul>

      <H3>{t('docs.hooksDoc.payloadTitle')}</H3>
      <P><Rich>{t('docs.hooksDoc.payloadBody')}</Rich></P>

      <H3>{t('docs.hooksDoc.exitTitle')}</H3>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><Rich>{t('docs.hooksDoc.exit0')}</Rich></li>
        <li><Rich>{t('docs.hooksDoc.exit2')}</Rich></li>
        <li><Rich>{t('docs.hooksDoc.exitOther')}</Rich></li>
      </ul>
      <P><Rich>{t('docs.hooksDoc.httpBody')}</Rich></P>
      <Callout tone="warn"><Rich>{t('docs.hooksDoc.securityCallout')}</Rich></Callout>

      <H3>{t('docs.hooksDoc.gateTitle')}</H3>
      <P><Rich>{t('docs.hooksDoc.gateBody')}</Rich></P>
      <CodeBlock label={t('docs.hooksDoc.gateSettingsLabel')}>{`{"settings": {"require_tool_approval": true}}`}</CodeBlock>
      <P><Rich>{t('docs.hooksDoc.gateTools')}</Rich></P>
      <P><Rich>{t('docs.hooksDoc.gateTask')}</Rich></P>
      <Callout tone="tip"><Rich>{t('docs.hooksDoc.advisoryTip')}</Rich></Callout>
    </div>
  );
}

export function MemoryDoc() {
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

export function InstancesDoc() {
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

export function OrchestratorDoc() {
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

export function ProvidersDoc() {
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
