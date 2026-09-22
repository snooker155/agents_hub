/**
 * Build-view timeline cards: an ordinary tool call, and a delegated run with
 * its own trail inside it.
 */
import { SKILL_TOOL, shortText } from '../processUtils';
import { useI18n } from '../../i18n';
import { ExtractionToolCard, RecallToolCard } from './memoryCards';
import { EXTRACTION_TOOLS } from './memoryTools';
import { GraphNodeStep, ReasoningStep } from './reasoning';
import { ChevronDown, ChevronUp, Repeat, Terminal, Zap } from 'lucide-react';
import { useState } from 'react';

// ---------------------------------------------------------------------------
// Build view — full inline transcript (messages + thinking/plan + tools + artifacts)
// ---------------------------------------------------------------------------
function TimelineToolCard({ entry }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const isSkill = entry.tool === SKILL_TOOL;
  return (
    <div className={`rounded-lg border ${isSkill ? 'border-violet-200 bg-violet-50/50' : 'border-amber-200 bg-amber-50/50'}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-left"
      >
        {isSkill ? <Zap className="w-3.5 h-3.5 text-violet-500 flex-shrink-0" /> : <Terminal className="w-3.5 h-3.5 text-amber-600 flex-shrink-0" />}
        <span className={`text-xs font-semibold ${isSkill ? 'text-violet-700' : 'text-amber-700'}`}>{entry.tool || 'tool'}</span>
        {entry.running && (
          <span className="flex gap-1 ml-1">
            {[0, 150, 300].map((d) => (
              <span key={d} className="w-1 h-1 bg-amber-400 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }} />
            ))}
          </span>
        )}
        {!open && entry.input && (
          <span className="text-[11px] text-gray-400 truncate flex-1">{shortText(entry.input, 80)}</span>
        )}
        {open ? <ChevronUp className="w-3 h-3 text-gray-400 ml-auto flex-shrink-0" /> : <ChevronDown className="w-3 h-3 text-gray-400 ml-auto flex-shrink-0" />}
      </button>
      {open && (
        <div className="px-3 pb-2.5 space-y-1.5">
          {entry.input && (
            <div className="text-[11px] text-gray-700 whitespace-pre-wrap break-all">
              <span className="text-gray-400">{t('chat.in2')}</span> {shortText(entry.input, 1000)}
            </div>
          )}
          {entry.output != null && (
            <div className="text-[11px] text-emerald-800 whitespace-pre-wrap break-all">
              <span className="text-emerald-600">{t('chat.out2')}</span> {shortText(entry.output, 1000)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// A live, collapsible block for one delegated agent run: header (agent + status)
// over its nested thoughts and tool calls, the same cards the parent uses.
// Recursive: a nested `delegation` entry renders another DelegationCard.
function DelegationCard({ entry }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  const nested = entry.timeline || [];
  const running = entry.running;
  const failed = entry.ok === false && !running;
  return (
    <div className="rounded-lg border border-indigo-200 bg-indigo-50/40">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-left"
      >
        <Repeat className="w-3.5 h-3.5 text-indigo-500 flex-shrink-0" />
        <span className="text-xs font-semibold text-indigo-700 flex-shrink-0">
          Delegated → {entry.agent_name || entry.agent_id}
        </span>
        {running ? (
          <span className="flex gap-1 ml-1">
            {[0, 150, 300].map((d) => (
              <span key={d} className="w-1 h-1 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }} />
            ))}
          </span>
        ) : (
          <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ml-1 ${failed ? 'bg-red-100 text-red-700' : 'bg-emerald-100 text-emerald-700'}`}>
            {failed ? 'failed' : 'done'}
          </span>
        )}
        {!open && entry.input && (
          <span className="text-[11px] text-gray-400 truncate flex-1">{shortText(entry.input, 60)}</span>
        )}
        {open ? <ChevronUp className="w-3 h-3 text-gray-400 ml-auto flex-shrink-0" /> : <ChevronDown className="w-3 h-3 text-gray-400 ml-auto flex-shrink-0" />}
      </button>
      {open && (
        <div className="px-3 pb-2.5 pt-0.5 ml-2 border-l-2 border-indigo-100 space-y-1.5">
          {nested.length === 0 ? (
            <div className="text-[11px] text-gray-400 italic">{t('chat.working')}</div>
          ) : (
            nested.map((e, i) => {
              if (e.type === 'reasoning') return <ReasoningStep key={i} step={e} />;
              if (e.type === 'graph_node') return <GraphNodeStep key={i} entry={e} />;
              if (e.type === 'delegation') return <DelegationCard key={i} entry={e} />;
              if (e.type === 'tool') {
                if (EXTRACTION_TOOLS.includes(e.tool)) return <ExtractionToolCard key={i} entry={e} />;
                if (e.tool === 'recall') return <RecallToolCard key={i} entry={e} />;
                return <TimelineToolCard key={i} entry={e} />;
              }
              if (e.type === 'text') {
                return e.text && e.text.trim()
                  ? <div key={i} className="text-[11px] text-gray-700 whitespace-pre-wrap break-words">{e.text}</div>
                  : null;
              }
              return null;
            })
          )}
        </div>
      )}
    </div>
  );
}

export { TimelineToolCard, DelegationCard };
