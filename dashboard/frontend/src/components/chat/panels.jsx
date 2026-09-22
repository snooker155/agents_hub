/**
 * The side panel's two halves: what the agent's process looked like, and the
 * files it changed, with the diff for each.
 */
import GraphMirror from '../GraphMirror';
import ProcessGraph, { TokenPill } from '../ProcessGraph';
import { useI18n } from '../../i18n';
import { ChevronDown, ChevronUp, Workflow } from 'lucide-react';
import { useMemo, useState } from 'react';

function ProcessPanelContent({ processInsights, topology = null, graphRun = null }) {
  const { t } = useI18n();
  const graphKey = (processInsights?.message_runs || [])
    .map((mr, idx) => `${mr?.run_id || mr?.message_id || idx}`)
    .join('|');
  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-3">
      <div className="flex flex-wrap gap-1">
        <TokenPill label={t('chat.sessionIn')} value={processInsights?.token_usage?.inbound_tokens || 0} />
        <TokenPill label={t('chat.sessionOut')} value={processInsights?.token_usage?.outbound_tokens || 0} />
        <TokenPill label={t('chat.sessionTotal')} value={processInsights?.token_usage?.total_tokens || 0} />
        {/* The bill above sums every LLM call of every turn — an agent loop
            resends the conversation each step. This is the largest single
            prompt the session sent, the same figure the context meter tracks. */}
        {(processInsights?.context_peak || 0) > 0 && (
          <TokenPill
            label={t('chat.sessionPeak')}
            value={
              processInsights.context_window_tokens > 0
                ? `${processInsights.context_peak} / ${processInsights.context_window_tokens}`
                : processInsights.context_peak
            }
            title={t('chat.sessionPeakTooltip')}
          />
        )}
      </div>
      {/* An imported agent that is a graph inside: its own shape, with the node
          it is in right now lit and the ones it has been through marked. The
          hub does not run this graph, so there is nothing to click — the value
          is seeing which branch a live run took. */}
      {topology?.nodes?.length > 0 && (
        <div className="border border-gray-100 rounded-lg p-3">
          <div className="flex items-center gap-1.5 mb-2">
            <Workflow className="w-3.5 h-3.5 text-indigo-500" />
            <span className="text-xs font-semibold text-gray-700">{t('chat.agentGraph')}</span>
            {graphRun?.active && (
              <span className="text-[11px] text-indigo-600 font-medium truncate">{graphRun.active}</span>
            )}
          </div>
          <GraphMirror
            topology={topology}
            activeNode={graphRun?.active || null}
            visitedNodes={graphRun?.visited || []}
          />
        </div>
      )}
      <ProcessGraph key={graphKey} messageRuns={processInsights.message_runs || []} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Build view — unified diff renderer (no external deps)
// ---------------------------------------------------------------------------
function DiffView({ diff }) {
  const { t } = useI18n();
  if (!diff) {
    return <div className="px-3 py-2 text-[11px] text-gray-400 italic">{t('chat.noTextualDiffAvailable')}</div>;
  }
  const lines = diff.split('\n');
  return (
    <div className="font-mono text-[11px] leading-relaxed overflow-x-auto">
      {lines.map((line, i) => {
        let cls = 'text-gray-600';
        let bg = '';
        if (line.startsWith('+++') || line.startsWith('---')) {
          cls = 'text-gray-400';
        } else if (line.startsWith('@@')) {
          cls = 'text-indigo-500';
          bg = 'bg-indigo-50/60';
        } else if (line.startsWith('+')) {
          cls = 'text-emerald-700';
          bg = 'bg-emerald-50';
        } else if (line.startsWith('-')) {
          cls = 'text-red-700';
          bg = 'bg-red-50';
        }
        return (
          <div key={i} className={`px-3 whitespace-pre ${bg} ${cls}`}>
            {line || ' '}
          </div>
        );
      })}
    </div>
  );
}

const ARTIFACT_OP_META = {
  add: { label: 'A', cls: 'bg-emerald-100 text-emerald-700' },
  modify: { label: 'M', cls: 'bg-amber-100 text-amber-700' },
  delete: { label: 'D', cls: 'bg-red-100 text-red-700' },
};

function ArtifactItem({ artifact, defaultOpen = false }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  const meta = ARTIFACT_OP_META[artifact.op] || ARTIFACT_OP_META.modify;
  return (
    <div data-artifact-path={artifact.path} className="rounded-lg border border-gray-200 bg-white overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-gray-50"
      >
        <span className={`w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold flex-shrink-0 ${meta.cls}`}>
          {meta.label}
        </span>
        <span className="flex-1 min-w-0 text-xs font-medium text-gray-700 truncate" title={artifact.path}>
          {artifact.path}
        </span>
        <span className="flex items-center gap-1.5 flex-shrink-0 text-[10px] font-mono">
          {artifact.additions > 0 && <span className="text-emerald-600">+{artifact.additions}</span>}
          {artifact.deletions > 0 && <span className="text-red-600">−{artifact.deletions}</span>}
        </span>
        {open ? <ChevronUp className="w-3 h-3 text-gray-400" /> : <ChevronDown className="w-3 h-3 text-gray-400" />}
      </button>
      {open && (
        <div className="border-t border-gray-100 py-2 max-h-[400px] overflow-y-auto">
          {artifact.binary ? (
            <div className="px-3 py-2 text-[11px] text-gray-400 italic">
              {artifact.truncated ? t('chat.fileTooLarge') : t('chat.binaryFile')}
            </div>
          ) : (
            <DiffView diff={artifact.diff} />
          )}
        </div>
      )}
    </div>
  );
}

function ArtifactsPanel({ artifacts }) {
  const { t } = useI18n();
  const items = useMemo(
    () => Object.values(artifacts || {}).sort((a, b) => (a.path || '').localeCompare(b.path || '')),
    [artifacts],
  );
  const totals = useMemo(() => {
    let add = 0, del = 0;
    for (const a of items) { add += a.additions || 0; del += a.deletions || 0; }
    return { add, del };
  }, [items]);

  if (!items.length) {
    return (
      <div className="flex-1 overflow-y-auto p-4">
        <p className="text-xs text-gray-500 italic">
          {t('chat.noFilesChangedYetWhen')}
        </p>
      </div>
    );
  }
  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-2">
      <div className="flex items-center gap-2 pb-1 text-[11px] text-gray-500">
        <span className="font-semibold text-gray-600">{t('chat.fileCount', { count: items.length })}</span>
        <span className="font-mono text-emerald-600">+{totals.add}</span>
        <span className="font-mono text-red-600">−{totals.del}</span>
      </div>
      {items.map((a) => (
        <ArtifactItem key={a.path} artifact={a} defaultOpen={items.length <= 2} />
      ))}
    </div>
  );
}

export { ProcessPanelContent, DiffView, ARTIFACT_OP_META, ArtifactItem, ArtifactsPanel };
