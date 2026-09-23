import { useState } from 'react';
import { Play, Plus, XCircle } from 'lucide-react';
import { useI18n } from '../../i18n';
import { INPUT_CLS, fmtKeys, parseKeys } from './graphHelpers';

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

// Node inspector: edits label/description/task for any node, plus the state
// contract (input/output keys) and config JSON for non-agent entity nodes.
export function NodeInspector({ node, isAgent, onPatch, onRun, running }) {
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
      <div className="rounded-[20px] border border-slate-200 bg-slate-50 p-4">
        <div className="flex items-center justify-between gap-2">
          <div className="text-sm font-bold text-slate-900">{d.label}</div>
          {category ? (
            <span className="shrink-0 rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-medium text-slate-600">
              {category}
            </span>
          ) : null}
        </div>
        <div className="mt-1 text-xs font-medium text-slate-400">{ident}</div>
        {d.description ? (
          <div className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-500">{d.description}</div>
        ) : null}
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

      {isAgent ? (
        <>
          <textarea
            value={d.nodeTask || ''}
            onChange={(e) => onPatch({ nodeTask: e.target.value })}
            rows={4}
            placeholder={t('flowEditor.optionalNodeSpecificTask')}
            className={INPUT_CLS}
          />
          <NodePolicy node={node} onPatch={onPatch} />
        </>
      ) : d.entity_id === 'human_interrupt' ? (
        <InterruptConfig node={node} onPatch={onPatch} />
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

      <button
        onClick={onRun}
        disabled={running}
        className="inline-flex items-center gap-2 rounded-2xl border border-cyan-200 bg-cyan-50 px-4 py-2 text-sm font-semibold text-cyan-700 transition hover:bg-cyan-100 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <Play className="h-4 w-4" />
        {running ? t('flowEditor.runningNode') : t('flowEditor.runSelectedNode')}
      </button>
    </div>
  );
}

export default NodeInspector;
