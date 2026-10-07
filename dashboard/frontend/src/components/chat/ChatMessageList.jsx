import { BuildMessage } from './BuildMessage';
import { MessageBubble, TypingIndicator } from './MessageBubble';
import HandoffDivider from './HandoffDivider';
import { AudioLines, Bot, Radio, Send as SendIcon, UsersRound, Workflow } from 'lucide-react';
import React from 'react';
import { Link } from 'react-router-dom';
import { useChatPage } from './context';
import { useChatScroll } from './useChatScroll';

// A prompt that can stay on screen for its reply: the user's own message, not
// one sent while the turn worked (that one steers the reply, it does not start it).
const isTurnPrompt = (msg) => msg.role === 'user' && !msg.steer;

// The transcript as turns: each prompt with the replies that follow it, so
// the prompt can stay on screen for them (useChatScroll). Whatever comes
// before the first prompt is a turn of its own, with nothing held.
function groupTurns(messages) {
  const turns = [];
  messages.forEach((msg, idx) => {
    if (isTurnPrompt(msg) || !turns.length) {
      turns.push({
        key: isTurnPrompt(msg) ? `turn-${msg.id}` : 'lead',
        promptId: isTurnPrompt(msg) ? msg.id : null,
        items: [],
      });
    }
    turns[turns.length - 1].items.push({ msg, idx });
  });
  return turns;
}

/**
 * The transcript itself, in whichever of the two views is selected, plus the
 * banner that appears when the open conversation is a Telegram thread.
 */
export default function ChatMessageList() {
  const {
    agentName, agents, artifacts, currentConv, currentConvId, currentTelegramBinding, flows, jumpToArtifact,
    liveMessages, liveTurn, loading, messages, renderedMessages,
    runTimelineByRunId, selectedFlow, selectedTeam, selectedWorkspace, sendMessage, t,
    targetMode, teams, telegramReplyAllowed, viewMode,
  } = useChatPage();
  const lastUserId = [...renderedMessages].reverse().find((m) => m.role === 'user')?.id ?? null;
  const { scrollerRef, contentRef, onScroll, onPromptClick } = useChatScroll({
    convId: currentConvId, lastUserId, messages: renderedMessages, stuckHint: t('chat.pinnedPromptHint'),
  });
  const renderMessage = (msg, idx) => {
    const msgAgentName = msg.role !== 'user'
      ? (currentConv?.origin === 'assistant' ? t('chat.assistantThread.badge')
        : msg.agent_label || agents.find((a) => a.id === msg.agent_id)?.name || msg.agent_id || agentName)
      : undefined;
    // A reply that took the conversation over by handoff opens with
    // the line saying who took over and why (chat/handoff.py).
    const handoffDivider = msg.role === 'agent' && msg.handoff ? (
      <HandoffDivider
        key="handoff"
        handoff={msg.handoff}
        agentName={agents.find((a) => a.id === msg.handoff.to_agent_id)?.name}
      />
    ) : null;
    // The mirrored turn is labelled: it is being written somewhere
    // else, so an answer appearing on its own is explained rather
    // than surprising.
    const liveLabel = msg.id === liveMessages[0]?.id ? (
      <div key="live-label" className="flex items-center gap-1.5 mb-2 text-[11px] font-medium text-indigo-500">
        <Radio className="w-3 h-3 animate-pulse" />
        {liveTurn?.source && liveTurn.source !== 'chat'
          ? t('chat.liveFromSource', { source: liveTurn.source })
          : t('chat.liveElsewhere')}
      </div>
    ) : null;
    if (viewMode === 'build') {
      // Reloaded messages lost their live timeline; fall back to the
      // server-reconstructed one (tools + thoughts) keyed by run_id.
      const reconstructed = (!msg.timeline || !msg.timeline.length) && msg.run_id
        ? runTimelineByRunId[String(msg.run_id)]
        : null;
      const buildMsg = reconstructed && reconstructed.length
        ? { ...msg, timeline: reconstructed }
        : msg;
      return (
        <React.Fragment key={msg.id}>
          {liveLabel}
          {handoffDivider}
          <BuildMessage
            msg={buildMsg}
            isStreaming={loading && idx === renderedMessages.length - 1 && msg.role === 'agent'}
            agentName={msgAgentName}
            onJumpArtifact={jumpToArtifact}
          />
        </React.Fragment>
      );
    }
    return (
      <React.Fragment key={msg.id}>
        {liveLabel}
        {handoffDivider}
        <MessageBubble
          msg={msg}
          isStreaming={
            (loading && idx === renderedMessages.length - 1 && msg.role === 'agent')
            || (msg.id === 'live-agent' && liveTurn?.status === 'running')
          }
          agentName={msgAgentName}
          artifactsByPath={artifacts}
          onAction={sendMessage}
        />
      </React.Fragment>
    );
  };
  const turns = groupTurns(renderedMessages);
  return (
    <>
        {/* Fixed Telegram debug-mirror banner — sits above the scrolling messages area */}
        {currentTelegramBinding && (
          <div className="flex-shrink-0 px-4 py-2 border-b border-sky-200 bg-sky-50 text-xs text-sky-800 flex items-center gap-2">
            <SendIcon className="w-3.5 h-3.5 shrink-0" />
            <div className="flex-1 min-w-0 truncate">
              <strong>{t('chat.telegram')}</strong> · {currentTelegramBinding.title || t('chat.telegramChat', { id: currentTelegramBinding.chat_id })}
              {' '}· {t('chat.boundTo')} <strong>{currentTelegramBinding.agent_name || currentTelegramBinding.agent_id}</strong>
              {currentTelegramBinding.workspace ? <> · {currentTelegramBinding.workspace}</> : null}
            </div>
            {!telegramReplyAllowed && (
              <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-medium" title={`Switch to "${currentTelegramBinding.workspace || 'default'}" to reply.`}>
                {t('chat.readOnly')}
              </span>
            )}
            <span className="px-1.5 py-0.5 rounded bg-sky-100 text-sky-700 font-medium">{t('chat.debugMirror')}</span>
          </div>
        )}

        {/* A conversation with the Assistant, by voice or text, as its text: read only. */}
        {currentConv?.origin === 'assistant' && (
          <div className="flex-shrink-0 px-4 py-2 border-b border-indigo-200 bg-indigo-50 text-xs text-indigo-800 flex items-center gap-2"
            data-testid="assistant-thread-banner">
            <AudioLines className="w-3.5 h-3.5 shrink-0" />
            <div className="flex-1 min-w-0">
              <strong>{t('chat.assistantThread.title')}</strong>
              {currentConv.assistant_thread?.mode === 'service' && <> · {t('chat.assistantThread.service')}</>}
              {' '}· {t('chat.assistantThread.hint')}
            </div>
            <span className="px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 font-medium">{t('chat.readOnly')}</span>
            {currentConv.assistant_thread?.active && (
              <Link to="/assistant" className="px-2 py-0.5 rounded bg-indigo-600 text-white font-medium hover:bg-indigo-700">
                {t('chat.assistantThread.continue')}
              </Link>
            )}
          </div>
        )}

        {/* Messages */}
        <div className="relative flex-1 min-h-0 flex flex-col">
        {/* The composer is over the foot of this scroller (ChatComposer sets
            the variable to its height), so the last message can still be
            scrolled clear of it. */}
        <div ref={scrollerRef} onScroll={onScroll} className="flex-1 overflow-y-auto"
             style={{ paddingBottom: 'var(--chat-composer-height, 0px)' }}>
          <div ref={contentRef} className="max-w-full mx-auto px-3 py-4 sm:px-6 sm:py-8">
            {messages.length === 0 && !loading && (
              <div className="flex flex-col items-center justify-center h-full min-h-[40vh] text-center">
                {targetMode === 'team' ? (
                  <>
                    <div className="w-16 h-16 bg-amber-100 rounded-2xl flex items-center justify-center mb-5 shadow-sm">
                      <UsersRound className="w-8 h-8 text-amber-600" />
                    </div>
                    <h2 className="text-xl font-semibold text-gray-800 mb-2">
                      {teams.find((tm) => tm.team_id === selectedTeam)?.name || t('chat.selectATeam')}
                    </h2>
                    <p className="text-sm text-gray-500 max-w-sm leading-relaxed">
                      {t('chat.emptyTeam')}
                    </p>
                  </>
                ) : targetMode === 'flow' ? (
                  <>
                    <div className="w-16 h-16 bg-emerald-100 rounded-2xl flex items-center justify-center mb-5 shadow-sm">
                      <Workflow className="w-8 h-8 text-emerald-600" />
                    </div>
                    <h2 className="text-xl font-semibold text-gray-800 mb-2">
                      {flows.find((f) => f.id === selectedFlow)?.name || t('chat.selectAFlow')}
                    </h2>
                    <p className="text-sm text-gray-500 max-w-sm leading-relaxed">
                      {t('chat.emptyFlow')}
                    </p>
                  </>
                ) : (
                  <>
                    <div className="w-16 h-16 bg-indigo-100 rounded-2xl flex items-center justify-center mb-5 shadow-sm">
                      <Bot className="w-8 h-8 text-indigo-600" />
                    </div>
                    <h2 className="text-xl font-semibold text-gray-800 mb-2">
                      {agentName}
                    </h2>
                    <p className="text-sm text-gray-500 max-w-sm leading-relaxed">
                      {t('chat.emptyAgent')}
                      {selectedWorkspace && <> {t('chat.agentWorksIn')} <strong className="text-gray-700">{selectedWorkspace}</strong> {t('chat.workspace')}</>}
                    </p>
                  </>
                )}
              </div>
            )}

            {turns.map((turn) => (
              <div key={turn.key} data-turn={turn.promptId ?? undefined} className="relative">
                {turn.items.map(({ msg, idx }) => {
                  const rendered = renderMessage(msg, idx);
                  if (!isTurnPrompt(msg)) return rendered;
                  return (
                    <React.Fragment key={msg.id}>
                      {/* The prompt, held at the top while its turn scrolls by
                          (useChatScroll); the spacer takes up what folding a
                          long held prompt frees, so the replies stay put. */}
                      <div
                        data-turn-prompt={msg.id}
                        className="chat-turn-prompt sticky top-0 z-10 pointer-events-none"
                        onClick={onPromptClick}
                      >
                        {rendered}
                      </div>
                      <div data-turn-spacer aria-hidden="true" />
                    </React.Fragment>
                  );
                })}
              </div>
            ))}

            {loading && (messages.length === 0 || messages[messages.length - 1].role !== 'agent') && (
              <TypingIndicator agentName={agentName} />
            )}

          </div>
        </div>
        </div>
    </>
  );
}
