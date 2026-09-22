/**
 * The Build view's message: the full trail of a turn, not just what it said.
 */
import { useI18n } from '../../i18n';
import { trimBubbleText } from '../../lib/chatText';
import { renderContent } from './markdown';
import { ExtractionToolCard, RecallToolCard } from './memoryCards';
import { EXTRACTION_TOOLS } from './memoryTools';
import { ARTIFACT_OP_META } from './panels';
import { GraphNodeStep, ReasoningStep } from './reasoning';
import { DelegationCard, TimelineToolCard } from './timeline';
import { Bot, FileText, User } from 'lucide-react';

// The chat bubble's process trail: thoughts, the rich memory cards (recall /
// extraction) and delegated sub-agent runs. `timeline` is the chronological
// feed the Build view and the Process graph use, so walking it keeps every card
// in execution order instead of grouping all thoughts above all tools. Other
// tool calls stay Build-view only. `timeline` is transient (stripped before
// persisting), so a reloaded conversation falls back to the thoughts alone,
// which do persist on `reasoning`.
function ChatTrail({ msg }) {
  const timeline = msg.timeline || [];
  const entries = timeline.length
    ? timeline.filter((e) => (
        e.type === 'reasoning'
        || e.type === 'delegation'
        || (e.type === 'tool' && (EXTRACTION_TOOLS.includes(e.tool) || e.tool === 'recall'))
      ))
    : (msg.reasoning || []).map((s) => ({ type: 'reasoning', ...s }));
  if (!entries.length) return null;
  return (
    <div className="space-y-2 mb-2">
      {entries.map((e, i) => {
        if (e.type === 'reasoning') return <ReasoningStep key={i} step={e} />;
        if (e.type === 'delegation') return <DelegationCard key={e.run_id || i} entry={e} />;
        return e.tool === 'recall'
          ? <RecallToolCard key={i} entry={e} />
          : <ExtractionToolCard key={i} entry={e} />;
      })}
    </div>
  );
}

function TimelineArtifactChip({ entry, onJump }) {
  const meta = ARTIFACT_OP_META[entry.op] || ARTIFACT_OP_META.modify;
  return (
    <button
      type="button"
      onClick={() => onJump?.(entry.path)}
      className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 text-left max-w-full"
      title={`${entry.path} — jump to diff`}
    >
      <span className={`w-4 h-4 rounded flex items-center justify-center text-[9px] font-bold flex-shrink-0 ${meta.cls}`}>{meta.label}</span>
      <FileText className="w-3 h-3 text-gray-400 flex-shrink-0" />
      <span className="text-[11px] font-medium text-gray-700 truncate">{entry.path}</span>
      <span className="text-[10px] font-mono flex-shrink-0">
        {entry.additions > 0 && <span className="text-emerald-600">+{entry.additions}</span>}{' '}
        {entry.deletions > 0 && <span className="text-red-600">−{entry.deletions}</span>}
      </span>
    </button>
  );
}

function BuildMessage({ msg, agentName, onJumpArtifact }) {
  const { t } = useI18n();
  const isUser = msg.role === 'user';
  if (isUser) {
    return (
      <div className="flex gap-3 mb-5 mx-2 flex-row-reverse">
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center text-white">
          <User className="w-4 h-4" />
        </div>
        <div className="max-w-[72%] bg-indigo-600 text-white rounded-2xl rounded-tr-sm px-4 py-3 text-base whitespace-pre-wrap">
          {trimBubbleText(msg.content)}
        </div>
      </div>
    );
  }

  // Agent message: render the chronological timeline. Fall back to plain content
  // for older messages that pre-date timeline capture (e.g. reloaded history).
  const timeline = msg.timeline && msg.timeline.length ? msg.timeline : null;
  return (
    <div className="flex gap-3 mb-5 mx-2">
      <div className="flex flex-col items-center gap-1 flex-shrink-0">
        <div className="w-8 h-8 rounded-full bg-gray-800 flex items-center justify-center text-white">
          <Bot className="w-4 h-4" />
        </div>
        {agentName && (
          <span className="text-[9px] text-gray-400 font-medium text-center leading-tight max-w-[56px] break-words">{agentName}</span>
        )}
      </div>
      <div className={`flex-1 min-w-0 space-y-2 ${msg.error ? 'text-red-700' : ''}`}>
        {timeline ? (
          timeline.map((entry, i) => {
            if (entry.type === 'text') {
              if (!entry.text || !entry.text.trim()) return null;
              return (
                <div key={i} className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm text-base text-gray-800 leading-relaxed">
                  {renderContent(entry.text)}
                </div>
              );
            }
            if (entry.type === 'reasoning') {
              return <ReasoningStep key={i} step={entry} />;
            }
            if (entry.type === 'graph_node') {
              return <GraphNodeStep key={i} entry={entry} />;
            }
            if (entry.type === 'delegation') {
              return <DelegationCard key={i} entry={entry} />;
            }
            if (entry.type === 'tool') {
              if (EXTRACTION_TOOLS.includes(entry.tool)) {
                return <ExtractionToolCard key={i} entry={entry} />;
              }
              if (entry.tool === 'recall') {
                return <RecallToolCard key={i} entry={entry} />;
              }
              return <TimelineToolCard key={i} entry={entry} />;
            }
            if (entry.type === 'artifact') {
              return <TimelineArtifactChip key={i} entry={entry} onJump={onJumpArtifact} />;
            }
            return null;
          })
        ) : (
          <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm text-base text-gray-800 leading-relaxed">
            {msg.content ? renderContent(msg.content) : <span className="text-gray-400 text-xs italic">{t('chat.noOutput')}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

export { ChatTrail, TimelineArtifactChip, BuildMessage };
