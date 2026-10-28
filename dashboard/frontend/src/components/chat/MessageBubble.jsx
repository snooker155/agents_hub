/**
 * One message, as the Chat view shows it, and the placeholder standing in for
 * the one still being written.
 *
 * The Chat view keeps a reply to its answer. What the agent does on the way
 * (thoughts, tool calls, text between steps) passes through while the turn is
 * live, as a three-line thought ticker or a one-line notice saying what the
 * tool call does (toolActivity.js), and is gone
 * once it ends; the Build view is where those steps stay. A delegated run is
 * shown the same way: while it works, the one step the worker is on, then a
 * line saying it finished, and nothing of it once the turn is over.
 */
import { useContext, useMemo } from 'react';
import { useI18n } from '../../i18n';
import { copyBubbleText, trimBubbleText } from '../../lib/chatText';
import ViewCard from '../../views/ViewCard';
import { SystemNotice } from './BuildMessage';
import Citations, { CitedText } from './Citations';
import { MessageEntities, MessageFiles, MessageViews, ResponseButtons } from './messageParts';
import { MessageChanges } from './entityChanges';
import { AVATAR_NAME_CLS, BUBBLE_SIZES } from './bubbleSizes';
import { messageViews } from './turnViews';
import { LiveThoughts } from './reasoning';
import LiveDelegation from './liveDelegation';
import { currentActivity, foldDelegationTools } from './trail';
import { steerCaption } from './steering';
import { toolActivity } from './toolActivity';
import ToolApprovals from './ToolApprovalCard';
import RefusalCard, { GuardrailWarnings } from './RefusalCard';
import { pendingApproval } from './toolApprovals';
import { ChatPageContext } from './context';
import { ChatCodeActionsContext } from './chatMarkdownContext';
import LiveMark from '../liveMark/LiveMark';
import { stateForTurn } from '../liveMark/activity';
import { Bot, Mic, Square, User } from 'lucide-react';

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

// `renderContent`: a host's own rendering of a finished reply (the Help panel
// turns links into navigation); the markdown renderer otherwise.
function MessageBubble({ msg, isStreaming = false, agentName, onAction, onRetry, compact = false, renderContent = null }) {
  const size = compact ? BUBBLE_SIZES.compact : BUBBLE_SIZES.full;
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
      <div className={`${size.row} flex-row-reverse`}>
        {!compact && (
          <div className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-white bg-indigo-600">
            <User className="w-4 h-4" />
          </div>
        )}
        <div data-prompt-bubble onCopy={copyBubbleText} className={`chat-prompt-bubble ${size.bubble} leading-relaxed bg-indigo-600 text-white rounded-tr-sm`}>
          <span className="whitespace-pre-wrap">{trimBubbleText(msg.content)}</span>
          {/* Said aloud to the Assistant: the line is its transcript. */}
          {msg.voice && (
            <span className="mt-1 flex items-center gap-1 text-[11px] text-indigo-100" data-testid="spoken-caption">
              <Mic className="w-3 h-3" /> {t('chat.assistantThread.spoken')}
            </span>
          )}
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
  // A step in progress standing in for the reply (the flow chat's Summary
  // view, flow/summaryView.js): what the node does, never the text it
  // writes, which is not the reply. Its name is under the avatar.
  const progress = isStreaming && Boolean(msg.progress);
  // While live, the text on show is the step being written, not everything
  // streamed so far: text written before a tool call goes when the call starts.
  const text = progress ? ''
    : !isStreaming || !hasTrail ? msg.content
    : activity?.kind === 'text' ? activity.text : '';
  const liveThought = isStreaming
    ? (hasTrail ? (activity?.kind === 'thinking' ? activity.text : '') : msg.thinking_live)
    : '';
  const runningTool = isStreaming
    ? (hasTrail ? (activity?.kind === 'tool' ? activity.tool : null) : msg.running_tool)
    : null;
  const runningInput = hasTrail ? (activity?.kind === 'tool' ? activity.input : null) : msg.running_tool_input;
  // The last thing the turn did was hand work to another agent: that run is
  // the step on show (running, or just finished) until the turn moves on.
  const trail = isStreaming && hasTrail && !activity ? foldDelegationTools(msg.timeline) : [];
  const liveDelegation = trail[trail.length - 1]?.type === 'delegation' ? trail[trail.length - 1] : null;
  const showWorking = isStreaming && !progress && !text && !liveThought && !liveDelegation;
  // A tool call of this turn waits for a person: say so instead of "running".
  const waitingOn = isStreaming ? pendingApproval(msg) : null;
  const workingLabel = waitingOn
    ? t('toolApproval.waiting', { tool: waitingOn.tool })
    : runningTool ? toolActivity(t, runningTool, runningInput) : msg.working_label || t('chat.workingLabel');
  // While the turn is live the avatar is the live mark, showing the step.
  const markState = isStreaming
    ? stateForTurn({
      waiting: !!waitingOn,
      thinking: !!liveThought,
      tool: runningTool,
      input: activity?.kind === 'tool' ? activity.input : undefined,
      text: !!text,
    })
    : null;
  const done = !isStreaming;
  // A turn the guard or a limit refused: a card with the action, not the error text.
  const refusal = done && msg.error ? msg.refusal : null;
  const views = done ? messageViews(msg) : [];
  const shownViewIds = new Set(views.map((v) => v.view_id));
  // A file the turn changed is listed, linked, with the files; its entity
  // chip would say the same twice.
  const listedFiles = new Set((msg.files || []).map((f) => f.path));
  const entities = (msg.entities || []).filter((e) => !(e.kind === 'view' && shownViewIds.has(e.id))
    && !(e.kind === 'file' && listedFiles.has(e.title)));
  return (
    <div className={`${size.row} flex-row`}>
      {/* Avatar: a fixed width, so every agent's bubble starts at the same
          indent whatever the length of its name; a long name wraps under it,
          three lines at most, in full on hover. A compact chat has one agent and no room for the gutter. */}
      {!compact && <div className="flex flex-col items-center gap-1 flex-shrink-0 w-14">
        {markState ? (
          <LiveMark state={markState} initial="working" size={32} className="w-8 h-8" />
        ) : (
          <div className="w-8 h-8 rounded-full flex items-center justify-center text-white bg-gray-800">
            <Bot className="w-4 h-4" />
          </div>
        )}
        {agentName && (
          <span title={agentName} className={AVATAR_NAME_CLS}>
            {agentName}
          </span>
        )}
      </div>}

      {/* Bubble */}
      <div
        className={`${size.bubble} min-w-0 leading-relaxed bg-white border border-gray-200 text-gray-800 rounded-tl-sm shadow-sm
          ${msg.error && !refusal ? 'border-red-300 bg-red-50 text-red-700' : ''}`}
      >
        {progress ? (
          <span className={`block ${liveDelegation || liveThought ? 'mb-1.5' : ''}`} data-testid="flow-step-progress">
            <WorkingDots label={workingLabel} />
          </span>
        ) : null}
        {liveDelegation ? <LiveDelegation key={liveDelegation.run_id} entry={liveDelegation} /> : null}
        {showWorking ? (
          <WorkingDots label={workingLabel} />
        ) : refusal ? (
          <RefusalCard refusal={refusal} onRetry={onRetry} />
        ) : text ? (
          <ChatCodeActionsContext.Provider value={codeActions}>
            {renderContent && !isStreaming
              ? renderContent(text)
              : <CitedText content={text} citations={msg.citations} anchor={msg.id} streaming={isStreaming} />}
          </ChatCodeActionsContext.Provider>
        ) : null}
        {/* The thought being written, tailing three lines, gone when the step ends. */}
        {liveThought ? <LiveThoughts text={liveThought} /> : null}
        {/* A tool started while text was on show: a one-line notice under it. */}
        {runningTool && !showWorking && !progress ? (
          <span className="mt-1 block"><WorkingDots label={workingLabel} /></span>
        ) : null}
        {/* A tool call waiting for a person: Approve / Deny in the same turn. */}
        <ToolApprovals msg={msg} live={isStreaming} />
        {/* What a build chat's turn changed in the thing it is pinned to. */}
        {done && <MessageChanges changes={msg.changes} />}
        {/* Warn guardrails that found something: the reply stands, this says so. */}
        {done && !refusal && <GuardrailWarnings warnings={msg.guardrail_warnings} />}
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
            {msg.response_obj?.kind === 'view_ref' && <ViewCard viewRef={msg.response_obj} inChat />}
          </>
        )}
        {/* The person stopped this turn: what it got so far stays above. */}
        {done && msg.stopped ? (
          <span className={`flex items-center gap-1.5 text-xs text-gray-500 ${text ? 'mt-2' : ''}`} data-testid="turn-stopped">
            <Square className="w-3 h-3" />
            {t('chat.stoppedByYou')}
          </span>
        ) : null}
        {/* Files the agent created / edited / deleted during this turn: the
            last thing in the bubble, in a field of their own under a line. */}
        <MessageFiles files={msg.files} entities={msg.entities} className={size.footer} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Typing indicator
// ---------------------------------------------------------------------------
function TypingIndicator({ agentName, compact = false }) {
  const { t } = useI18n();
  const size = compact ? BUBBLE_SIZES.compact : BUBBLE_SIZES.full;
  return (
    <div className={size.row}>
      {/* The same avatar column as an answer, so the indicator lines up with it. */}
      {!compact && (
        <div className="flex-shrink-0 w-14 flex justify-center">
          <LiveMark state="working" size={32} className="w-8 h-8" />
        </div>
      )}
      <div className={`bg-white border border-gray-200 rounded-tl-sm shadow-sm flex items-center gap-2 ${compact ? 'rounded-xl px-3 py-2' : 'rounded-2xl px-4 py-3'}`}>
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
