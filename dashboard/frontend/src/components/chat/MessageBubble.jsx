/**
 * One message, as the Chat view shows it, and the placeholder standing in for
 * the one still being written.
 *
 * The Chat view keeps a reply to its answer. What the agent does on the way
 * (thoughts, tool calls, text between steps) passes through while the turn is
 * live, as a three-line thought ticker or a one-line tool notice, and is gone
 * once it ends; the Build view is where those steps stay. A delegated run is
 * shown the same way: while it works, the one step the worker is on, then a
 * line saying it finished, and nothing of it once the turn is over.
 */
import { useContext, useMemo } from 'react';
import { useI18n } from '../../i18n';
import { trimBubbleText } from '../../lib/chatText';
import ViewCard from '../../views/ViewCard';
import { SystemNotice } from './BuildMessage';
import Citations, { CitedText } from './Citations';
import { MessageEntities, MessageFiles, MessageViews, ResponseButtons } from './messageParts';
import { messageViews } from './turnViews';
import { LiveThoughts } from './reasoning';
import LiveDelegation from './liveDelegation';
import { currentActivity, foldDelegationTools } from './trail';
import { steerCaption } from './steering';
import ToolApprovals from './ToolApprovalCard';
import { pendingApproval } from './toolApprovals';
import { ChatPageContext } from './context';
import { ChatCodeActionsContext } from './chatMarkdownContext';
import LiveMark from '../liveMark/LiveMark';
import { stateForTurn } from '../liveMark/activity';
import { Bot, User } from 'lucide-react';

function WorkingDots({ label }) {
  return (
    <span className="flex items-center gap-2">
      <span className="text-xs text-gray-400">{label}</span>
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
  );
}

function MessageBubble({ msg, isStreaming = false, agentName, onAction, artifactsByPath }) {
  const { t } = useI18n();
  // Outside the Chat page (tests, other hosts) there is no Code panel to open.
  const openInCodePanel = useContext(ChatPageContext)?.openInCodePanel;
  const codeActions = useMemo(() => (
    openInCodePanel && msg.run_id
      ? { open: (code, language) => openInCodePanel({ code, language, runId: msg.run_id }) }
      : null
  ), [openInCodePanel, msg.run_id]);
  // A fact about the transcript (e.g. older turns folded into a summary), not
  // something either party said: a subtle centered line, not a chat bubble.
  if (msg.role === 'system') return <SystemNotice msg={msg} />;
  const isUser = msg.role === 'user';
  if (isUser) {
    // A message sent while the turn worked: where it is (waiting for the next
    // step, delivered at step N, queued for the next turn).
    const caption = msg.steer ? steerCaption(msg.steer) : null;
    return (
      <div className="flex gap-3 mb-6 mx-2 flex-row-reverse">
        <div className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-white bg-indigo-600">
          <User className="w-4 h-4" />
        </div>
        <div data-prompt-bubble className="chat-prompt-bubble max-w-[72%] text-base leading-relaxed bg-indigo-600 text-white rounded-2xl rounded-tr-sm px-4 py-3">
          <span className="whitespace-pre-wrap">{trimBubbleText(msg.content)}</span>
          {caption && (
            <span className="mt-1 block text-[11px] text-indigo-100" data-testid="steer-caption">
              {msg.steer.mode === 'system' && <span className="font-semibold mr-1">{t('steering.systemTag')}</span>}
              {t(caption.key, caption.values)}
            </span>
          )}
        </div>
      </div>
    );
  }

  // A turn mirrored from elsewhere carries no trail, only the running tool and
  // the thought ticker, so it falls back to those.
  const hasTrail = Array.isArray(msg.timeline) && msg.timeline.length > 0;
  const activity = isStreaming && hasTrail ? currentActivity(msg) : null;
  // While live, the text on show is the step being written, not everything
  // streamed so far: text written before a tool call goes when the call starts.
  const text = !isStreaming || !hasTrail
    ? msg.content
    : activity?.kind === 'text' ? activity.text : '';
  const liveThought = isStreaming
    ? (hasTrail ? (activity?.kind === 'thinking' ? activity.text : '') : msg.thinking_live)
    : '';
  const runningTool = isStreaming
    ? (hasTrail ? (activity?.kind === 'tool' ? activity.tool : null) : msg.running_tool)
    : null;
  // The last thing the turn did was hand work to another agent: that run is
  // the step on show (running, or just finished) until the turn moves on.
  const trail = isStreaming && hasTrail && !activity ? foldDelegationTools(msg.timeline) : [];
  const liveDelegation = trail[trail.length - 1]?.type === 'delegation' ? trail[trail.length - 1] : null;
  const showWorking = isStreaming && !text && !liveThought && !liveDelegation;
  // A tool call of this turn waits for a person: say so instead of "running".
  const waitingOn = isStreaming ? pendingApproval(msg) : null;
  const workingLabel = waitingOn
    ? t('toolApproval.waiting', { tool: waitingOn.tool })
    : runningTool ? t('chat.runningTool', { tool: runningTool }) : t('chat.workingLabel');
  // While the turn is live the avatar is the live mark, showing the step.
  const markState = isStreaming
    ? stateForTurn({
      waiting: !!waitingOn,
      thinking: !!liveThought,
      tool: runningTool,
      text: !!text,
    })
    : null;
  const done = !isStreaming;
  const views = done ? messageViews(msg) : [];
  const shownViewIds = new Set(views.map((v) => v.view_id));
  const entities = (msg.entities || []).filter((e) => !(e.kind === 'view' && shownViewIds.has(e.id)));
  return (
    <div className="flex gap-3 mb-6 mx-2 flex-row">
      {/* Avatar */}
      <div className="flex flex-col items-center gap-1 flex-shrink-0">
        {markState ? (
          <LiveMark state={markState} initial="working" size={32} className="w-8 h-8" />
        ) : (
          <div className="w-8 h-8 rounded-full flex items-center justify-center text-white bg-gray-800">
            <Bot className="w-4 h-4" />
          </div>
        )}
        {agentName && (
          <span className="text-[9px] text-gray-400 font-medium text-center leading-tight max-w-[56px] break-words">
            {agentName}
          </span>
        )}
      </div>

      {/* Bubble */}
      <div
        className={`max-w-[72%] min-w-0 text-base leading-relaxed bg-white border border-gray-200 text-gray-800 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm
          ${msg.error ? 'border-red-300 bg-red-50 text-red-700' : ''}`}
      >
        {liveDelegation ? <LiveDelegation key={liveDelegation.run_id} entry={liveDelegation} /> : null}
        {showWorking ? (
          <WorkingDots label={workingLabel} />
        ) : text ? (
          <ChatCodeActionsContext.Provider value={codeActions}>
            <CitedText content={text} citations={msg.citations} anchor={msg.id} streaming={isStreaming} />
          </ChatCodeActionsContext.Provider>
        ) : null}
        {/* The thought being written, tailing three lines, gone when the step ends. */}
        {liveThought ? <LiveThoughts text={liveThought} /> : null}
        {/* A tool started while text was on show: a one-line notice under it. */}
        {runningTool && !showWorking ? (
          <span className="mt-1 block"><WorkingDots label={workingLabel} /></span>
        ) : null}
        {/* A tool call waiting for a person: Approve / Deny in the same turn. */}
        <ToolApprovals msg={msg} live={isStreaming} />
        {/* Files the agent created / edited / deleted during this turn. */}
        <MessageFiles files={msg.files} artifactsByPath={artifactsByPath} />
        {done && (
          <>
            {/* The views the turn built, delegated runs included, as live previews. */}
            <MessageViews views={views} />
            {/* Links to the tasks / flows / files this turn touched. */}
            <MessageEntities entities={entities} />
            {/* The sources the reply cites as [n] (a memory search's passages). */}
            <Citations citations={msg.citations} anchor={msg.id} />
            {/* Structured response UI (buttons / Telegram keyboard) under the text. */}
            <ResponseButtons response={msg.response_obj} onAction={onAction} disabled={isStreaming} />
            {/* A rich view (chart / table / diagram / …) the reply is. */}
            {msg.response_obj?.kind === 'view_ref' && <ViewCard viewRef={msg.response_obj} />}
          </>
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
      <LiveMark state="working" size={32} className="flex-shrink-0 w-8 h-8" />
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
