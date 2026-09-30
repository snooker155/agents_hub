/**
 * How the agent's thinking reads: one folded step per thought, a node of an
 * imported agent's graph, and the live ticker while it is still thinking.
 */
import { useI18n } from '../../i18n';
import { parseReasoningContent } from './reasoningText';
import { BrainCircuit, ChevronDown, ChevronUp, ListChecks, Workflow } from 'lucide-react';
import { useState } from 'react';

// Full static class strings per accent — Tailwind cannot resolve interpolated
// class names, so each variant must appear literally.
const REASONING_META = {
  think: {
    label: 'Thought',
    Icon: BrainCircuit,
    box: 'bg-violet-50/50 border-violet-100',
    icon: 'text-violet-600',
    title: 'text-violet-700',
  },
  plan: {
    label: 'Plan',
    Icon: ListChecks,
    box: 'bg-indigo-50/50 border-indigo-100',
    icon: 'text-indigo-600',
    title: 'text-indigo-700',
  },
};

// One node of an imported agent's own graph, as it runs.
//
// An imported agent that is internally a graph (a LangGraph flow, say) reports
// `graph_node_start` / `graph_node_end` through its stream. Those are *its*
// nodes, not this hub's flow nodes, so they render as a step inside the one
// bubble rather than opening a bubble each the way flow nodes do: the answer
// still comes from one agent, and splitting it would misrepresent what ran.
function GraphNodeStep({ entry }) {
  const { t } = useI18n();
  const failed = entry.ok === false;
  const dot = entry.running
    ? 'bg-indigo-400 animate-pulse'
    : failed ? 'bg-red-500' : 'bg-emerald-500';
  return (
    <div
      className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 ${
        failed ? 'bg-red-50 border-red-200' : 'bg-indigo-50/60 border-indigo-100'
      }`}
      // Subgraph nodes are indented so a nested graph does not read as a flat
      // list that matches no picture of itself.
      style={entry.depth ? { marginLeft: Math.min(entry.depth, 4) * 12 } : undefined}
    >
      <Workflow className="w-3.5 h-3.5 text-indigo-500 flex-shrink-0" />
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dot}`} />
      <span className="text-xs font-semibold text-indigo-900 truncate">{entry.node}</span>
      {entry.next && (
        <span className="text-[11px] text-gray-400 truncate" title={t('chat.graphNodeNext')}>
          → {entry.next}
        </span>
      )}
      {failed && entry.error && (
        <span className="text-[11px] text-red-600 truncate flex-1">{entry.error}</span>
      )}
    </div>
  );
}

function ReasoningStep({ step, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const meta = REASONING_META[step.kind] || REASONING_META.think;
  const { Icon, label } = meta;
  const content = parseReasoningContent(step.content);
  const preview = content.replace(/\s+/g, ' ').trim();
  return (
    <div className={`rounded-lg border ${meta.box}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-left"
      >
        <Icon className={`w-3.5 h-3.5 ${meta.icon} flex-shrink-0`} />
        <span className={`text-xs font-semibold ${meta.title} flex-shrink-0`}>{label}</span>
        {!open && (
          <span className="text-xs text-gray-400 truncate flex-1">{preview}</span>
        )}
        {open ? (
          <ChevronUp className="w-3 h-3 text-gray-400 ml-auto flex-shrink-0" />
        ) : (
          <ChevronDown className="w-3 h-3 text-gray-400 flex-shrink-0" />
        )}
      </button>
      {open && (
        <div className="px-3 pb-3 -mt-0.5 text-xs text-gray-700 whitespace-pre-wrap leading-relaxed">
          {content}
        </div>
      )}
    </div>
  );
}

// The model's reasoning as it is being written — a three-line ticker under the
// "working" indicator, always showing the tail of the thought. Native reasoning
// only becomes a `think` step once it is complete (the `</think>` closes, or the
// answer starts), which on a long thought leaves the bubble blank for many
// seconds; the `think_delta` stream fills that gap. When the thought completes,
// the caller clears `thinking_live` and the collapsible ReasoningStep takes over.
//
// The tail is shown without JS scrolling: a fixed-height clipped box whose flex
// content is bottom-aligned overflows past its own top edge, so the newest lines
// stay in view.
function LiveThoughts({ text }) {
  if (!text || !text.trim()) return null;
  return (
    <div className="mt-2 flex items-start gap-1.5">
      <BrainCircuit className="w-3.5 h-3.5 text-violet-500 flex-shrink-0 mt-0.5 animate-pulse" />
      <div className="flex-1 min-w-0 h-[3.75rem] overflow-hidden flex flex-col justify-end">
        <div className="text-[11px] leading-5 text-gray-400 whitespace-pre-wrap break-words">
          {text}
        </div>
      </div>
    </div>
  );
}

export { REASONING_META, GraphNodeStep, ReasoningStep, LiveThoughts };
