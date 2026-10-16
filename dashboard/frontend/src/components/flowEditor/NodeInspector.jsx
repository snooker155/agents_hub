import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Plus, XCircle } from 'lucide-react';
import { useI18n } from '../../i18n';
import { getLoops, getTeams, listFlows, listN8nWorkflows } from '../../api';
import { useWorkspace } from '../workspace';
import { InfoHint } from '../PageLayout';
import { INPUT_CLS, fmtKeys, isUnchosenAgent, parseKeys } from './graphHelpers';
import { MapConfig, RouteConfig, SwitchConfig } from './BranchConfig';

// Per-node execution policy: how many times a failed attempt is repeated and
// how long one attempt may take. Both are optional; left at zero the node
// behaves exactly as it did before retries and timeouts existed.
export function NodePolicy({ node, onPatch }) {
  const { t } = useI18n();
  const d = node.data || {};
  const retry = d.retry || {};
  const patchRetry = (patch) => {
    const next = { max: Number(retry.max) || 0, backoff_seconds: Number(retry.backoff_seconds) || 0, ...patch };
    onPatch({ retry: (next.max > 0 || next.backoff_seconds > 0) ? next : null });
  };

  return (
    <div className="space-y-2 rounded-[20px] border border-slate-200 bg-slate-50 p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        {t('flowEditor.nodePolicy')}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="block text-[11px] font-medium text-slate-500">
          {t('flowEditor.retryMax')}
          <input
            type="number"
            min="0"
            value={retry.max ?? 0}
            onChange={(e) => patchRetry({ max: Number(e.target.value) || 0 })}
            className={`${INPUT_CLS} mt-1`}
          />
        </label>
        <label className="block text-[11px] font-medium text-slate-500">
          {t('flowEditor.retryBackoff')}
          <input
            type="number"
            min="0"
            step="0.5"
            value={retry.backoff_seconds ?? 0}
            onChange={(e) => patchRetry({ backoff_seconds: Number(e.target.value) || 0 })}
            className={`${INPUT_CLS} mt-1`}
          />
        </label>
      </div>
      <label className="block text-[11px] font-medium text-slate-500">
        {t('flowEditor.timeoutSeconds')}
        <input
          type="number"
          min="0"
          value={d.timeout_seconds ?? ''}
          onChange={(e) => onPatch({ timeout_seconds: e.target.value })}
          placeholder={t('flowEditor.noTimeout')}
          className={`${INPUT_CLS} mt-1`}
        />
      </label>
      <p className="text-[11px] leading-5 text-slate-400">{t('flowEditor.nodePolicyHint')}</p>
    </div>
  );
}

// The human_interrupt node asks a person and parks the run. Its config is two
// fields and a list, so it gets a form rather than the raw JSON box: a blank
// question is the one way to make the node park a run on an empty prompt.
export function InterruptConfig({ node, onPatch }) {
  const { t } = useI18n();
  const d = node.data || {};
  const config = d.config || {};
  const choices = Array.isArray(config.choices) ? config.choices : [];
  const patchConfig = (patch) => onPatch({ config: { ...config, ...patch } });
  const setChoice = (index, value) =>
    patchConfig({ choices: choices.map((c, i) => (i === index ? value : c)) });

  return (
    <div className="space-y-3">
      <div>
        <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          {t('flowEditor.interruptQuestion')}
        </label>
        <textarea
          value={config.question || ''}
          onChange={(e) => patchConfig({ question: e.target.value })}
          rows={3}
          placeholder={t('flowEditor.interruptQuestionPlaceholder')}
          className={INPUT_CLS}
        />
        <p className="px-1 pt-1 text-[11px] text-slate-400">{t('flowEditor.interruptQuestionHint')}</p>
      </div>

      <div className="space-y-2">
        <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          {t('flowEditor.interruptChoices')}
        </label>
        {choices.map((choice, index) => (
          <div key={index} className="flex items-center gap-2">
            <input
              value={choice}
              onChange={(e) => setChoice(index, e.target.value)}
              className={INPUT_CLS}
            />
            <button
              type="button"
              onClick={() => patchConfig({ choices: choices.filter((_, i) => i !== index) })}
              className="shrink-0 rounded-xl border border-slate-200 px-2 py-2 text-slate-400 transition hover:border-rose-300 hover:text-rose-500"
              aria-label={t('flowEditor.removeChoice')}
            >
              <XCircle className="h-4 w-4" />
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => patchConfig({ choices: [...choices, ''] })}
          className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-cyan-300 hover:bg-cyan-50 hover:text-cyan-700"
        >
          <Plus className="h-3.5 w-3.5" />
          {t('flowEditor.addChoice')}
        </button>
        <p className="px-1 text-[11px] text-slate-400">{t('flowEditor.interruptChoicesHint')}</p>
      </div>

      <div>
        <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          {t('flowEditor.interruptOutputKey')}
        </label>
        <input
          value={(d.output || [])[0] || ''}
          onChange={(e) => onPatch({ output: e.target.value.trim() ? [e.target.value.trim()] : [] })}
          placeholder="answer"
          className={INPUT_CLS}
        />
        <p className="px-1 pt-1 text-[11px] text-slate-400">{t('flowEditor.interruptOutputKeyHint')}</p>
      </div>
    </div>
  );
}

// Container nodes run a team, a loop or another flow as a nested run. Which
// catalog the id comes from is keyed by entity id (the registry marks the same
// field with a config_schema `source` hint: teams, loops or flows).
const CONTAINER_FIELDS = {
  run_team: { idKey: 'team_id', load: getTeams, pick: (d) => d?.teams, idOf: (x) => x.team_id, seed: false },
  run_loop: { idKey: 'loop_id', load: getLoops, pick: (d) => d?.loops, idOf: (x) => x.loop_id, seed: true },
  run_flow: { idKey: 'flow_id', load: listFlows, pick: (d) => d?.flows || d, idOf: (x) => x.id, seed: true },
};

export function ContainerConfig({ node, onPatch }) {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const d = node.data || {};
  const config = d.config || {};
  const fields = CONTAINER_FIELDS[d.entity_id];
  const [options, setOptions] = useState([]);
  const patchConfig = (patch) => onPatch({ config: { ...config, ...patch } });

  useEffect(() => {
    if (!fields) return undefined;
    let alive = true;
    fields.load(selectedWorkspace || undefined)
      .then((r) => {
        const list = fields.pick(r.data);
        if (alive) setOptions(Array.isArray(list) ? list : []);
      })
      .catch(() => { if (alive) setOptions([]); });
    return () => { alive = false; };
  }, [fields, selectedWorkspace]);

  if (!fields) return null;
  const current = config[fields.idKey] || '';
  const known = options.some((o) => fields.idOf(o) === current);

  return (
    <div className="space-y-3">
      <div>
        <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          {t(`flowEditor.container.${d.entity_id}`)}
        </label>
        <select
          value={current}
          onChange={(e) => patchConfig({ [fields.idKey]: e.target.value })}
          className={INPUT_CLS}
        >
          <option value="">{t('flowEditor.container.choose')}</option>
          {current && !known ? <option value={current}>{current}</option> : null}
          {options.map((o) => (
            <option key={fields.idOf(o)} value={fields.idOf(o)}>{o.name || fields.idOf(o)}</option>
          ))}
        </select>
      </div>
      <div>
        <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          {t('flowEditor.container.goal')}
        </label>
        <textarea
          value={config.goal || ''}
          onChange={(e) => patchConfig({ goal: e.target.value })}
          rows={3}
          placeholder={t('flowEditor.interruptQuestionPlaceholder')}
          className={INPUT_CLS}
        />
        <p className="px-1 pt-1 text-[11px] text-slate-400">{t('flowEditor.interruptQuestionHint')}</p>
      </div>
      {fields.seed ? (
        <div>
          <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            {t('flowEditor.container.seedKeys')}
          </label>
          <input
            value={fmtKeys(config.seed_keys)}
            onChange={(e) => patchConfig({ seed_keys: parseKeys(e.target.value) })}
            placeholder={t('flowEditor.commaSeparatedKeys')}
            className={INPUT_CLS}
          />
        </div>
      ) : null}
      <p className="px-1 text-[11px] leading-5 text-slate-400">{t('flowEditor.container.hint')}</p>
      <NodePolicy node={node} onPatch={onPatch} />
    </div>
  );
}

// The run_n8n node: which n8n workflow it starts and what it sends. The
// workflow list needs an API key on the n8n connector; without one the path
// of the Webhook or Chat trigger is typed in.
export function N8nConfig({ node, onPatch }) {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const d = node.data || {};
  const config = d.config || {};
  const [workflows, setWorkflows] = useState(null);
  const [listError, setListError] = useState('');
  const patchConfig = (patch) => onPatch({ config: { ...config, ...patch } });

  useEffect(() => {
    let alive = true;
    listN8nWorkflows(selectedWorkspace || undefined)
      .then((r) => { if (alive) setWorkflows(Array.isArray(r.data) ? r.data : []); })
      .catch((e) => {
        if (!alive) return;
        setWorkflows([]);
        setListError(e.response?.data?.detail || e.message || '');
      });
    return () => { alive = false; };
  }, [selectedWorkspace]);

  const current = config.workflow_id || '';
  const known = (workflows || []).some((w) => w.id === current);
  const label = (key) => (
    <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{t(key)}</label>
  );

  return (
    <div className="space-y-3">
      <div>
        {label('flowEditor.n8n.workflow')}
        <select
          value={current}
          onChange={(e) => patchConfig({ workflow_id: e.target.value })}
          className={INPUT_CLS}
        >
          <option value="">{t('flowEditor.container.choose')}</option>
          {current && !known ? <option value={current}>{current}</option> : null}
          {(workflows || []).map((w) => (
            <option key={w.id} value={w.id}>{w.name || w.id}{w.active ? '' : ` (${t('flowEditor.n8n.inactive')})`}</option>
          ))}
        </select>
        {listError ? (
          <p className="px-1 pt-1 text-[11px] text-amber-600">
            {listError}{' '}
            <Link to="/connectors?tab=n8n" className="font-semibold underline">{t('flowEditor.n8n.openConnector')}</Link>
          </p>
        ) : null}
      </div>
      <div>
        {label('flowEditor.n8n.webhookPath')}
        <input
          value={config.webhook_path || ''}
          onChange={(e) => patchConfig({ webhook_path: e.target.value.trim() })}
          placeholder="my-hook  |  <webhookId>/chat"
          className={INPUT_CLS}
        />
        <p className="px-1 pt-1 text-[11px] text-slate-400">{t('flowEditor.n8n.webhookPathHint')}</p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          {label('flowEditor.n8n.trigger')}
          <select
            value={config.trigger || 'auto'}
            onChange={(e) => patchConfig({ trigger: e.target.value })}
            className={INPUT_CLS}
          >
            <option value="auto">auto</option>
            <option value="webhook">webhook</option>
            <option value="chat">chat</option>
          </select>
        </div>
        <div>
          {label('flowEditor.n8n.method')}
          <select
            value={config.method || 'POST'}
            onChange={(e) => patchConfig({ method: e.target.value })}
            className={INPUT_CLS}
          >
            {['POST', 'GET', 'PUT', 'PATCH'].map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
      </div>
      <div>
        {label('flowEditor.container.goal')}
        <textarea
          value={config.goal || ''}
          onChange={(e) => patchConfig({ goal: e.target.value })}
          rows={3}
          placeholder={t('flowEditor.interruptQuestionPlaceholder')}
          className={INPUT_CLS}
        />
        <p className="px-1 pt-1 text-[11px] text-slate-400">{t('flowEditor.interruptQuestionHint')}</p>
      </div>
      <div>
        {label('flowEditor.n8n.sendKeys')}
        <input
          value={fmtKeys(config.send_keys)}
          onChange={(e) => patchConfig({ send_keys: parseKeys(e.target.value) })}
          placeholder={t('flowEditor.commaSeparatedKeys')}
          className={INPUT_CLS}
        />
      </div>
      <p className="px-1 text-[11px] leading-5 text-slate-400">{t('flowEditor.n8n.hint')}</p>
      <NodePolicy node={node} onPatch={onPatch} />
    </div>
  );
}

// Which agent runs in an agent node. The palette has one abstract agent node;
// this is where it becomes a particular agent. The node's label follows the
// agent unless it was renamed by hand.
export function AgentPicker({ node, agents, onPatch }) {
  const { t } = useI18n();
  const d = node.data || {};
  const current = d.agent_id || '';
  const known = agents.some((a) => a.id === current);
  const choose = (agentId) => {
    const agent = agents.find((a) => a.id === agentId);
    const previous = agents.find((a) => a.id === current);
    const labelIsDefault = !d.label || d.label === t('flowFlowCanvas.agentNode') || d.label === previous?.name;
    onPatch({
      agent_id: agentId,
      ...(agent && labelIsDefault ? { label: agent.name } : {}),
      ...(agent ? { description: agent.description || '', domain: agent.group || agent.domain || 'general' } : {}),
    });
  };
  return (
    <div className="space-y-1">
      <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        {t('flowEditor.agent')}
      </label>
      <select
        value={current}
        onChange={(e) => choose(e.target.value)}
        className={`${INPUT_CLS} ${isUnchosenAgent(d) ? 'border-amber-400' : ''}`}
        data-testid="node-agent-picker"
      >
        <option value="">{t('flowEditor.chooseAgent')}</option>
        {current && !known ? <option value={current}>{current}</option> : null}
        {agents.map((a) => (
          <option key={a.id} value={a.id}>{a.name || a.id}</option>
        ))}
      </select>
      {isUnchosenAgent(d) ? (
        <p className="px-1 text-[11px] text-amber-600">{t('flowEditor.chooseAgentHint')}</p>
      ) : null}
    </div>
  );
}

// Node inspector: edits label/description/task for any node, plus the state
// contract (input/output keys) and config JSON for non-agent entity nodes.
// Running a node is the run page's business (pages/FlowRun.jsx), not this one.
export function NodeInspector({ node, isAgent, onPatch, agents = [], successors = [] }) {
  const { t } = useI18n();
  const d = node.data || {};
  const ident = isAgent ? d.agent_id : d.entity_id;
  const category = d.category || (isAgent ? 'agent' : '');

  // Local text state for the config JSON so invalid intermediate input doesn't
  // wipe the stored object; committed to node data only when it parses.
  // Selecting another node remounts this panel (key={node.id} at the call site),
  // so the draft below starts from that node's config, no re-sync effect.
  const [configText, setConfigText] = useState(JSON.stringify(d.config || {}, null, 2));
  const [configErr, setConfigErr] = useState('');

  const commitConfig = (text) => {
    setConfigText(text);
    if (!text.trim()) {
      setConfigErr('');
      onPatch({ config: {} });
      return;
    }
    try {
      const parsed = JSON.parse(text);
      setConfigErr('');
      onPatch({ config: parsed });
    } catch {
      setConfigErr(t('flowEditor.invalidJson'));
    }
  };

  return (
    <div className="space-y-4">
      {/* Name first, its kind under it; the agent's description, often
          long, waits behind the ⓘ. */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <input
            value={d.label || ''}
            onChange={(e) => onPatch({ label: e.target.value })}
            aria-label={t('flowEditor.nodeLabel')}
            placeholder={t('flowEditor.nodeLabel')}
            className={`${INPUT_CLS} min-w-0 flex-1 font-bold`}
          />
          <InfoHint
            text={d.description ? <span className="whitespace-pre-wrap">{d.description}</span> : null}
            label={t('flowEditor.aboutNode')}
            align="right"
          />
        </div>
        <div className="flex min-w-0 items-center gap-2 px-1">
          {category ? (
            <span className="shrink-0 rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-medium text-slate-600">
              {category}
            </span>
          ) : null}
          {ident ? <span className="truncate text-xs font-medium text-slate-400">{ident}</span> : null}
        </div>
      </div>

      {/* State contract, read/write keys shared via flow state */}
      <div className="space-y-2">
        <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          {t('flowEditor.inputStateKeys')}
        </label>
        <input
          value={fmtKeys(d.input)}
          onChange={(e) => onPatch({ input: parseKeys(e.target.value) })}
          placeholder={t('flowEditor.commaSeparatedKeys')}
          className={INPUT_CLS}
        />
        <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          {t('flowEditor.outputStateKeys')}
        </label>
        <input
          value={fmtKeys(d.output)}
          onChange={(e) => onPatch({ output: parseKeys(e.target.value) })}
          placeholder={t('flowEditor.commaSeparatedKeys')}
          className={INPUT_CLS}
        />
      </div>

      {isAgent ? <AgentPicker node={node} agents={agents} onPatch={onPatch} /> : null}

      {isAgent ? (
        <>
          <textarea
            value={d.nodeTask || ''}
            onChange={(e) => onPatch({ nodeTask: e.target.value })}
            rows={4}
            placeholder={t('flowEditor.optionalNodeSpecificTask')}
            className={INPUT_CLS}
          />
          <RouteConfig node={node} successors={successors} onPatch={onPatch} />
          <NodePolicy node={node} onPatch={onPatch} />
        </>
      ) : d.entity_id === 'map' ? (
        <>
          <MapConfig node={node} agents={agents} onPatch={onPatch} />
          <NodePolicy node={node} onPatch={onPatch} />
        </>
      ) : d.entity_id === 'state_switch' ? (
        <SwitchConfig node={node} successors={successors} onPatch={onPatch} />
      ) : d.entity_id === 'human_interrupt' ? (
        <InterruptConfig node={node} onPatch={onPatch} />
      ) : d.entity_id === 'run_n8n' ? (
        <N8nConfig node={node} onPatch={onPatch} />
      ) : category === 'container' && CONTAINER_FIELDS[d.entity_id] ? (
        <ContainerConfig node={node} onPatch={onPatch} />
      ) : (
        <div className="space-y-1">
          <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Config (JSON)
          </label>
          <textarea
            value={configText}
            onChange={(e) => commitConfig(e.target.value)}
            rows={6}
            spellCheck={false}
            placeholder="{}"
            className={`${INPUT_CLS} font-mono text-xs ${configErr ? 'border-red-300' : ''}`}
          />
          {configErr ? <div className="px-1 text-[11px] text-red-500">{configErr}</div> : null}
        </div>
      )}
    </div>
  );
}

export default NodeInspector;
