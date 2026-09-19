import { useState } from 'react';
import { useI18n } from '../../i18n';
import {
  User,
  Bot,
  Wrench,
  Cpu,
  ArrowRight,
  ArrowDown,
  CheckCircle2,
  Circle,
  RotateCcw,
  FileText,
  Hash,
  CalendarDays,
  Activity,
  Network,
  Database,
  GitBranch,
  Folder,
  FolderGit2,
  CheckSquare,
  AlertTriangle,
  Info,
  X,
  Server,
  FileCode,
} from 'lucide-react';

// ===========================================================================
// Synthetic example widgets for the Docs feature pages.
//
// IMPORTANT: none of these call the backend or render the user's real data.
// Every value here is illustrative/made-up, designed to teach how a feature
// works. Several are lightly interactive (tabs, steppers, clickable nodes) so
// the concept is tangible without touching a live instance.
// ===========================================================================

function DemoFrame({ label, children }) {
  const { t } = useI18n();
  return (
    <div className="rounded-2xl border border-gray-200 overflow-hidden bg-white">
      <div className="px-3 py-1.5 bg-gray-50 border-b border-gray-200 flex items-center gap-1.5">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-400">{t('exampleWidgets.example')}</span>
        {label && <span className="text-[11px] text-gray-400">· {label}</span>}
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

function Badge({ children, color = 'gray' }) {
  const map = {
    gray: 'bg-gray-100 text-gray-600',
    green: 'bg-green-100 text-green-700',
    blue: 'bg-blue-100 text-blue-700',
    amber: 'bg-amber-100 text-amber-700',
    violet: 'bg-violet-100 text-violet-700',
    indigo: 'bg-indigo-100 text-indigo-700',
  };
  return <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${map[color]}`}>{children}</span>;
}

// ---------------------------------------------------------------------------
// Chat — scripted, clickable demo conversation (no real agent)
// ---------------------------------------------------------------------------
const chatDemo = (t) => ['summarise', 'createTask', 'remember'].map((id) => ({
  id,
  q: t(`exampleWidgets.chatDemo.${id}.q`),
  tool: t(`exampleWidgets.chatDemo.${id}.tool`),
  a: t(`exampleWidgets.chatDemo.${id}.a`),
}));

export function SyntheticChat() {
  const { t } = useI18n();
  const CHAT_DEMO = chatDemo(t);
  const [turns, setTurns] = useState([]);
  const ask = (item) => setTurns((t) => [...t, item]);
  const used = new Set(turns.map((t) => t.q));

  return (
    <DemoFrame label={t('exampleWidgets.aScriptedConversation')}>
      <div className="space-y-3 mb-3 min-h-[3rem]">
        {turns.length === 0 && (
          <p className="text-sm text-gray-400 text-center py-3">{t('exampleWidgets.clickAPromptBelowTo')}</p>
        )}
        {turns.map((t, i) => (
          <div key={i} className="space-y-2">
            <div className="flex justify-end">
              <div className="flex items-start gap-2 max-w-[85%]">
                <div className="bg-indigo-600 text-white rounded-2xl px-3 py-2 text-sm">{t.q}</div>
                <User className="w-5 h-5 text-indigo-400 mt-1 shrink-0" />
              </div>
            </div>
            <div className="flex justify-start">
              <div className="flex items-start gap-2 max-w-[85%]">
                <Bot className="w-5 h-5 text-gray-400 mt-1 shrink-0" />
                <div className="space-y-1.5">
                  <div className="flex items-center gap-1.5 text-[11px] text-violet-600 font-medium">
                    <Wrench className="w-3 h-3" /> {t('exampleWidgets.calledTool')} <code className="bg-violet-50 px-1 rounded">{t.tool}</code>
                  </div>
                  <div className="bg-gray-100 text-gray-800 rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap">{t.a}</div>
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-2 border-t border-gray-100 pt-3">
        {CHAT_DEMO.map((item) => (
          <button
            key={item.q}
            onClick={() => ask(item)}
            disabled={used.has(item.q)}
            className="text-xs px-2.5 py-1 rounded-full border border-gray-200 text-gray-600 hover:border-indigo-300 hover:text-indigo-600 disabled:opacity-40"
          >
            {item.q}
          </button>
        ))}
        {turns.length > 0 && (
          <button onClick={() => setTurns([])} className="text-xs px-2 py-1 rounded-full text-gray-400 hover:text-gray-600 flex items-center gap-1">
            <RotateCcw className="w-3 h-3" /> {t('exampleWidgets.reset')}
          </button>
        )}
      </div>
    </DemoFrame>
  );
}

// ---------------------------------------------------------------------------
// Workspaces — nesting diagram (synthetic)
// ---------------------------------------------------------------------------
export function WorkspaceModelExample() {
  const { t } = useI18n();
  return (
    <DemoFrame label={t('exampleWidgets.howWorkspacesNest')}>
      <div className="rounded-xl border border-blue-200 bg-blue-50/40 p-3">
        <div className="flex items-center gap-1.5 text-sm font-semibold text-blue-700">
          <Folder className="w-4 h-4" /> {t('exampleWidgets.acmeWebsite')} <Badge color="blue">{t('exampleWidgets.workspace')}</Badge>
        </div>
        <div className="ml-5 mt-2 space-y-2">
          <div className="rounded-lg border border-gray-200 bg-white p-2.5">
            <div className="flex items-center gap-1.5 text-sm font-medium text-gray-800">
              <FolderGit2 className="w-4 h-4 text-gray-500" /> {t('exampleWidgets.checkoutRedesign')} <Badge>{t('exampleWidgets.project')}</Badge>
            </div>
            <div className="ml-5 mt-1.5 space-y-1 text-sm text-gray-600">
              <div className="flex items-center gap-1.5"><CheckSquare className="w-3.5 h-3.5 text-emerald-500" /> {t('exampleWidgets.addApplePay')} <Badge color="green">{t('exampleWidgets.done')}</Badge></div>
              <div className="flex items-center gap-1.5"><CheckSquare className="w-3.5 h-3.5 text-blue-500" /> {t('exampleWidgets.redesignCart')} <Badge color="blue">{t('exampleWidgets.running')}</Badge></div>
            </div>
          </div>
          <p className="text-[11px] text-gray-500">{t('exampleWidgets.plusThisWorkspaceSOwn')}</p>
        </div>
      </div>
      <p className="text-xs text-gray-500 mt-3">
        Create one workspace per client, product or experiment. The <code className="bg-gray-100 px-1 rounded">{t('exampleWidgets.default')}</code> workspace acts as an “all” view.
      </p>
    </DemoFrame>
  );
}

// ---------------------------------------------------------------------------
// Projects — synthetic project card
// ---------------------------------------------------------------------------
export function ProjectCardExample() {
  const { t } = useI18n();
  return (
    <DemoFrame label={t('exampleWidgets.aProjectAtAGlance')}>
      <div className="rounded-xl border border-gray-200 p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FolderGit2 className="w-5 h-5 text-indigo-500" />
            <span className="font-semibold text-gray-900">{t('exampleWidgets.checkoutRedesign')}</span>
          </div>
          <Badge color="blue">{t('exampleWidgets.active')}</Badge>
        </div>
        <p className="text-sm text-gray-500 mt-1">{t('exampleWidgets.reworkTheCheckoutFunnelAnd')}</p>
        <div className="grid grid-cols-2 gap-2 mt-3 text-xs">
          <div className="rounded-lg bg-gray-50 p-2"><span className="text-gray-400">{t('exampleWidgets.repo')}</span><br /><span className="font-mono text-gray-700">{t('exampleWidgets.githubComAcmeWeb')}</span></div>
          <div className="rounded-lg bg-gray-50 p-2"><span className="text-gray-400">{t('exampleWidgets.preview')}</span><br /><span className="font-mono text-gray-700">{t('exampleWidgets.localhost5173')}</span></div>
          <div className="rounded-lg bg-gray-50 p-2"><span className="text-gray-400">{t('exampleWidgets.stack')}</span><br /><span className="text-gray-700">{t('exampleWidgets.reactFastapi')}</span></div>
          <div className="rounded-lg bg-gray-50 p-2"><span className="text-gray-400">{t('exampleWidgets.tasks')}</span><br /><span className="text-gray-700">{t('exampleWidgets.k5Open12Done')}</span></div>
        </div>
      </div>
    </DemoFrame>
  );
}

// ---------------------------------------------------------------------------
// Tasks — interactive lifecycle stepper
// ---------------------------------------------------------------------------
const taskStages = (t) => ['pending', 'assigned', 'running', 'completed'].map((id) => ({
  id,
  name: t(`exampleWidgets.taskStages.${id}.name`),
  log: t(`exampleWidgets.taskStages.${id}.log`),
}));

export function TaskLifecycleExample() {
  const { t } = useI18n();
  const TASK_STAGES = taskStages(t);
  const [i, setI] = useState(0);
  return (
    <DemoFrame label={t('exampleWidgets.aTaskThroughItsLifecycle')}>
      <div className="flex items-center justify-between mb-3">
        <span className="font-semibold text-gray-900 text-sm">{t('exampleWidgets.addOauthLoginToThe')}</span>
        <span className="text-[11px] text-gray-400">{t('exampleWidgets.assignedSweAgent')}</span>
      </div>
      <div className="flex items-center gap-1 mb-3">
        {TASK_STAGES.map((s, idx) => (
          <div key={s.name} className="flex items-center flex-1">
            <div className={`flex items-center gap-1 ${idx <= i ? 'text-indigo-600' : 'text-gray-300'}`}>
              {idx < i ? <CheckCircle2 className="w-4 h-4" /> : <Circle className={`w-4 h-4 ${idx === i ? 'fill-indigo-100' : ''}`} />}
              <span className="text-[11px] font-medium">{s.name}</span>
            </div>
            {idx < TASK_STAGES.length - 1 && <div className={`h-px flex-1 mx-1 ${idx < i ? 'bg-indigo-300' : 'bg-gray-200'}`} />}
          </div>
        ))}
      </div>
      <pre className="text-[11px] text-gray-600 bg-gray-50 rounded-lg p-3 whitespace-pre-wrap min-h-[2.5rem]">{TASK_STAGES[i].log}</pre>
      <div className="flex gap-2 mt-3">
        <button onClick={() => setI((v) => Math.max(0, v - 1))} disabled={i === 0} className="text-xs px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 disabled:opacity-40">{t('exampleWidgets.back')}</button>
        <button onClick={() => setI((v) => Math.min(TASK_STAGES.length - 1, v + 1))} disabled={i === TASK_STAGES.length - 1} className="text-xs px-3 py-1.5 rounded-lg bg-indigo-600 text-white disabled:opacity-40">{t('exampleWidgets.advance')}</button>
      </div>
    </DemoFrame>
  );
}

// ---------------------------------------------------------------------------
// Agents — layered definition example with tabs + registry card
// ---------------------------------------------------------------------------
const AGENT_DEF = {
  instructions: `# Code Reviewer

You review diffs for correctness, security and clarity.
Be specific: cite file and line, explain the risk, suggest a fix.
Prefer fewer high-confidence findings over noisy nitpicks.`,
  capabilities: `## Capabilities

- Read repository files and diffs
- Run the test suite and read failures
- Cannot push commits or modify CI`,
  usage: `## Usage

Good fit: reviewing a pull request before merge.
Poor fit: writing large features from scratch (use the SWE agent).`,
};

export function AgentDefinitionExample() {
  const { t } = useI18n();
  const [tab, setTab] = useState('instructions');
  const tabs = ['instructions', 'capabilities', 'usage'];
  return (
    <DemoFrame label={t('exampleWidgets.aSyntheticCodeReviewerAgent')}>
      <div className="grid md:grid-cols-3 gap-3">
        <div className="md:col-span-2 border border-gray-200 rounded-xl overflow-hidden">
          <div className="flex border-b border-gray-100 bg-gray-50">
            {tabs.map((t) => (
              <button key={t} onClick={() => setTab(t)} className={`px-3 py-1.5 text-xs font-medium capitalize ${tab === t ? 'text-indigo-600 border-b-2 border-indigo-500' : 'text-gray-500'}`}>
                {t}.md
              </button>
            ))}
          </div>
          <pre className="text-[11px] leading-relaxed text-gray-700 p-3 whitespace-pre-wrap min-h-[9rem]">{AGENT_DEF[tab]}</pre>
        </div>
        <div className="border border-gray-200 rounded-xl p-3 space-y-2 text-xs">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">{t('exampleWidgets.registryEntry')}</p>
          <div className="flex items-center gap-1.5 text-gray-700"><Cpu className="w-3.5 h-3.5 text-violet-500" /> {t('exampleWidgets.claudeOpus48')}</div>
          <div className="flex items-center gap-1.5 text-gray-700"><Wrench className="w-3.5 h-3.5 text-indigo-500" /> {t('exampleWidgets.readFilesRunTests')}</div>
          <div className="flex items-center gap-1.5 text-gray-700"><Database className="w-3.5 h-3.5 text-emerald-500" /> {t('exampleWidgets.memoryWorkspacePool')}</div>
          <p className="text-[11px] text-gray-400 pt-1">{t('exampleWidgets.promptInstructionsCapabilitiesUsageConcatenated')}</p>
        </div>
      </div>
    </DemoFrame>
  );
}

// ---------------------------------------------------------------------------
// Imported agent — the readiness report, before and after setup
//
// Mirrors the real report shape produced by agents/importer/checks.py for the
// bundled Aider example, so the docs show the same verdict the import dialog
// will show. The values are illustrative; nothing here calls the backend.
// ---------------------------------------------------------------------------
// The readiness report mirrors agents/importer/checks.py for the bundled Aider
// example. Wording resolves through i18n; ids and technical details stay put.
const aiderRepoFiles = (t) => [
  { name: 'agent-hub.json', note: t('exampleWidgets.aiderFiles.manifest'), added: true },
  { name: 'server.py', note: t('exampleWidgets.aiderFiles.server'), added: true },
  { name: 'Dockerfile.agenthub', note: t('exampleWidgets.aiderFiles.dockerfile'), added: true },
  { name: 'requirements.txt', note: 'aider-chat, fastapi, uvicorn', added: true },
  { name: t('exampleWidgets.aiderFiles.restName'), note: t('exampleWidgets.aiderFiles.restNote'), added: false },
];

const aiderReport = (t) => ({
  before: {
    runnable: false,
    summary: t('exampleWidgets.aiderReport.beforeSummary'),
    checks: [
      { id: 'repo', label: t('exampleWidgets.aiderChecks.repo'), ok: true, required: true, detail: 'clone @ 4f21ac9' },
      { id: 'manifest', label: t('exampleWidgets.aiderChecks.manifest'), ok: true, required: true, detail: t('exampleWidgets.aiderChecks.manifestDetail') },
      { id: 'agent_id', label: t('exampleWidgets.aiderChecks.agentId'), ok: true, required: true, detail: 'aider' },
      { id: 'runtime', label: t('exampleWidgets.aiderChecks.runtime'), ok: true, required: true, detail: 'http — POST /run' },
      {
        id: 'endpoint', label: t('exampleWidgets.aiderChecks.endpoint'), ok: false, required: true,
        detail: t('exampleWidgets.aiderChecks.endpointMissing'),
        fix: t('exampleWidgets.aiderChecks.endpointFix'),
      },
      {
        id: 'env', label: t('exampleWidgets.aiderChecks.env'), ok: false, required: true,
        detail: t('exampleWidgets.aiderChecks.envMissing'),
        fix: t('exampleWidgets.aiderChecks.envFix'),
      },
      { id: 'packaging', label: t('exampleWidgets.aiderChecks.packaging'), ok: true, required: false, detail: t('exampleWidgets.aiderChecks.packagingDetail') },
      { id: 'streaming', label: t('exampleWidgets.aiderChecks.streaming'), ok: true, required: false, detail: t('exampleWidgets.aiderChecks.streamingDetail') },
      {
        id: 'health', label: t('exampleWidgets.aiderChecks.health'), ok: false, required: false,
        detail: t('exampleWidgets.aiderChecks.healthMissing'),
        fix: t('exampleWidgets.aiderChecks.healthFix'),
      },
    ],
  },
  after: {
    runnable: true,
    summary: t('exampleWidgets.aiderReport.afterSummary'),
    checks: [
      { id: 'repo', label: t('exampleWidgets.aiderChecks.repo'), ok: true, required: true, detail: 'clone @ 4f21ac9' },
      { id: 'manifest', label: t('exampleWidgets.aiderChecks.manifest'), ok: true, required: true, detail: t('exampleWidgets.aiderChecks.manifestDetail') },
      { id: 'agent_id', label: t('exampleWidgets.aiderChecks.agentId'), ok: true, required: true, detail: 'aider' },
      { id: 'runtime', label: t('exampleWidgets.aiderChecks.runtime'), ok: true, required: true, detail: 'http — POST /run' },
      { id: 'endpoint', label: t('exampleWidgets.aiderChecks.endpoint'), ok: true, required: true, detail: 'http://localhost:8410' },
      { id: 'env', label: t('exampleWidgets.aiderChecks.env'), ok: true, required: true, detail: t('exampleWidgets.aiderChecks.envSet') },
      { id: 'packaging', label: t('exampleWidgets.aiderChecks.packaging'), ok: true, required: false, detail: t('exampleWidgets.aiderChecks.packagingDetail') },
      { id: 'streaming', label: t('exampleWidgets.aiderChecks.streaming'), ok: true, required: false, detail: t('exampleWidgets.aiderChecks.streamingDetail') },
      { id: 'health', label: t('exampleWidgets.aiderChecks.health'), ok: true, required: false, detail: t('exampleWidgets.aiderChecks.healthOk') },
    ],
  },
});

function ReportCheckRow({ check }) {
  const Icon = check.ok ? CheckCircle2 : check.required ? X : Info;
  const tone = check.ok ? 'text-emerald-600' : check.required ? 'text-red-600' : 'text-amber-600';
  return (
    <li className="flex items-start gap-2 text-[11px] leading-relaxed">
      <Icon className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${tone}`} />
      <span className="min-w-0">
        <span className="font-semibold text-gray-800">{check.label}</span>
        {check.detail && <span className="text-gray-500"> — {check.detail}</span>}
        {!check.ok && check.fix && <span className="block text-gray-400 italic">{check.fix}</span>}
      </span>
    </li>
  );
}

export function ImportedAgentExample() {
  const { t } = useI18n();
  const [stage, setStage] = useState('before');
  const report = aiderReport(t)[stage];

  return (
    <DemoFrame label={t('exampleWidgets.importingAiderTheReadinessReport')}>
      <div className="grid md:grid-cols-2 gap-3">
        <div className="border border-gray-200 rounded-xl p-3">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-gray-400 mb-2">
            {t('exampleWidgets.whatTheRepositoryAdds')}
          </p>
          <ul className="space-y-1.5">
            {aiderRepoFiles(t).map((f) => (
              <li key={f.name} className="flex items-start gap-2 text-[11px]">
                <FileCode className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${f.added ? 'text-indigo-500' : 'text-gray-300'}`} />
                <span className="min-w-0">
                  <code className={f.added ? 'text-gray-800' : 'text-gray-400'}>{f.name}</code>
                  <span className="block text-gray-400">{f.note}</span>
                </span>
              </li>
            ))}
          </ul>
          <div className="mt-3 pt-3 border-t border-gray-100 flex items-center gap-1.5 text-[11px] text-gray-500">
            <Server className="w-3.5 h-3.5 text-gray-400" />
            {t('exampleWidgets.threeFilesAiderItselfIs')}
          </div>
        </div>

        <div className="border border-gray-200 rounded-xl overflow-hidden">
          <div className="flex border-b border-gray-100 bg-gray-50">
            {[
              ['before', t('exampleWidgets.aiderStages.before')],
              ['after', t('exampleWidgets.aiderStages.after')],
            ].map(([key, label]) => (
              <button
                key={key}
                onClick={() => setStage(key)}
                className={`px-3 py-1.5 text-[11px] font-medium ${
                  stage === key ? 'text-indigo-600 border-b-2 border-indigo-500' : 'text-gray-500'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="p-3">
            <div
              className={`flex items-start gap-2 rounded-lg px-2.5 py-2 mb-3 ${
                report.runnable ? 'bg-emerald-50 text-emerald-900' : 'bg-amber-50 text-amber-900'
              }`}
            >
              {report.runnable ? (
                <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
              ) : (
                <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              )}
              <div className="text-[11px]">
                <p className="font-bold">{report.summary}</p>
                {!report.runnable && (
                  <p className="opacity-80">{t('exampleWidgets.importedAnywayMarkedNeedsSetup')}</p>
                )}
              </div>
            </div>
            <ul className="space-y-1.5">
              {report.checks.map((c) => (
                <ReportCheckRow key={c.id} check={c} />
              ))}
            </ul>
          </div>
        </div>
      </div>
    </DemoFrame>
  );
}

// ---------------------------------------------------------------------------
// Marketplace — synthetic cloneable cards
// ---------------------------------------------------------------------------
const market = (t) => [
  { name: 'Researcher', color: 'blue' },
  { name: 'Code Reviewer', color: 'violet' },
  { name: 'PM Agent', color: 'amber' },
  { name: 'QA Agent', color: 'green' },
].map((m, i) => ({ ...m, desc: t(`exampleWidgets.market.${['researcher', 'reviewer', 'pm', 'qa'][i]}`) }));

export function MarketplaceExample() {
  const { t } = useI18n();
  const MARKET = market(t);
  const [cloned, setCloned] = useState({});
  return (
    <DemoFrame label={t('exampleWidgets.cloneableAgentRoles')}>
      <div className="grid sm:grid-cols-2 gap-2">
        {MARKET.map((a) => (
          <div key={a.name} className="p-3 rounded-xl border border-gray-200 flex items-start justify-between gap-2">
            <div>
              <div className="flex items-center gap-1.5"><Bot className="w-4 h-4 text-gray-400" /><span className="font-medium text-sm text-gray-900">{a.name}</span></div>
              <p className="text-[11px] text-gray-500 mt-0.5">{a.desc}</p>
            </div>
            <button
              onClick={() => setCloned((c) => ({ ...c, [a.name]: true }))}
              className={`text-[11px] px-2 py-1 rounded-lg shrink-0 ${cloned[a.name] ? 'bg-green-100 text-green-700' : 'bg-indigo-600 text-white hover:bg-indigo-700'}`}
            >
              {cloned[a.name] ? t('exampleWidgets.cloned') : t('exampleWidgets.clone')}
            </button>
          </div>
        ))}
      </div>
      <p className="text-[11px] text-gray-400 mt-2">{t('exampleWidgets.illustrativeClone')}</p>
    </DemoFrame>
  );
}

// ---------------------------------------------------------------------------
// Flows — clickable synthetic graph
// ---------------------------------------------------------------------------
const flowNodes = (t) => ['pm', 'swe', 'qa', 'rev'].map((id) => ({
  id,
  label: t(`exampleWidgets.flowNodes.${id}.label`),
  desc: t(`exampleWidgets.flowNodes.${id}.desc`),
}));

export function FlowDiagramExample() {
  const { t } = useI18n();
  const FLOW_NODES = flowNodes(t);
  const [sel, setSel] = useState('swe');
  const node = FLOW_NODES.find((n) => n.id === sel);
  return (
    <DemoFrame label={t('exampleWidgets.a4StageDeliveryFlow')}>
      <div className="flex items-center gap-1 overflow-x-auto pb-1">
        {FLOW_NODES.map((n, idx) => (
          <div key={n.id} className="flex items-center">
            <button
              onClick={() => setSel(n.id)}
              className={`px-3 py-2 rounded-xl border text-xs font-medium whitespace-nowrap ${sel === n.id ? 'border-indigo-400 bg-indigo-50 text-indigo-700' : 'border-gray-200 text-gray-600 hover:border-indigo-200'}`}
            >
              {n.label}
            </button>
            {idx < FLOW_NODES.length - 1 && <ArrowRight className="w-4 h-4 text-gray-300 mx-0.5 shrink-0" />}
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-start gap-2 text-sm text-gray-600">
        <GitBranch className="w-4 h-4 text-indigo-400 mt-0.5 shrink-0" />
        <span><strong className="text-gray-800">{node.label}:</strong> {node.desc}</span>
      </div>
    </DemoFrame>
  );
}

// ---------------------------------------------------------------------------
// Toolbox — synthetic tools + an example invocation
// ---------------------------------------------------------------------------
const TOOL_GROUPS = {
  filesystem: [
    { name: 'read_file', args: 'path' },
    { name: 'write_file', args: 'path, content' },
  ],
  shell: [{ name: 'run_command', args: 'cmd' }],
  planning: [{ name: 'create_plan', args: 'steps[]' }],
};

export function ToolExample() {
  const { t } = useI18n();
  return (
    <DemoFrame label={t('exampleWidgets.toolsAnAgentCanCall')}>
      <div className="space-y-3">
        {Object.entries(TOOL_GROUPS).map(([cat, tools]) => (
          <div key={cat}>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-gray-400 mb-1">{cat}</p>
            <div className="flex flex-wrap gap-1.5">
              {tools.map((t) => (
                <span key={t.name} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-lg bg-gray-50 border border-gray-200">
                  <Wrench className="w-3 h-3 text-indigo-400" />
                  <span className="font-mono text-gray-700">{t.name}</span>
                  <span className="text-gray-400">({t.args})</span>
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
      <p className="text-[10px] font-semibold uppercase tracking-wider text-gray-400 mt-3 mb-1">{t('exampleWidgets.exampleCall')}</p>
      <pre className="text-[11px] text-gray-100 bg-gray-900 rounded-lg p-3 overflow-x-auto">{`read_file({ "path": "auth/oauth.py" })
→ "def login(): ..."`}</pre>
    </DemoFrame>
  );
}

// ---------------------------------------------------------------------------
// Memory — synthetic layers with tabs
// ---------------------------------------------------------------------------
const MEM_ICONS = { shared: FileText, episodic: Activity, procedural: Hash, graph: Network, rag: CalendarDays };
const memLayers = (t) => ['shared', 'episodic', 'procedural', 'graph', 'rag'].map((id) => ({
  id,
  icon: MEM_ICONS[id],
  label: t(`exampleWidgets.memLayers.${id}.label`),
  body: t(`exampleWidgets.memLayers.${id}.body`),
}));

export function MemoryExample() {
  const { t } = useI18n();
  const MEM_LAYERS = memLayers(t);
  const [tab, setTab] = useState('shared');
  const layer = MEM_LAYERS.find((l) => l.id === tab);
  const Icon = layer.icon;
  return (
    <DemoFrame label={t('exampleWidgets.theFiveMemoryLayers')}>
      <div className="flex flex-wrap gap-1 mb-3">
        {MEM_LAYERS.map((l) => {
          const LIcon = l.icon;
          return (
            <button key={l.id} onClick={() => setTab(l.id)} className={`flex items-center gap-1 text-xs px-2 py-1 rounded-lg border ${tab === l.id ? 'border-indigo-400 bg-indigo-50 text-indigo-700' : 'border-gray-200 text-gray-600'}`}>
              <LIcon className="w-3.5 h-3.5" /> {l.label}
            </button>
          );
        })}
      </div>
      <div className="flex items-start gap-2">
        <Icon className="w-4 h-4 text-indigo-400 mt-2 shrink-0" />
        <pre className="flex-1 text-[11px] leading-relaxed text-gray-700 bg-gray-50 rounded-lg p-3 whitespace-pre-wrap min-h-[5rem]">{layer.body}</pre>
      </div>
      <p className="text-[11px] text-gray-400 mt-2">{t('exampleWidgets.agentsReadWriteTheseWith')} <code className="bg-gray-100 px-1 rounded">{t('exampleWidgets.recall')}</code> / <code className="bg-gray-100 px-1 rounded">{t('exampleWidgets.remember')}</code> / <code className="bg-gray-100 px-1 rounded">{t('exampleWidgets.recordEpisode')}</code> / <code className="bg-gray-100 px-1 rounded">{t('exampleWidgets.link')}</code>.</p>
    </DemoFrame>
  );
}

// ---------------------------------------------------------------------------
// Nodes & Sessions — synthetic node + run timeline
// ---------------------------------------------------------------------------
export function NodeSessionExample() {
  const { t } = useI18n();
  const events = [
    ['00:00', t('exampleWidgets.sessionEvents.start')],
    ['00:01', 'Tool call: read_file(auth/oauth.py)'],
    ['00:04', 'Tool call: write_file(auth/oauth.py)'],
    ['00:09', 'Tool call: run_command(pytest)'],
    ['00:12', t('exampleWidgets.sessionEvents.done')],
  ];
  return (
    <DemoFrame label={t('exampleWidgets.aNodeRunningOneSession')}>
      <div className="rounded-lg border border-gray-200 p-2.5 mb-3 flex items-center gap-2 text-sm">
        <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
        <span className="font-medium text-gray-800">{t('exampleWidgets.sweAgent')}</span>
        <Badge color="blue">{t('exampleWidgets.running')}</Badge>
        <span className="ml-auto text-[11px] text-gray-400">{t('exampleWidgets.node1ActiveSession')}</span>
      </div>
      <ol className="space-y-1.5">
        {events.map(([t, e], i) => (
          <li key={i} className="flex gap-3 text-xs">
            <span className="font-mono text-gray-400 shrink-0">{t}</span>
            <span className="text-gray-600">{e}</span>
          </li>
        ))}
      </ol>
    </DemoFrame>
  );
}

// ---------------------------------------------------------------------------
// Orchestrator — interactive routing demo
// ---------------------------------------------------------------------------
const candidates = (t) => [
  { id: 'devops_agent', match: 92, why: t('exampleWidgets.candidates.devops') },
  { id: 'swe_agent', match: 61, why: t('exampleWidgets.candidates.swe') },
  { id: 'researcher', match: 18, why: t('exampleWidgets.candidates.researcher') },
];

export function OrchestratorExample() {
  const { t } = useI18n();
  const CANDIDATES = candidates(t);
  const [routed, setRouted] = useState(false);
  const winner = CANDIDATES[0];
  return (
    <DemoFrame label={t('exampleWidgets.autoAssigningATask')}>
      <div className="rounded-lg bg-gray-50 border border-gray-200 p-2.5 text-sm text-gray-800 mb-3">
        Task: <strong>{t('exampleWidgets.fixTheFailingCiPipeline')}</strong>
      </div>
      <div className="flex items-center justify-center gap-2 text-gray-400 mb-3">
        <ArrowDown className="w-4 h-4" /> <Network className="w-4 h-4" /> <span className="text-xs">{t('exampleWidgets.orchestratorScoresAgentsByCapability')}</span>
      </div>
      <div className="space-y-1.5">
        {CANDIDATES.map((c) => (
          <div key={c.id} className={`flex items-center gap-2 p-2 rounded-lg border ${routed && c.id === winner.id ? 'border-green-300 bg-green-50' : 'border-gray-100'}`}>
            <Bot className="w-4 h-4 text-gray-400 shrink-0" />
            <span className="text-sm font-medium text-gray-800">{c.id}</span>
            <span className="text-[11px] text-gray-400">{c.why}</span>
            <div className="ml-auto flex items-center gap-2">
              <div className="w-20 h-1.5 bg-gray-100 rounded-full overflow-hidden"><div className="h-full bg-indigo-400" style={{ width: `${c.match}%` }} /></div>
              <span className="text-[11px] font-semibold text-gray-500 w-8 text-right">{c.match}%</span>
            </div>
            {routed && c.id === winner.id && <CheckCircle2 className="w-4 h-4 text-green-500 shrink-0" />}
          </div>
        ))}
      </div>
      <button onClick={() => setRouted(true)} disabled={routed} className="mt-3 text-xs px-3 py-1.5 rounded-lg bg-indigo-600 text-white disabled:opacity-50">
        {routed ? t('exampleWidgets.assignedTo', { agent: winner.id }) : t('exampleWidgets.routeTask')}
      </button>
    </DemoFrame>
  );
}

// ---------------------------------------------------------------------------
// Providers — synthetic config + .env snippet
// ---------------------------------------------------------------------------
const PROVIDERS = [
  { name: 'OpenAI', model: 'gpt-5', color: 'green' },
  { name: 'Anthropic', model: 'claude-opus-4-8', color: 'amber' },
  { name: 'Ollama', model: 'llama3 (local)', color: 'violet' },
];

export function ProviderExample() {
  const { t } = useI18n();
  return (
    <DemoFrame label={t('exampleWidgets.configuringModels')}>
      <div className="grid sm:grid-cols-3 gap-2 mb-3">
        {PROVIDERS.map((p) => (
          <div key={p.name} className="p-2.5 rounded-xl border border-gray-200">
            <div className="flex items-center gap-1.5"><Cpu className="w-3.5 h-3.5 text-gray-400" /><span className="text-sm font-medium text-gray-800">{p.name}</span></div>
            <Badge color={p.color}>{p.model}</Badge>
          </div>
        ))}
      </div>
      <pre className="text-[11px] text-gray-100 bg-gray-900 rounded-lg p-3 overflow-x-auto">{`DEFAULT_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-4-8`}</pre>
      <p className="text-[11px] text-gray-400 mt-2">{t('exampleWidgets.eachWorkspaceCanOverrideThe')}</p>
    </DemoFrame>
  );
}
