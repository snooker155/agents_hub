import { Link } from 'react-router-dom';
import { Bot, CornerDownRight, Loader2, RotateCcw, Settings2, Workflow } from 'lucide-react';
import { useI18n } from '../../i18n';
import { InfoHint } from '../PageLayout';
import { fmtKeys, isUnchosenAgent } from './graphHelpers';

const SECTION_CLS = 'space-y-2 rounded-[20px] border border-slate-200 bg-white p-4';
const TITLE_CLS = 'flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400';
const BUTTON_CLS = 'inline-flex w-full items-center justify-center gap-2 rounded-2xl border px-3 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-60';

// One setting as a label over its value; an unset one says so in grey.
function Row({ label, children, empty }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] font-medium text-slate-400">{label}</div>
      <div className={`break-words text-xs leading-5 ${empty ? 'text-slate-400' : 'text-slate-700'}`}>{children}</div>
    </div>
  );
}

function configValue(value) {
  if (value == null || value === '') return '';
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

// The agent an agent node runs: what it is, as the agent list knows it.
function AgentSection({ agentId, agent, editPath }) {
  const { t } = useI18n();
  const version = agent?.pinned_version
    ? t('flowEditor.nodePanel.versionPinned', { version: agent.pinned_version })
    : agent?.current_version
      ? t('flowEditor.nodePanel.versionCurrent', { version: agent.current_version })
      : '';
  return (
    <div className={SECTION_CLS} data-testid="node-panel-agent">
      <div className={TITLE_CLS}><Bot className="h-3.5 w-3.5" />{t('flowEditor.nodePanel.agent')}</div>
      {!agentId ? (
        <p className="text-xs leading-5 text-amber-600">
          {t('flowEditor.noAgentChosen')}{' '}
          <Link to={editPath} className="font-semibold underline">{t('flowEditor.editFlow')}</Link>
        </p>
      ) : (
        <>
          <Link to={`/agents/${encodeURIComponent(agentId)}`} className="-mx-1.5 inline-block max-w-full truncate rounded-md px-1.5 py-0.5 align-top text-sm font-bold text-cyan-700 transition hover:bg-cyan-50">
            {agent?.name || agentId}
          </Link>
          {agent?.description ? (
            <p className="line-clamp-4 text-xs leading-5 text-slate-500">{agent.description}</p>
          ) : null}
          {!agent ? <p className="text-xs leading-5 text-amber-600">{t('flowEditor.nodePanel.agentMissing')}</p> : null}
          <Row label={t('flowEditor.nodePanel.model')} empty={!agent?.model}>
            {agent?.model ? `${agent.provider ? `${agent.provider} · ` : ''}${agent.model}` : t('flowEditor.nodePanel.defaultModel')}
          </Row>
          {version ? <Row label={t('flowEditor.nodePanel.version')}>{version}</Row> : null}
        </>
      )}
    </div>
  );
}

// What this node is set to in this flow, read only: editing is the editor's
// (the page's Edit button).
function FlowSettingsSection({ node, isAgent }) {
  const { t } = useI18n();
  const d = node.data || {};
  const none = t('flowEditor.nodePanel.none');
  const inputs = fmtKeys(d.input);
  const outputs = fmtKeys(d.output);
  const retry = d.retry || {};
  const config = Object.entries(d.config || {}).filter(([, v]) => configValue(v) !== '');
  const routeMode = d.route === true ? 'one' : (d.route || '');
  return (
    <div className={SECTION_CLS} data-testid="node-panel-settings">
      <div className={TITLE_CLS}><Settings2 className="h-3.5 w-3.5" />{t('flowEditor.nodePanel.inThisFlow')}</div>
      {isAgent ? (
        <Row label={t('flowEditor.nodeInstruction')} empty={!d.nodeTask}>
          <span className="whitespace-pre-wrap">{d.nodeTask || none}</span>
        </Row>
      ) : (
        <Row label={t('flowEditor.nodePanel.kind')}>{d.entity_id || d.category || none}</Row>
      )}
      <Row label={t('flowEditor.inputStateKeys')} empty={!inputs}>{inputs || none}</Row>
      <Row label={t('flowEditor.outputStateKeys')} empty={!outputs}>{outputs || none}</Row>
      {isAgent && routeMode ? (
        <Row label={t('flowEditor.route.title')}>
          {t(`flowEditor.route.${routeMode}`)}
          {d.route_hint ? <span className="block text-slate-500">{d.route_hint}</span> : null}
        </Row>
      ) : null}
      {config.map(([key, value]) => (
        <Row key={key} label={key}><span className="whitespace-pre-wrap">{configValue(value)}</span></Row>
      ))}
      {retry.max > 0 ? (
        <Row label={t('flowEditor.retryMax')}>
          {retry.backoff_seconds > 0
            ? t('flowEditor.nodePanel.retryWithBackoff', { count: retry.max, seconds: retry.backoff_seconds })
            : retry.max}
        </Row>
      ) : null}
      {Number(d.timeout_seconds) > 0 ? (
        <Row label={t('flowEditor.timeoutSeconds')}>{d.timeout_seconds}</Row>
      ) : null}
    </div>
  );
}

// Starting from this node. With a History record picked, the record's own
// inputs for it, run again on their own or on to the end; without one, the
// next chat message goes to it and the flow runs on from there.
function RunSection({ record, running, unchosen, addressed, onRerun, onAddress, onCancelAddress }) {
  const { t } = useI18n();
  if (record) {
    const busy = Boolean(running);
    const recordLive = record.status === 'running';
    const disabled = busy || unchosen || recordLive;
    const title = record.title || t('flowEditor.selectedRun');
    return (
      <div className={SECTION_CLS} data-testid="node-panel-rerun">
        <div className={TITLE_CLS}><RotateCcw className="h-3.5 w-3.5" />{t('flowEditor.nodePanel.rerunTitle')}</div>
        <p className="text-xs leading-5 text-slate-500">
          {recordLive ? t('flowEditor.nodePanel.rerunWhileRunning') : t('flowEditor.nodePanel.rerunHint', { run: title })}
        </p>
        <button type="button" onClick={() => onRerun('single')} disabled={disabled}
          className={`${BUTTON_CLS} border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100`}
          data-testid="node-rerun-single">
          {running === 'single' ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
          {t('flowEditor.nodePanel.rerunSingle')}
        </button>
        <button type="button" onClick={() => onRerun('rest')} disabled={disabled}
          className={`${BUTTON_CLS} border-cyan-200 bg-cyan-50 text-cyan-700 hover:bg-cyan-100`}
          data-testid="node-rerun-rest">
          {running === 'rest' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Workflow className="h-4 w-4" />}
          {t('flowEditor.nodePanel.rerunRest')}
        </button>
      </div>
    );
  }
  return (
    <div className={SECTION_CLS} data-testid="node-panel-start">
      <div className={TITLE_CLS}><CornerDownRight className="h-3.5 w-3.5" />{t('flowEditor.nodePanel.startTitle')}</div>
      <p className="text-xs leading-5 text-slate-500">{t('flowEditor.nodePanel.startHint')}</p>
      {addressed ? (
        <button type="button" onClick={onCancelAddress}
          className={`${BUTTON_CLS} border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100`}
          data-testid="node-start-cancel">
          {t('flowEditor.nodePanel.startCancel')}
        </button>
      ) : (
        <button type="button" onClick={onAddress} disabled={unchosen}
          className={`${BUTTON_CLS} border-cyan-200 bg-cyan-50 text-cyan-700 hover:bg-cyan-100`}
          data-testid="node-start">
          <CornerDownRight className="h-4 w-4" />
          {t('flowEditor.nodePanel.start')}
        </button>
      )}
    </div>
  );
}

/**
 * The run page's right column for a picked node (pages/FlowRun.jsx): what
 * the node is, its agent and what it is set to in this flow (read only,
 * editing is the editor's), and starting the flow from it.
 *
 * `record` is the History record picked, or null; `running` is the rerun
 * mode being started for this node (`single` | `rest`), or null.
 */
export default function NodeRunPanel({
  node, agents = [], editPath, record = null, running = null, addressed = false,
  onRerun, onAddress, onCancelAddress,
}) {
  const { t } = useI18n();
  const d = node.data || {};
  const isAgent = d.category === 'agent' || !d.category;
  const unchosen = isUnchosenAgent(d);
  const agent = isAgent ? agents.find((a) => a.id === d.agent_id) || null : null;
  return (
    <div className="space-y-3" data-testid="node-run-panel">
      <div className="rounded-[20px] border border-slate-200 bg-slate-50 p-4">
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1 truncate text-sm font-bold text-slate-900">{d.label}</div>
          <InfoHint
            text={d.description ? <span className="whitespace-pre-wrap">{d.description}</span> : null}
            label={t('flowEditor.aboutNode')}
            align="right"
          />
        </div>
        <div className="mt-1.5 flex min-w-0 items-center gap-2">
          <span className="shrink-0 rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-medium text-slate-600">
            {d.category || 'agent'}
          </span>
          {!isAgent && d.entity_id ? (
            <span className="truncate text-xs font-medium text-slate-400">{d.entity_id}</span>
          ) : null}
        </div>
      </div>
      {isAgent ? <AgentSection agentId={unchosen ? '' : d.agent_id} agent={agent} editPath={editPath} /> : null}
      <FlowSettingsSection node={node} isAgent={isAgent} />
      <RunSection
        record={record}
        running={running}
        unchosen={unchosen}
        addressed={addressed}
        onRerun={onRerun}
        onAddress={onAddress}
        onCancelAddress={onCancelAddress}
      />
    </div>
  );
}
