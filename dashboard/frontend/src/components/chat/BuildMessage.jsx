/**
 * The Build view's message: the full trail of a turn, not just what it said.
 */
import { useContext, useMemo } from 'react';
import { useI18n } from '../../i18n';
import { trimBubbleText } from '../../lib/chatText';
import { renderContent } from './markdown';
import { ExtractionToolCard, RecallToolCard } from './memoryCards';
import { EXTRACTION_TOOLS } from './memoryTools';
import { MessageViews } from './messageParts';
import { messageViews } from './turnViews';
import { ARTIFACT_OP_META } from './panels';
import { GraphNodeStep, ReasoningStep } from './reasoning';
import { DelegationCard, TimelineToolCard } from './timeline';
import { foldDelegationTools, withFinalText } from './trail';
import { ChatPageContext } from './context';
import { ChatCodeActionsContext } from './chatMarkdownContext';
import { Bot, FileText, User } from 'lucide-react';

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

/**
 * A system notice, such as the compaction marker the backend emits when older
 * history is folded into a summary: one quiet centred line between messages,
 * never a bubble with an avatar. Shared by both chat views so the notice
 * looks the same whichever one is open.
 */
function SystemNotice({ msg }) {
  return (
    <div className="flex justify-center mb-6 mx-2">
      <span className="text-xs text-gray-400 italic text-center px-3">{msg.content}</span>
    </div>
  );
}

function BuildMessage({ msg, agentName, onJumpArtifact, isStreaming = false }) {
  const { t } = useI18n();
  const openInCodePanel = useContext(ChatPageContext)?.openInCodePanel;
  const codeActions = useMemo(() => (
    openInCodePanel && msg.run_id
      ? { open: (code, language) => openInCodePanel({ code, language, runId: msg.run_id }) }
      : null
  ), [openInCodePanel, msg.run_id]);
  if (msg.role === 'system') return <SystemNotice msg={msg} />;
  const isUser = msg.role === 'user';
  if (isUser) {
    return (
      <div className="flex gap-3 mb-6 mx-2 flex-row-reverse">
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center text-white">
          <User className="w-4 h-4" />
        </div>
        <div data-prompt-bubble className="chat-prompt-bubble max-w-[72%] bg-indigo-600 text-white rounded-2xl rounded-tr-sm px-4 py-3 text-base leading-relaxed whitespace-pre-wrap">
          {trimBubbleText(msg.content)}
        </div>
      </div>
    );
  }

  // Agent message: render the chronological timeline. Fall back to plain content
  // for older messages that pre-date timeline capture (e.g. reloaded history).
  // Once the turn is over its last text is the reply it settled on.
  const raw = msg.timeline && msg.timeline.length ? msg.timeline : null;
  const timeline = raw ? foldDelegationTools(isStreaming ? raw : withFinalText(raw, msg.content)) : null;
  const views = isStreaming ? [] : messageViews(msg);
  // Only the text still being written needs its unfinished markup closed.
  const lastTextIdx = timeline ? timeline.map((e) => e.type).lastIndexOf('text') : -1;
  return (
    <ChatCodeActionsContext.Provider value={codeActions}>
    {/* Same row spacing and bubble type as MessageBubble: switching views
        must not move the answers, only add the steps between them. */}
    <div className="flex gap-3 mb-6 mx-2">
      <div className="flex flex-col items-center gap-1 flex-shrink-0">
        <div className="w-8 h-8 rounded-full bg-gray-800 flex items-center justify-center text-white">
          <Bot className="w-4 h-4" />
        </div>
        {agentName && (
          <span className="text-[9px] text-gray-400 font-medium text-center leading-tight max-w-[56px] break-words">{agentName}</span>
        )}
      </div>
      {/* As wide as a Chat view bubble at most: switching views keeps the
          transcript's shape, the steps just appear between the answers. */}
      <div className={`max-w-[72%] min-w-0 space-y-2 ${msg.error ? 'text-red-700' : ''}`}>
        {timeline ? (
          timeline.map((entry, i) => {
            if (entry.type === 'text') {
              if (!entry.text || !entry.text.trim()) return null;
              return (
                <div key={i} className="w-fit max-w-full bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm text-base text-gray-800 leading-relaxed">
                  {renderContent(entry.text, { streaming: isStreaming && i === lastTextIdx })}
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
              return <DelegationCard key={entry.run_id || i} entry={entry} />;
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
          <div className="w-fit max-w-full bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm text-base text-gray-800 leading-relaxed">
            {msg.content ? renderContent(msg.content, { streaming: isStreaming }) : <span className="text-gray-400 text-xs italic">{t('chat.noOutput')}</span>}
          </div>
        )}
        <MessageViews views={views} />
      </div>
    </div>
    </ChatCodeActionsContext.Provider>
  );
}

export { TimelineArtifactChip, BuildMessage, SystemNotice };
