/**
 * The Build view's message: the full trail of a turn, not just what it said.
 */
import { useContext, useMemo, useState } from 'react';
import { useI18n } from '../../i18n';
import { copyBubbleText, trimBubbleText } from '../../lib/chatText';
import { renderContent } from './markdown';
import { ExtractionToolCard, RecallToolCard } from './memoryCards';
import { EXTRACTION_TOOLS } from './memoryTools';
import { MessageViews } from './messageParts';
import { messageViews } from './turnViews';
import { fileLink } from './artifactOps';
import { FileOpenContext } from './fileOpenContext';
import { GraphNodeStep, LiveThoughts, ReasoningStep } from './reasoning';
import { DelegationCard, TimelineToolCard } from './timeline';
import { foldDelegationTools, withFinalText } from './trail';
import { ChangeStep } from './entityChanges';
import { DiffView } from './panels';
import { AVATAR_NAME_CLS, BUBBLE_SIZES } from './bubbleSizes';
import { ChatPageContext } from './context';
import { ChatCodeActionsContext } from './chatMarkdownContext';
import { Bot, ChevronDown, ChevronUp, FileText, User } from 'lucide-react';
import { Link } from 'react-router-dom';

// A file the turn changed: its name opens the file's page (`url`, see
// artifactOps.js fileLink), or shows it in the artifacts beside the chat
// where the page has them (fileOpenContext.js), or without either (a deleted file, an old change)
// jumps to the diff where a host offers that. Beside it, the change's size
// unfolds the diff right here, under the chip.
function TimelineArtifactChip({ entry, onJump, url = null }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  // A page with the run's artifacts beside the chat shows the file there.
  const openFile = useContext(FileOpenContext);
  const name = (entry.path || '').split('/').pop() || entry.path;
  const label = (
    <>
      <FileText className="w-3 h-3 text-gray-400 flex-shrink-0" />
      <span className={`text-[11px] font-medium truncate text-gray-700 ${entry.op === 'delete' ? 'line-through' : ''}`}>{name}</span>
    </>
  );
  const nameCls = 'inline-flex min-w-0 items-center gap-1.5 rounded-md px-1 py-0.5 no-underline transition-colors hover:bg-indigo-50';
  let nameEl;
  if (openFile && entry.op !== 'delete') {
    nameEl = <button type="button" onClick={() => openFile(entry.path, entry)} className={`${nameCls} text-left`} title={`${t('chat.showInArtifacts')}: ${entry.path}`}>{label}</button>;
  } else if (url) {
    nameEl = <Link to={url} className={nameCls} title={`${t('chat.openFile')}: ${entry.path}`}>{label}</Link>;
  } else if (onJump) {
    nameEl = <button type="button" onClick={() => onJump(entry.path)} className={`${nameCls} text-left`} title={entry.path}>{label}</button>;
  } else {
    nameEl = <span className="inline-flex min-w-0 items-center gap-1.5 px-1 py-0.5" title={entry.path}>{label}</span>;
  }
  const hasDiff = Boolean(entry.diff);
  return (
    <div className="max-w-full" data-testid="timeline-artifact">
      <div className="inline-flex max-w-full items-center gap-1 rounded-lg border border-indigo-300 bg-white px-1 py-0.5 shadow-sm">
        {nameEl}
        {hasDiff ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            title={t(open ? 'chat.hideChanges' : 'chat.showChanges')}
            className="inline-flex flex-shrink-0 items-center gap-1 rounded-md px-1 py-0.5 text-[10px] font-mono text-gray-500 transition-colors hover:bg-indigo-50 hover:text-gray-800"
          >
            {entry.additions > 0 && <span className="text-emerald-600">+{entry.additions}</span>}
            {entry.deletions > 0 && <span className="text-red-600">−{entry.deletions}</span>}
            {open ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>
        ) : null}
      </div>
      {open && hasDiff ? (
        <div className="mt-1 max-h-80 overflow-y-auto rounded-lg border border-gray-200 bg-white py-1" data-testid="timeline-artifact-diff">
          <DiffView diff={entry.diff} />
        </div>
      ) : null}
    </div>
  );
}

/**
 * A system notice, such as the compaction marker the backend emits when older
 * history is folded into a summary: one quiet centred line between messages,
 * never a bubble with an avatar. Shared by both chat views so the notice
 * looks the same whichever one is open.
 */
function SystemNotice({ msg }) {
  const { t } = useI18n();
  return (
    <div className="flex justify-center mb-6 mx-2">
      <span className="text-xs text-gray-400 italic text-center px-3">{noticeText(t, msg)}</span>
    </div>
  );
}

// A notice the hub wrote as data (a background run that ended,
// chat/background_runs.py) is worded here, in the reader's language.
function noticeText(t, msg) {
  const n = msg.notice;
  if (n?.type === 'background_run_finished') {
    const status = ['completed', 'failed', 'stopped'].includes(n.status) ? n.status : 'completed';
    return t(`chat.backgroundNotice.${n.kind || 'flow'}.${status}`, { name: n.name || '' });
  }
  return msg.content;
}

function BuildMessage({ msg, agentName, onJumpArtifact, isStreaming = false, compact = false }) {
  const size = compact ? BUBBLE_SIZES.compact : BUBBLE_SIZES.full;
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
      <div className={`${size.row} flex-row-reverse`}>
        {!compact && (
          <div className="flex-shrink-0 w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center text-white">
            <User className="w-4 h-4" />
          </div>
        )}
        <div data-prompt-bubble onCopy={copyBubbleText} className={`chat-prompt-bubble ${size.bubble} bg-indigo-600 text-white rounded-tr-sm leading-relaxed whitespace-pre-wrap`}>
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
  const textBox = compact ? 'rounded-xl px-3 py-2 text-sm' : 'rounded-2xl px-4 py-3 text-base';
  return (
    <ChatCodeActionsContext.Provider value={codeActions}>
    {/* Same row spacing and bubble type as MessageBubble: switching views
        must not move the answers, only add the steps between them. */}
    <div className={size.row}>
      {!compact && (
        <div className="flex flex-col items-center gap-1 flex-shrink-0 w-14">
          <div className="w-8 h-8 rounded-full bg-gray-800 flex items-center justify-center text-white">
            <Bot className="w-4 h-4" />
          </div>
          {agentName && (
            <span title={agentName} className={AVATAR_NAME_CLS}>{agentName}</span>
          )}
        </div>
      )}
      {/* As wide as a Chat view bubble at most: switching views keeps the
          transcript's shape, the steps just appear between the answers. */}
      <div className={`${compact ? 'max-w-[94%] w-full' : 'max-w-[88%] sm:max-w-[72%]'} min-w-0 space-y-2 ${msg.error ? 'text-red-700' : ''}`}>
        {timeline ? (
          timeline.map((entry, i) => {
            if (entry.type === 'text') {
              if (!entry.text || !entry.text.trim()) return null;
              return (
                <div key={i} className={`w-fit max-w-full bg-white border border-gray-200 rounded-tl-sm shadow-sm text-gray-800 leading-relaxed ${textBox}`}>
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
              return <TimelineArtifactChip key={i} entry={entry} onJump={onJumpArtifact} url={fileLink(entry, msg.entities)} />;
            }
            if (entry.type === 'change') {
              return <ChangeStep key={i} entry={entry} />;
            }
            return null;
          })
        ) : (
          <div className={`w-fit max-w-full bg-white border border-gray-200 rounded-tl-sm shadow-sm text-gray-800 leading-relaxed ${textBox}`}>
            {msg.content
              ? renderContent(msg.content, { streaming: isStreaming })
              : isStreaming
                ? <span className="text-xs text-gray-400 animate-pulse">{t('chat.workingLabel')}</span>
                : <span className="text-gray-400 text-xs italic">{t('chat.noOutput')}</span>}
          </div>
        )}
        {/* The thought being written, until it lands as a step above. */}
        {isStreaming && msg.thinking_live ? <LiveThoughts text={msg.thinking_live} /> : null}
        <MessageViews views={views} />
      </div>
    </div>
    </ChatCodeActionsContext.Provider>
  );
}

export { TimelineArtifactChip, BuildMessage, SystemNotice };
