/**
 * What a flow node's agent did on the way to its output, inside the node's
 * line in the run log: its thoughts, tool calls, text between steps and
 * delegated runs, drawn with the Chat page's Build view cards. The files it
 * changed are not steps here: they sit under the line as links (NodeFiles). Folded by
 * default, with the counts on the header so a run can be skimmed for the node
 * that called twenty tools or failed three of them without opening any.
 *
 * Live, the steps come from the stream (useFlowRuns folds them into the log
 * line as `turn`); for a finished node they are fetched from its run once the
 * block is opened (chat/runTrail.js), the counts riding on the log line.
 */
import { useState } from 'react';
import { AlertTriangle, BrainCircuit, ChevronDown, ChevronRight, Loader2, Wrench } from 'lucide-react';
import { useI18n } from '../../i18n';
import { ReasoningStep, GraphNodeStep, LiveThoughts } from '../chat/reasoning';
import { DelegationCard, TimelineToolCard } from '../chat/timeline';
import { ExtractionToolCard, RecallToolCard } from '../chat/memoryCards';
import { EXTRACTION_TOOLS } from '../chat/memoryTools';
import { foldDelegationTools } from '../chat/trail';
import { useRunTrail } from '../chat/runTrail';
import { trailStats } from './stepStats';

function Step({ entry }) {
  switch (entry.type) {
    case 'reasoning': return <ReasoningStep step={entry} />;
    case 'graph_node': return <GraphNodeStep entry={entry} />;
    case 'delegation': return <DelegationCard entry={entry} defaultOpen={false} />;
    case 'tool':
      if (EXTRACTION_TOOLS.includes(entry.tool)) return <ExtractionToolCard entry={entry} />;
      if (entry.tool === 'recall') return <RecallToolCard entry={entry} />;
      return <TimelineToolCard entry={entry} />;
    case 'text':
      return (entry.text || '').trim()
        ? <div className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-[11px] leading-4 text-gray-700 whitespace-pre-wrap break-words max-h-40 overflow-y-auto">{entry.text}</div>
        : null;
    default: return null;
  }
}

function Count({ icon: Icon, n, className, title }) {
  if (!n) return null;
  return (
    <span className={`inline-flex items-center gap-0.5 ${className}`} title={title}>
      <Icon className="h-3 w-3" />{n}
    </span>
  );
}

export default function NodeSteps({ log }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const live = log.turn || null;
  const running = log.type === 'agent_start';
  // A finished node's steps are fetched when opened, and its counts too when
  // the log line was written without them (a task run's node, an older run).
  const trail = useRunTrail(log.run_id, Boolean(!live && log.run_id && (open || !log.stats)));
  if (!live && !log.run_id) return null;

  const timeline = live ? live.timeline : trail?.timeline;
  const stats = live ? trailStats(live.timeline) : (log.stats || trail?.stats || null);
  const empty = stats && !stats.tools && !stats.thoughts;
  // A node that answered straight away has nothing to fold.
  if (!running && empty && (!timeline || !timeline.some((e) => e.type !== 'text' && e.type !== 'artifact'))) return null;

  // The node's answer is its Output below; the steps stop before it.
  let steps = foldDelegationTools((timeline || []).filter((e) => e.type !== 'artifact'));
  if (!running && steps[steps.length - 1]?.type === 'text') steps = steps.slice(0, -1);

  return (
    <div className="mt-1.5 ml-[72px]" data-testid="node-steps">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 rounded-md px-1 py-0.5 text-left text-[10px] text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
      >
        {open ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
        <span className="font-semibold uppercase tracking-wide">{t('flowEditor.steps.title')}</span>
        {running && <Loader2 className="h-3 w-3 shrink-0 animate-spin text-amber-500" />}
        <span className="ml-auto flex items-center gap-2 font-mono">
          {stats ? (
            empty ? <span>{t('flowEditor.steps.none')}</span> : (
              <>
                <Count icon={Wrench} n={stats.tools} className="text-amber-600" title={t('flowEditor.steps.tools', { count: stats.tools })} />
                <Count icon={AlertTriangle} n={stats.errors} className="text-rose-600" title={t('flowEditor.steps.errors', { count: stats.errors })} />
                <Count icon={BrainCircuit} n={stats.thoughts} className="text-violet-600" title={t('flowEditor.steps.thoughts', { count: stats.thoughts })} />
              </>
            )
          ) : <span>…</span>}
        </span>
      </button>
      {open && (
        <div className="mt-1 space-y-1.5" data-testid="node-steps-body">
          {timeline == null ? (
            <div className="text-[11px] text-slate-400">{t('common.loading')}</div>
          ) : steps.length === 0 && !live?.thinking_live ? (
            <div className="text-[11px] text-slate-400">{t('flowEditor.steps.none')}</div>
          ) : (
            steps.map((entry, i) => <Step key={entry.run_id || i} entry={entry} />)
          )}
          {running && live?.thinking_live ? <LiveThoughts text={live.thinking_live} /> : null}
        </div>
      )}
    </div>
  );
}
