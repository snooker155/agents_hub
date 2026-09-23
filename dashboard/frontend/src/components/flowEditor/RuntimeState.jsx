import { useMemo, useState } from 'react';
import { ChevronDown, ChevronUp, Workflow } from 'lucide-react';
import { useI18n } from '../../i18n';
import { INPUT_CLS } from './graphHelpers';

// Graph-tab state block: edits the flow's seed/initial state JSON and can
// auto-build the state structure by scanning every node's declared input/output
// keys, adding any that are missing with an empty-string default.
export function GraphStateBlock({ flow, nodes, onPatch }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  // Keyed on flow.id at the call site, so opening another flow remounts this
  // block with that flow's state as the draft.
  const [stateText, setStateText] = useState(JSON.stringify(flow.state || {}, null, 2));
  const [stateErr, setStateErr] = useState('');

  const commitState = (text) => {
    setStateText(text);
    if (!text.trim()) { setStateErr(''); onPatch({ state: {} }); return; }
    try { onPatch({ state: JSON.parse(text) }); setStateErr(''); }
    catch { setStateErr(t('flowEditor.invalidJson')); }
  };

  // All state keys referenced by node contracts (input + output), de-duplicated.
  const contractKeys = useMemo(() => {
    const keys = new Set();
    for (const node of nodes) {
      const d = node.data || {};
      for (const k of d.input || []) if (k) keys.add(k);
      for (const k of d.output || []) if (k) keys.add(k);
    }
    return [...keys];
  }, [nodes]);

  const current = useMemo(() => {
    try { return JSON.parse(stateText || '{}'); } catch { return null; }
  }, [stateText]);

  const missingKeys = current ? contractKeys.filter((k) => !(k in current)) : [];

  const buildFromGraph = () => {
    const base = current && typeof current === 'object' ? current : {};
    const next = { ...base };
    for (const k of contractKeys) if (!(k in next)) next[k] = '';
    const text = JSON.stringify(next, null, 2);
    setStateText(text);
    setStateErr('');
    onPatch({ state: next });
  };

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between py-1 text-sm font-bold text-slate-900"
      >
        State
        {open ? <ChevronUp className="h-4 w-4 text-slate-400" /> : <ChevronDown className="h-4 w-4 text-slate-400" />}
      </button>
      {open ? (
        <div className="space-y-3 pt-2">
          <div className="flex items-center justify-between gap-2">
            <div className="text-[11px] text-slate-400">
              {contractKeys.length} key{contractKeys.length === 1 ? '' : 's'} used by nodes
              {missingKeys.length ? ` · ${missingKeys.length} missing` : ''}
            </div>
            <button
              type="button"
              onClick={buildFromGraph}
              disabled={!current || !contractKeys.length}
              className="inline-flex items-center gap-1.5 rounded-xl border border-cyan-200 bg-cyan-50 px-3 py-1.5 text-xs font-semibold text-cyan-700 transition hover:bg-cyan-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Workflow className="h-3.5 w-3.5" />
              {t('flowEditor.buildFromGraph')}
            </button>
          </div>
          <textarea
            value={stateText}
            onChange={(e) => commitState(e.target.value)}
            rows={6}
            spellCheck={false}
            placeholder="{}"
            className={`${INPUT_CLS} font-mono text-xs ${stateErr ? 'border-red-300' : ''}`}
          />
          {stateErr ? <div className="px-1 text-[11px] text-red-500">{stateErr}</div> : null}
          <div className="px-1 text-[11px] text-slate-400">
            {t('flowEditor.seedValuesHint')}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// One value card in the runtime-state block. `accent` lets the second section
// (unkeyed node outputs) read visually distinct from declared state keys.
export function StateEntry({ label, value, accent = 'cyan' }) {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  const isEmpty = text === '' || text === '""' || value == null;
  const labelColor = accent === 'slate'
    ? 'text-slate-500 dark:text-slate-400'
    : 'text-cyan-700 dark:text-cyan-400';
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-800/60">
      <div className={`font-mono text-[10px] font-semibold ${labelColor}`}>{label}</div>
      <pre className={`mt-0.5 max-h-28 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-[1.5] ${isEmpty ? 'text-slate-400 dark:text-slate-500' : 'text-slate-700 dark:text-slate-300'}`}>
        {isEmpty ? '(pending)' : text}
      </pre>
    </div>
  );
}

// Logs-tab runtime state block: shows the live shared-state values produced as a
// run unfolds. Declared state keys ride on flow_start / per-node events emitted
// by the engine (`state` field), we render the latest snapshot seen. Agent node
// outputs are NOT shared state: nodes that declare no output keys never write to
// it, so when a flow declares no state fields this block shows nothing.
export function RuntimeStateBlock({ logs }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);

  // Latest snapshot wins: walk the stream and keep the most recent `state`.
  const snapshot = useMemo(() => {
    let latest = null;
    for (const log of logs) {
      if (log && log.state && typeof log.state === 'object') latest = log.state;
    }
    return latest;
  }, [logs]);

  const entries = snapshot ? Object.entries(snapshot) : [];
  if (!entries.length) return null;
  const total = entries.length;

  return (
    <div className="shrink-0 border-b border-slate-200 dark:border-slate-700">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500"
      >
        <span>{t('flowEditor.runtimeState')} · {t('flowEditor.valueCount', { count: total })}</span>
        {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
      </button>
      {open ? (
        <div className="max-h-[40vh] overflow-y-auto px-3 pb-2">
          <div className="space-y-1.5">
            {entries.map(([key, value]) => (
              <StateEntry key={`k-${key}`} label={key} value={value} />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default RuntimeStateBlock;
