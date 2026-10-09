/**
 * Docs sections for the workspace, project, agent and view features.
 */
import { Callout, CodeBlock, H2, H3, P, Rich, Walkthrough } from './DocsPrimitives';
import {
  AgentDefinitionExample, FlowDiagramExample, ImportedAgentExample, MarketplaceExample,
  ProjectCardExample, TaskLifecycleExample, WorkspaceModelExample,
} from './ExampleWidgets';
import { useI18n } from '../../i18n';

export function FeatureDoc({ title, lead, points, widget }) {
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

export function WorkspacesDoc() {
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

export function ProjectsDoc() {
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

export function TasksDoc() {
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

export function AgentsDoc() {
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

export function ImportingAgentsDoc() {
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

export function MarketplaceDoc() {
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

export function SkillsDoc() {
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

export function WebLogsDoc() {
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

export function FlowsDoc() {
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

export function ViewsDoc() {
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
