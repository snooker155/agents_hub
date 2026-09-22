import { BuildMessage } from './BuildMessage';
import { MessageBubble, TypingIndicator } from './MessageBubble';
import { Bot, Radio, Send as SendIcon, UsersRound, Workflow } from 'lucide-react';
import React from 'react';
import { useChatPage } from './context';

/**
 * The transcript itself, in whichever of the two views is selected, plus the
 * banner that appears when the open conversation is a Telegram thread.
 */
export default function ChatMessageList() {
  const {
    agentName, agents, artifacts, currentTelegramBinding, flows, jumpToArtifact,
    liveMessages, liveTurn, loading, messages, messagesEndRef, renderedMessages,
    runTimelineByRunId, selectedFlow, selectedTeam, selectedWorkspace, sendMessage, t,
    targetMode, teams, telegramReplyAllowed, viewMode,
  } = useChatPage();
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

        {/* Messages */}
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-full mx-auto px-6 py-8">
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

            {renderedMessages.map((msg, idx) => {
              const msgAgentName = msg.role !== 'user'
                ? (msg.agent_label || agents.find((a) => a.id === msg.agent_id)?.name || msg.agent_id || agentName)
                : undefined;
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
                    <BuildMessage
                      msg={buildMsg}
                      agentName={msgAgentName}
                      onJumpArtifact={jumpToArtifact}
                    />
                  </React.Fragment>
                );
              }
              return (
                <React.Fragment key={msg.id}>
                  {liveLabel}
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
            })}

            {loading && (messages.length === 0 || messages[messages.length - 1].role !== 'agent') && (
              <TypingIndicator agentName={agentName} />
            )}

            <div ref={messagesEndRef} />
          </div>
        </div>
    </>
  );
}
