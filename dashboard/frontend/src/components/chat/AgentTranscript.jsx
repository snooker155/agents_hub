import React from 'react';
import { BuildMessage } from './BuildMessage';
import { MessageBubble, TypingIndicator } from './MessageBubble';

/**
 * A conversation drawn the way the Chat page draws it, for every other chat:
 * a flow's, an entity build chat's (loops, teams, agents, projects), an
 * instance's. The same bubbles, the same tool and thought cards, the same
 * file list, in whichever of the two views is chosen (see viewMode.js).
 *
 * Messages are the Chat page's shape: `{id, role, content, timeline, files,
 * changes, error, ...}`, built from the stream by turnEvents.js.
 *
 * @param {object[]} props.messages
 * @param {'chat'|'build'} props.viewMode
 * @param {string|null} props.streamingId the message still being written.
 * @param {boolean} [props.waiting] a turn is open and no reply has started yet.
 * @param {boolean} [props.compact] a side column's sizes (bubbleSizes.js).
 * @param {function} [props.agentNameFor] (msg) => the name under its avatar.
 * @param {string} [props.agentName] the name while waiting for the first reply.
 * @param {function} [props.onAction] a response button's value, sent as the next message.
 */
export default function AgentTranscript({
  messages, viewMode, streamingId = null, waiting = false, compact = false,
  agentNameFor = null, agentName = '', onAction = null, renderContent = null,
}) {
  return (
    <>
      {messages.map((msg) => {
        // `streaming`: a reply being written outside the turn's own stream
        // (a summary asked for after the fact, flow/summaryView.js).
        const isStreaming = msg.id === streamingId || msg.streaming === true;
        const name = msg.role === 'user' ? undefined : (agentNameFor ? agentNameFor(msg) : agentName);
        return viewMode === 'build' ? (
          <BuildMessage key={msg.id} msg={msg} isStreaming={isStreaming} agentName={name} compact={compact} />
        ) : (
          <MessageBubble
            key={msg.id}
            msg={msg}
            isStreaming={isStreaming}
            agentName={name}
            compact={compact}
            onAction={onAction || undefined}
            renderContent={renderContent || undefined}
          />
        );
      })}
      {waiting && <TypingIndicator agentName={agentName} compact={compact} />}
    </>
  );
}
