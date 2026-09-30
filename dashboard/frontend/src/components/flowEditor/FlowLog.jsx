import { useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, Circle, Loader2, XCircle } from 'lucide-react';
import { useI18n } from '../../i18n';
import { useTheme } from '../theme';
import { DOMAIN_COLORS } from './graphHelpers';
import { fmtTime } from './runFormat';
import { RuntimeStateBlock } from './RuntimeState';

function logMeta(log) {
  const t = log.type || '';
  if (t === 'flow_start')   return { symbol: '◆', color: '#22d3ee', label: 'Flow started' };
  if (t === 'flow_finish')  return { symbol: '◆', color: '#34d399', label: 'Flow finished' };
  if (t === 'flow_stopped') return { symbol: '◆', color: '#f87171', label: 'Flow stopped' };
  if (t === 'agent_start')  return { symbol: '⟳', color: '#fbbf24', label: log.agent_name || 'Agent', spinning: true };
  if (t === 'agent_finish') return { symbol: '✓', color: '#34d399', label: log.agent_name || 'Agent' };
  if (t === 'agent_error')  return { symbol: '✗', color: '#f87171', label: log.agent_name || 'Agent' };
  if (t === 'agent_stopped') return { symbol: '■', color: '#fb923c', label: log.agent_name || 'Agent' };
  if (t === 'node_skip')    return { symbol: '⊘', color: '#64748b', label: log.agent_name || 'Node' };
  return { symbol: '·', color: '#64748b', label: log.agent_name || t };
}

function TerminalBlock({ label, text, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  if (!text) return null;
  return (
    <div className="mt-1.5 ml-[72px]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 font-mono text-[10px] text-slate-400 hover:text-slate-700 dark:text-slate-500 dark:hover:text-slate-300 transition-colors"
      >
        <span>{open ? '▾' : '▸'}</span>
        <span>{label}</span>
      </button>
      {open && (
        <pre className="mt-1 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-200 bg-slate-100 p-3 font-mono text-[10px] leading-[1.6] text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">
          {text}
        </pre>
      )}
    </div>
  );
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

function LogLine({ log, isLast, isDark }) {
  const { t } = useI18n();
  const meta = logMeta(log);
  const palette = isDark ? DOMAIN_COLORS_DARK : DOMAIN_COLORS_LIGHT;
  const nameColor = palette[log.tag] || meta.color;
  const symbolColor = meta.color;

  return (
    <div className="group">
      <div className="flex items-baseline gap-0 font-mono text-[11px] leading-6">
        <span className="w-[52px] shrink-0 text-[10px] text-slate-400 dark:text-slate-500">{fmtTime(log.timestamp)}</span>
        <span className="relative flex w-5 shrink-0 flex-col items-center self-stretch">
          {!isLast && <span className="absolute top-5 bottom-0 left-1/2 w-px -translate-x-1/2 bg-slate-200 dark:bg-slate-700" />}
          <span
            className={`relative z-10 mt-1.5 text-[13px] leading-none${meta.spinning ? ' animate-spin' : ''}`}
            style={{ color: symbolColor }}
          >
            {meta.symbol}
          </span>
        </span>
        <span className="w-[80px] shrink-0 truncate pl-2 font-semibold" style={{ color: nameColor }}>
          {meta.label}
        </span>
        <span className="min-w-0 flex-1 pl-2 break-words text-slate-700 dark:text-slate-300">{log.content}</span>
      </div>
      <TerminalBlock label={t('flowEditor.input')}  text={log.input}  defaultOpen={false} />
      <TerminalBlock label={t('flowEditor.output')} text={log.output} defaultOpen={log.type === 'agent_finish'} />
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
      label: 'text-slate-800 dark:text-slate-100', badge: 'text-emerald-600 dark:text-emerald-400', word: 'Done',
    },
    running: {
      Icon: Loader2, iconClass: 'text-amber-500 animate-spin',
      row: 'border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-900/30',
      label: 'text-amber-900 dark:text-amber-100', badge: 'text-amber-600 dark:text-amber-400', word: 'Processing',
    },
    error: {
      Icon: XCircle, iconClass: 'text-rose-500',
      row: 'border-rose-200 bg-rose-50 dark:border-rose-900/50 dark:bg-rose-900/20',
      label: 'text-rose-800 dark:text-rose-200', badge: 'text-rose-600 dark:text-rose-400', word: 'Error',
    },
    pending: {
      Icon: Circle, iconClass: 'text-slate-300 dark:text-slate-600',
      row: 'border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800/60',
      label: 'text-slate-400 dark:text-slate-500', badge: 'text-slate-400 dark:text-slate-500', word: 'Pending',
    },
    ready: {
      Icon: Circle, iconClass: 'text-cyan-400 dark:text-cyan-500',
      row: 'border-cyan-200 bg-cyan-50/50 dark:border-cyan-900/50 dark:bg-cyan-900/10',
      label: 'text-slate-600 dark:text-slate-300', badge: 'text-cyan-600 dark:text-cyan-400', word: 'Ready',
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
          return (
            <div
              key={node.id}
              className={`flex items-center gap-2.5 rounded-xl border px-3 py-2 ${s.row}`}
            >
              <span className="w-4 shrink-0 text-center text-[11px] font-mono text-slate-400 dark:text-slate-500">{i + 1}</span>
              <Icon className={`h-4 w-4 shrink-0 ${s.iconClass}`} />
              <span className={`min-w-0 flex-1 truncate text-sm font-semibold ${s.label}`}>{node.label}</span>
              <span className={`shrink-0 text-[11px] font-medium ${s.badge}`}>{s.word}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function FlowLog({ logs, stateLogs, filterLabel, running, nodeStatuses }) {
  const { t } = useI18n();
  const bottomRef = useRef(null);
  const didInitialScrollRef = useRef(false);
  const { theme } = useTheme();
  const isDark = theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);

  // `flow_state` events only carry a state snapshot for the runtime-state block;
  // they have no log content, so keep them out of the printed log stream.
  const visible = useMemo(() => logs.filter((l) => l.type !== 'flow_state'), [logs]);

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
    <div className="flex h-full flex-col bg-slate-50 dark:bg-slate-900">
      <FlowAgentStatusBar nodeStatuses={nodeStatuses} />
      <RuntimeStateBlock logs={stateLogs || logs} />
      {filterLabel && (
        <div className="border-b border-slate-200 px-3 py-1.5 font-mono text-[10px] text-slate-400 dark:border-slate-700 dark:text-slate-500">
          # filtered · {filterLabel}
        </div>
      )}
      <div className="flex-1 overflow-y-auto px-3 py-3">
        {visible.length === 0 ? (
          <span className="font-mono text-[11px] text-slate-400 dark:text-slate-500">{t('flowEditor.waitingForExecution')}</span>
        ) : (
          <div className="space-y-0.5">
            {visible.map((log, i) => (
              <LogLine key={`${log.timestamp}-${i}`} log={log} isLast={i === visible.length - 1} isDark={isDark} />
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

export default FlowLog;
