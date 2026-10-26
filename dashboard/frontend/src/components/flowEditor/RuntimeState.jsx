import { useMemo, useState } from 'react';
import { ChevronDown, ChevronUp, Workflow } from 'lucide-react';
import { useI18n } from '../../i18n';
import { INPUT_CLS } from './graphHelpers';
import ProcessStepModal from '../ProcessStepModal';
import { initialFlowState } from './stateKeys';

// Graph-tab state block: edits the flow's seed/initial state JSON and can
// auto-build the state structure by scanning every node's declared input/output
// keys, adding any that are missing with an empty-string default.
export function GraphStateBlock({ flow, nodes, onPatch }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  // Keyed on flow.id at the call site, so opening another flow remounts this
  // block with that flow's state as the draft: the state a run starts from,
  // so `user_request` shows even when the flow declares nothing.
  const [stateText, setStateText] = useState(JSON.stringify(initialFlowState(flow), null, 2));
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

// One key of the runtime-state object, as a line of JSON cut to two lines; a
// click opens the whole state (RuntimeStateBlock's modal).
// Folded, a value keeps no more than this many characters: two lines of the
// block hold fewer, so the cut never shows, but a huge value is not laid out.
const FOLDED_CHARS = 300;

function StateLine({ name, value, last, onOpen }) {
  const isEmpty = value == null || value === '';
  let text = JSON.stringify(value ?? null);
  if (text.length > FOLDED_CHARS) text = `${text.slice(0, FOLDED_CHARS)}…`;
  return (
    // The clamp sits on an inner block: a button's own display would
    // override the -webkit-box that line-clamp needs.
    <button type="button" onClick={onOpen} className="block w-full pl-4 text-left">
      <span className="line-clamp-2 break-words" data-testid="state-line">
        <span className="text-cyan-700 dark:text-cyan-400">{JSON.stringify(name)}</span>
        <span className="text-slate-400">: </span>
        <span className={isEmpty ? 'text-slate-400 dark:text-slate-500' : 'text-slate-700 dark:text-slate-300'}>{text}</span>
        {last ? null : <span className="text-slate-400">,</span>}
      </span>
    </button>
  );
}

// Logs-tab runtime state block: shows the live shared-state values produced as a
// run unfolds. Declared state keys ride on flow_start / per-node events emitted
// by the engine (`state` field), we render the latest snapshot seen. Agent node
// outputs are NOT shared state: nodes that declare no output keys never write to
// it, so when a flow declares no state fields this block shows nothing.
// `initial` is the flow's own starting state (its declared `state`, a
// `user_request` among it): shown until a run brings a snapshot of its own.
export function RuntimeStateBlock({ logs, initial = null }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  // The whole state in a modal, every value in full.
  const [shown, setShown] = useState(false);

  // Latest snapshot wins: walk the stream and keep the most recent `state`.
  const snapshot = useMemo(() => {
    let latest = null;
    for (const log of logs) {
      if (log && log.state && typeof log.state === 'object') latest = log.state;
    }
    if (latest) return latest;
    return initial && typeof initial === 'object' && !Array.isArray(initial) ? initial : null;
  }, [logs, initial]);

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
          {/* The whole state as one JSON object, a line per key. */}
          <div className="rounded-lg border border-slate-200 bg-white px-2.5 py-2 font-mono text-[11px] leading-4 dark:border-slate-700 dark:bg-slate-800/60">
            <div className="text-slate-400">{'{'}</div>
            {entries.map(([key, value], i) => (
              <StateLine key={`k-${key}`} name={key} value={value} last={i === entries.length - 1} onOpen={() => setShown(true)} />
            ))}
            <div className="text-slate-400">{'}'}</div>
          </div>
        </div>
      ) : null}
      {shown ? (
        <ProcessStepModal title={t('flowEditor.runtimeState')} labelColor="text-cyan-700" onClose={() => setShown(false)}>
          {/* A plain list: each field's name, then its value whole (a
              structured one as indented JSON), nothing to fold. */}
          <dl className="space-y-3">
            {entries.map(([key, value]) => (
              <div key={`m-${key}`}>
                <dt className="font-mono text-xs font-semibold text-cyan-700 dark:text-cyan-400">{key}</dt>
                <dd className={`mt-1 whitespace-pre-wrap break-words text-sm leading-6 ${
                  value !== null && typeof value === 'object' ? 'font-mono text-xs' : ''
                } ${value == null || value === '' ? 'text-slate-400' : 'text-slate-800 dark:text-slate-200'}`}
                >
                  {value !== null && typeof value === 'object'
                    ? JSON.stringify(value, null, 2)
                    : value == null || value === '' ? '""' : String(value)}
                </dd>
              </div>
            ))}
          </dl>
        </ProcessStepModal>
      ) : null}
    </div>
  );
}

export default RuntimeStateBlock;
