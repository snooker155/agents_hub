import React, { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, FileText } from 'lucide-react';
import { useI18n } from '../../i18n';
import { useTheme } from '../theme';
import { LogLine } from '../flowEditor/FlowLog';
import { logRows } from '../flowEditor/logRows';
import { StatusBadge, Trajectory } from './loopUi';
import { passEvents } from './passLog';

/** One pass's work log, folded except for the pass that is working. */
function PassLog({ iteration, events, nodeLabels, isDark, defaultOpen }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  const shown = useMemo(() => passEvents(events, iteration), [events, iteration]);
  const rows = useMemo(() => logRows(shown), [shown]);
  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <div className="border-b border-slate-200" data-testid={`loop-iteration-${iteration.iteration}`}>
      <button
        type="button" onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-gray-50"
      >
        <Chevron className="h-3.5 w-3.5 shrink-0 text-gray-400" />
        <span className="text-xs font-semibold text-gray-900">{t('loops.iterationN', { n: iteration.iteration })}</span>
        <StatusBadge status={iteration.status} />
      </button>
      {open && (
        <div className="px-3 pb-3">
          {rows.length === 0 ? (
            <p className="text-xs text-gray-400">
              {events === undefined ? t('common.loading') : t('loops.noLogYet')}
            </p>
          ) : (
            <div className="space-y-0.5">
              {rows.map((log, i) => (
                <LogLine key={`${log.timestamp}-${i}`} log={log} isLast={i === rows.length - 1} isDark={isDark} nodeLabels={nodeLabels} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The right column of the loop page: the run's scores across its passes,
 * pinned on top, and under them what the flow did on each pass, its work
 * log. The chat beside it carries the judgement of each pass
 * (PassEvaluation.jsx).
 */
export default function LoopPassLogs({ runId, iterations, logs, nodeLabels, className = '' }) {
  const { t } = useI18n();
  const { theme } = useTheme();
  const isDark = theme === 'dark' || (theme === 'system' && window.matchMedia?.('(prefers-color-scheme: dark)').matches);
  const last = iterations.length ? iterations[iterations.length - 1].iteration : null;
  return (
    <div className={`flex-col overflow-hidden bg-white ${className}`} data-testid="loop-pass-logs">
      {/* The column's title, in the flow page's column-tab look (PageTabs.jsx). */}
      <div className="flex shrink-0 items-center justify-center gap-1.5 border-b-2 border-indigo-500 px-3 py-3 text-xs font-bold uppercase tracking-wide text-gray-800">
        <FileText className="h-4 w-4 shrink-0 text-indigo-500" />
        <span className="truncate">{t('loops.workLog')}</span>
      </div>
      {/* The scores across the passes, in sight however far the log is
          scrolled: is the run converging or just spending? */}
      {iterations.length > 0 && (
        <div className="shrink-0 border-b border-slate-200 px-3 py-3" data-testid="loop-trajectory">
          <Trajectory iterations={iterations} />
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {iterations.length === 0 ? (
          <p className="px-3 py-4 text-xs italic text-gray-500">{t('loops.noPassLogsYet')}</p>
        ) : iterations.map((it) => (
          <PassLog
            key={`${runId}:${it.iteration}`}
            iteration={it}
            events={logs[it.iteration]}
            nodeLabels={nodeLabels}
            isDark={isDark}
            defaultOpen={it.status === 'running' || it.iteration === last}
          />
        ))}
      </div>
    </div>
  );
}
