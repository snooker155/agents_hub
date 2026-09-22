/**
 * The optimistic side of a send: the conversation record created for a first
 * message, the user's own bubble, and the empty assistant bubble a
 * single-target (agent) turn streams into. Flow and team turns build their
 * bubbles later, one per node / board entry — see handleFlowResponse and
 * handleTeamResponse.
 *
 * Pure builders/reducers so the shape of what gets written to `conversations`
 * can be checked without a real setState.
 */

import { truncateTitle } from './buildRequest';

function buildNewConversation({
  convId, text, attachmentLine, t, targetMode, isFlowMode, isTeamMode,
  selectedAgent, selectedFlow, selectedTeam, selectedWorkspace, selectedProject,
}) {
  const basis = text || attachmentLine || t('chat.newChat');
  return {
    id: convId,
    title: truncateTitle(basis),
    agent_id: targetMode === 'agent' ? selectedAgent : null,
    flow_id: isFlowMode ? selectedFlow : null,
    team_id: isTeamMode ? selectedTeam : null,
    target_mode: targetMode,
    workspace: selectedWorkspace,
    project_id: selectedProject || null,
    messages: [],
    created_at: new Date().toISOString(),
  };
}

// Append the user's own bubble, and give a still-titleless conversation its
// first title from it (the auto-title computed for the request may differ —
// this one only ever runs once, on the conversation's very first message).
function appendUserMessage(conversations, convId, userMsg, text, attachmentLine) {
  return conversations.map((c) =>
    c.id === convId
      ? {
          ...c,
          messages: [...c.messages, userMsg],
          title: c.messages.length === 0 ? truncateTitle(text || attachmentLine) : c.title,
        }
      : c,
  );
}

function buildAssistantBubble(assistantId, selectedAgent) {
  return {
    id: assistantId,
    role: 'agent',
    agent_id: selectedAgent,
    content: '',
    error: false,
    run_id: null,
    inbound_tokens: null,
    outbound_tokens: null,
    total_tokens: null,
    tool_calls: null,
    duration_ms: null,
  };
}

export { buildNewConversation, appendUserMessage, buildAssistantBubble };
