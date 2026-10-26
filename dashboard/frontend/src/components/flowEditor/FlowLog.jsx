import { useEffect, useMemo, useRef } from 'react';
import { CheckCircle2, Circle, CircleSlash, Loader2, Square, XCircle } from 'lucide-react';
import { useI18n } from '../../i18n';
import { useTheme } from '../theme';
import { DOMAIN_COLORS } from './graphHelpers';
import { fmtTime } from './runFormat';
import { RuntimeStateBlock } from './RuntimeState';
import ProcessNode from '../ProcessNode';
import StepText from '../StepText';
import NodeSteps from './NodeSteps.jsx';
import { FileLinks } from '../chat/messageParts';
import { useRunTrail } from '../chat/runTrail';
import { logRows } from './logRows';
import { nodeStatusText } from './nodeStatus';
import { logText } from './logText';

// The mark and colour of a line; its words come from logText.js.
function logMeta(log) {
  const t = log.type || '';
  if (t === 'flow_start')   return { symbol: '◆', color: '#22d3ee' };
  if (t === 'flow_finish')  return { symbol: '◆', color: '#34d399' };
  if (t === 'flow_stopped') return { symbol: '◆', color: '#f87171' };
  if (t === 'agent_start')  return { symbol: '⟳', color: '#fbbf24', spinning: true };
  if (t === 'agent_finish') return { symbol: '✓', color: '#34d399' };
  if (t === 'agent_error')  return { symbol: '✗', color: '#f87171' };
  if (t === 'agent_stopped') return { symbol: '■', color: '#fb923c' };
  if (t === 'node_skip')    return { symbol: '⊘', color: '#64748b' };
  if (t === 'flow_summary') return { symbol: '✎', color: '#a78bfa' };
  return { symbol: '·', color: '#64748b' };
}

// A node's input or output: the Chat process panel's step card, three lines
// of it, the whole text in a modal on a click.
const IO_TONES = {
  input: { labelColor: 'text-slate-500', borderColor: 'border-slate-200', bgColor: 'bg-white' },
  output: { labelColor: 'text-emerald-700', borderColor: 'border-emerald-100', bgColor: 'bg-emerald-50/40' },
  error: { labelColor: 'text-rose-700', borderColor: 'border-rose-200', bgColor: 'bg-rose-50/40' },
};

function IoBlock({ label, text, tone, footer = null }) {
  if (!text && !footer) return null;
  const s = IO_TONES[tone] || IO_TONES.input;
  return (
    <div className="mt-1.5 ml-5">
      <ProcessNode
        label={label} lines={3} labelColor={s.labelColor} borderColor={s.borderColor} bgColor={s.bgColor}
        footer={footer}
      >
        {text ? <StepText text={String(text)} /> : null}
      </ProcessNode>
    </div>
  );
}

// The files a node changed, shown in its output as links to their pages, and
// what it was given. A line saved with them (`files`, `input`) or a live one
// (its turn) has them at hand; a line saved before nodes kept them (an older
// run, a task run's node, a loop's pass) takes them from the node's run.
function useNodeRecord(log) {
  const ended = Boolean(log.node_id) && log.type !== 'agent_start';
  const known = log.files || log.turn?.files;
  const trail = useRunTrail(log.run_id, Boolean(ended && log.run_id && (!known || !log.input)));
  return {
    files: ended ? (known || trail?.files || []) : [],
    input: log.input || (ended ? trail?.input : '') || '',
  };
}

const DOMAIN_COLORS_DARK = DOMAIN_COLORS;
const DOMAIN_COLORS_LIGHT = {
  management: '#0891b2',
  analysis: '#d97706',
  design: '#7c3aed',
  development: '#059669',
  testing: '#e11d48',
  operations: '#ea580c',
  flow: '#475569',
  general: '#475569',
};

export function LogLine({ log, isLast, isDark, nodeLabels }) {
  const { t } = useI18n();
  const meta = logMeta(log);
  const words = logText(t, log, nodeLabels);
  const { files, input } = useNodeRecord(log);
  const palette = isDark ? DOMAIN_COLORS_DARK : DOMAIN_COLORS_LIGHT;
  const nameColor = palette[log.tag] || meta.color;
  const symbolColor = meta.color;

  return (
    <div className="group">
      {/* Mark, name, what happened, and the time at the right edge, so the
          blocks under the line start right past the mark. */}
      <div className="flex items-baseline gap-0 font-mono text-[11px] leading-6">
        <span className="relative flex w-5 shrink-0 flex-col items-center self-stretch">
          {!isLast && <span className="absolute top-5 bottom-0 left-1/2 w-px -translate-x-1/2 bg-slate-200 dark:bg-slate-700" />}
          <span
            className={`relative z-10 mt-1.5 text-[13px] leading-none${meta.spinning ? ' animate-spin' : ''}`}
            style={{ color: symbolColor }}
          >
            {meta.symbol}
          </span>
        </span>
        <span className="w-[80px] shrink-0 truncate pl-2 font-semibold" style={{ color: nameColor }} title={words.label}>
          {words.label}
        </span>
        <span className="min-w-0 flex-1 pl-2 break-words text-slate-700 dark:text-slate-300">{words.content}</span>
        <span className="shrink-0 pl-2 text-[10px] text-slate-400 dark:text-slate-500">{fmtTime(log.timestamp)}</span>
      </div>
      <IoBlock label={t('flowEditor.input')} text={input} tone="input" />
      {/* What the node's agent did between its input and its output. */}
      {(log.turn || (log.run_id && log.type !== 'agent_start')) ? <NodeSteps log={log} /> : null}
      <IoBlock
        label={t('flowEditor.output')}
        text={words.output}
        footer={files.length ? <FileLinks files={files} /> : null}
        tone={log.type === 'agent_finish' || log.type === 'flow_summary' ? 'output'
          : log.type === 'agent_error' || log.type === 'flow_error' ? 'error' : 'input'}
      />
    </div>
  );
}

function FlowAgentStatusBar({ nodeStatuses = [] }) {
  const { t } = useI18n();
  if (!nodeStatuses.length) return null;
  const STYLES = {
    done: {
      Icon: CheckCircle2, iconClass: 'text-emerald-500',
      row: 'border-emerald-200 bg-emerald-50 dark:border-emerald-900/50 dark:bg-emerald-900/20',
      label: 'text-slate-800 dark:text-slate-100', badge: 'text-emerald-600 dark:text-emerald-400',
    },
    running: {
      Icon: Loader2, iconClass: 'text-amber-500 animate-spin',
      row: 'border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-900/30',
      label: 'text-amber-900 dark:text-amber-100', badge: 'text-amber-600 dark:text-amber-400',
    },
    error: {
      Icon: XCircle, iconClass: 'text-rose-500',
      row: 'border-rose-200 bg-rose-50 dark:border-rose-900/50 dark:bg-rose-900/20',
      label: 'text-rose-800 dark:text-rose-200', badge: 'text-rose-600 dark:text-rose-400',
    },
    pending: {
      Icon: Circle, iconClass: 'text-slate-300 dark:text-slate-600',
      row: 'border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800/60',
      label: 'text-slate-400 dark:text-slate-500', badge: 'text-slate-400 dark:text-slate-500',
    },
    stopped: {
      Icon: Square, iconClass: 'text-orange-500',
      row: 'border-orange-200 bg-orange-50 dark:border-orange-900/50 dark:bg-orange-900/20',
      label: 'text-orange-800 dark:text-orange-200', badge: 'text-orange-600 dark:text-orange-400',
    },
    skipped: {
      Icon: CircleSlash, iconClass: 'text-slate-400 dark:text-slate-500',
      row: 'border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/40',
      label: 'text-slate-500 dark:text-slate-400', badge: 'text-slate-500 dark:text-slate-400',
    },
    ready: {
      Icon: Circle, iconClass: 'text-cyan-400 dark:text-cyan-500',
      row: 'border-cyan-200 bg-cyan-50/50 dark:border-cyan-900/50 dark:bg-cyan-900/10',
      label: 'text-slate-600 dark:text-slate-300', badge: 'text-cyan-600 dark:text-cyan-400',
    },
  };
  return (
    <div className="max-h-[45%] shrink-0 overflow-y-auto border-b border-slate-200 p-2 dark:border-slate-700">
      <div className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
        {t('flowEditor.agents')}
      </div>
      <div className="space-y-1">
        {nodeStatuses.map((node, i) => {
          const s = STYLES[node.status] || STYLES.pending;
          const Icon = s.Icon;
          const text = nodeStatusText(t, node);
          return (
            <div
              key={node.id}
              className={`flex items-center gap-2.5 rounded-xl border px-3 py-2 ${s.row}`}
            >
              <span className="w-4 shrink-0 text-center text-[11px] font-mono text-slate-400 dark:text-slate-500">{i + 1}</span>
              <Icon className={`h-4 w-4 shrink-0 ${s.iconClass}`} />
              <span className={`min-w-0 flex-1 truncate text-sm font-semibold ${s.label}`}>{node.label}</span>
              <span className={`shrink-0 text-[11px] font-medium ${s.badge}`} title={text.title}>{text.badge}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// `selection`: what is picked on the page and so shown in the log (a History
// record, a node), as one plain line over it. `initialState`: the flow's own
// starting state, in the state block until a run has a snapshot.
export function FlowLog({ logs, stateLogs, initialState = null, selection = '', running, nodeStatuses }) {
  const { t } = useI18n();
  const bottomRef = useRef(null);
  const didInitialScrollRef = useRef(false);
  const { theme } = useTheme();
  const isDark = theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);

  // One row per agent node, which turns from running into its outcome
  // (logRows.js); `flow_state` snapshots only feed the runtime-state block.
  const visible = useMemo(() => logRows(logs), [logs]);
  // A saved line that names only its node gets the node's name.
  const nodeLabels = useMemo(
    () => Object.fromEntries((nodeStatuses || []).map((n) => [n.id, n.label])),
    [nodeStatuses],
  );

  // On mount (or when logs first appear), jump straight to the end with no animation.
  useEffect(() => {
    if (didInitialScrollRef.current) return;
    if (logs.length === 0) return;
    bottomRef.current?.scrollIntoView({ behavior: 'instant', block: 'end' });
    didInitialScrollRef.current = true;
  }, [logs.length]);

  // While the flow is executing, follow new log lines smoothly. When it's not
  // running we leave the scroll position alone so users can read past output.
  useEffect(() => {
    if (!running) return;
    if (!didInitialScrollRef.current) return;
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [logs.length, running]);

  return (
    <div className="flex h-full flex-col bg-white">
      <FlowAgentStatusBar nodeStatuses={nodeStatuses} />
      <RuntimeStateBlock logs={stateLogs || logs} initial={initialState} />
      {selection ? (
        <div className="truncate border-b border-slate-200 px-3 py-1.5 text-[11px] text-slate-500 dark:border-slate-700 dark:text-slate-400" title={selection} data-testid="flow-log-selection">
          {selection}
        </div>
      ) : null}
      <div className="flex-1 overflow-y-auto px-3 py-3">
        {visible.length === 0 ? (
          <p className="text-xs leading-5 text-slate-400 dark:text-slate-500">{t('flowEditor.waitingForExecution')}</p>
        ) : (
          <div className="space-y-0.5">
            {visible.map((log, i) => (
              <LogLine key={`${log.timestamp}-${i}`} log={log} isLast={i === visible.length - 1} isDark={isDark} nodeLabels={nodeLabels} />
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

export default FlowLog;
