/**
 * One message, as the Chat view shows it, and the placeholder standing in for
 * the one still being written.
 */
import { TokenPill } from '../ProcessGraph';
import { fmtDurationMs } from '../processUtils';
import { useI18n } from '../../i18n';
import { trimBubbleText } from '../../lib/chatText';
import ViewCard from '../../views/ViewCard';
import { ChatTrail } from './BuildMessage';
import { renderContent } from './markdown';
import { MessageEntities, MessageFiles, ResponseButtons } from './messageParts';
import { LiveThoughts } from './reasoning';
import { Bot, User } from 'lucide-react';

function MessageBubble({ msg, isStreaming = false, agentName, onAction, artifactsByPath }) {
  const { t } = useI18n();
  // A fact about the transcript (e.g. older turns folded into a summary), not
  // something either party said: a subtle centered line, not a chat bubble.
  if (msg.role === 'system') {
    return (
      <div className="flex justify-center mb-6 mx-2">
        <span className="text-xs text-gray-400 italic text-center px-3">{msg.content}</span>
      </div>
    );
  }
  const isUser = msg.role === 'user';
  // A tool is currently executing (set on tool_start, cleared on the first
  // response token). Surfaced as a labelled indicator so the user sees that
  // memory tools (recall / remember / forget / …) are processing.
  const runningTool = !isUser && isStreaming ? msg.running_tool : null;
  const showTypingDots = !isUser && isStreaming && !msg.content;
  return (
    <div className={`flex gap-3 mb-6 mx-2 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      {/* Avatar */}
      <div className="flex flex-col items-center gap-1 flex-shrink-0">
        <div
          className={`w-8 h-8 rounded-full flex items-center justify-center text-white
            ${isUser ? 'bg-indigo-600' : 'bg-gray-800'}`}
        >
          {isUser ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
        </div>
        {!isUser && agentName && (
          <span className="text-[9px] text-gray-400 font-medium text-center leading-tight max-w-[56px] break-words">
            {agentName}
          </span>
        )}
      </div>

      {/* Bubble */}
      <div
        className={`max-w-[72%] text-base leading-relaxed
          ${isUser
            ? 'bg-indigo-600 text-white rounded-2xl rounded-tr-sm px-4 py-3'
            : 'bg-white border border-gray-200 text-gray-800 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm'
          }
          ${msg.error ? 'border-red-300 bg-red-50 text-red-700' : ''}`}
      >
        {/* Thoughts, memory tool cards and delegated sub-agent runs, inline in
            the order they happened, above the response that followed them. */}
        {!isUser && <ChatTrail msg={msg} />}
        {showTypingDots ? (
          <span className="flex items-center gap-2">
            <span className="text-xs text-gray-400">{runningTool ? t('chat.runningTool', { tool: runningTool }) : t('chat.workingLabel')}</span>
            <span className="flex gap-1">
              {[0, 150, 300].map((delay) => (
                <span
                  key={delay}
                  className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
                  style={{ animationDelay: `${delay}ms` }}
                />
              ))}
            </span>
          </span>
        ) : isUser
          ? <span className="whitespace-pre-wrap">{trimBubbleText(msg.content)}</span>
          : <div>{renderContent(msg.content)}</div>
        }
        {/* The thought currently being written, tailing three lines. Cleared as
            soon as the completed thought arrives as a ReasoningStep above.
            Gated on the buffer rather than `isStreaming` so agent-initiated
            continuation runs (session SSE, no page-level loading flag) show it. */}
        {!isUser && <LiveThoughts text={msg.thinking_live} />}
        {/* Files the agent created / edited / deleted during this turn. */}
        {!isUser && <MessageFiles files={msg.files} artifactsByPath={artifactsByPath} />}
        {/* Links to the tasks / views / flows / files this turn touched. */}
        {!isUser && !showTypingDots && <MessageEntities entities={msg.entities} />}
        {/* Structured response UI (buttons / Telegram keyboard) under the text. */}
        {!isUser && !showTypingDots && (
          <ResponseButtons response={msg.response_obj} onAction={onAction} disabled={isStreaming} />
        )}
        {/* A rich view (chart / table / diagram / …) referenced by the reply. */}
        {!isUser && !showTypingDots && msg.response_obj?.kind === 'view_ref' && (
          <ViewCard viewRef={msg.response_obj} />
        )}
        {/* A tool started after some text already streamed (content present, so the
            typing-dots block above is hidden) — show a compact running indicator. */}
        {!isUser && runningTool && msg.content ? (
          <span className="mt-1 flex items-center gap-2 text-xs text-gray-400">
            <span>{t('chat.runningTool', { tool: runningTool })}</span>
            <span className="flex gap-1">
              {[0, 150, 300].map((delay) => (
                <span
                  key={delay}
                  className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
                  style={{ animationDelay: `${delay}ms` }}
                />
              ))}
            </span>
          </span>
        ) : null}
        {!isUser && msg.run_id && (
          <div className="mt-2 pt-2 border-t border-gray-100">
            <div className="flex items-center gap-1.5 mb-2">
              {typeof msg.inbound_tokens === 'number' && <TokenPill label={t('chat.in')} value={msg.inbound_tokens} />}
              {typeof msg.outbound_tokens === 'number' && <TokenPill label={t('chat.out')} value={msg.outbound_tokens} />}
              {typeof msg.duration_ms === 'number' && <TokenPill label={t('chat.duration')} value={fmtDurationMs(msg.duration_ms)} />}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Typing indicator
// ---------------------------------------------------------------------------
function TypingIndicator({ agentName }) {
  const { t } = useI18n();
  return (
    <div className="flex gap-3 mb-6">
      <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gray-800 flex items-center justify-center">
        <Bot className="w-4 h-4 text-white" />
      </div>
      <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm shadow-sm px-4 py-3 flex items-center gap-2">
        <span className="text-xs text-gray-400">{t('chat.agentIsWorking', { agent: agentName })}</span>
        <span className="flex gap-1">
          {[0, 150, 300].map((delay) => (
            <span
              key={delay}
              className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
              style={{ animationDelay: `${delay}ms` }}
            />
          ))}
        </span>
      </div>
    </div>
  );
}

export { MessageBubble, TypingIndicator };
