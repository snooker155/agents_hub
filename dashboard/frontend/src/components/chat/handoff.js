/**
 * A chat turn that changes hands (chat/handoff.py, docs/handoffs.md).
 *
 * The backend streams one `handoff` event between the handing agent's run and
 * the receiving agent's: the handing bubble closes with that agent's own reply
 * (`from_response`), and a new bubble opens below it for the receiving agent,
 * carrying the handoff it came from. The divider in the transcript is drawn
 * from that field (HandoffDivider), so a reloaded conversation, or one the
 * server stored because the tab was gone (chat/broadcast.py), shows the same
 * thing. The conversation's target moves to the receiving agent as well: the
 * next turn goes to it.
 *
 * Pure reducers, so the shape written to `conversations` is testable without
 * a stream or a component.
 */

// The handoff as a bubble keeps it: the stream event without its type and the
// handing run's own tallies (those belong on the handing bubble).
export function handoffFields(event) {
  const {
    type: _type, usage: _usage, tool_calls: _toolCalls, duration_ms: _duration, session_id: _session,
    ...rest
  } = event || {};
  return rest;
}

function closeHandingBubble(msg, event) {
  return {
    ...msg,
    content: (event.from_response || '').trim() || msg.content || '',
    run_id: event.run_id || msg.run_id || null,
    running_tool: null,
    thinking_live: '',
    inbound_tokens: event.usage?.inbound_tokens ?? msg.inbound_tokens ?? null,
    outbound_tokens: event.usage?.outbound_tokens ?? msg.outbound_tokens ?? null,
    total_tokens: event.usage?.total_tokens ?? msg.total_tokens ?? null,
    tool_calls: event.tool_calls ?? msg.tool_calls ?? null,
    duration_ms: event.duration_ms ?? msg.duration_ms ?? null,
  };
}

export function buildReceivingBubble(id, event) {
  return {
    id,
    role: 'agent',
    agent_id: event.to_agent_id,
    content: '',
    error: false,
    run_id: event.next_run_id || null,
    inbound_tokens: null,
    outbound_tokens: null,
    total_tokens: null,
    tool_calls: null,
    duration_ms: null,
    handoff: handoffFields(event),
  };
}

/**
 * Fold one `handoff` event into the conversation list: close the bubble
 * `fromMsgId`, append the receiving agent's bubble as `toMsgId`, and point the
 * conversation at the receiving agent.
 */
export function applyHandoff(conversations, convId, fromMsgId, toMsgId, event) {
  return conversations.map((c) => {
    if (c.id !== convId) return c;
    const messages = c.messages.map((m) => (m.id === fromMsgId ? closeHandingBubble(m, event) : m));
    return {
      ...c,
      agent_id: event.to_agent_id || c.agent_id,
      messages: [...messages, buildReceivingBubble(toMsgId, event)],
    };
  });
}

/**
 * The handed-over part of a turn someone else is running (the live mirror,
 * components/chatLiveTurn.js): one closed bubble per handing agent, each
 * after the first carrying the handoff that brought it.
 */
export function liveHandoffBubbles(handoffs) {
  return (handoffs || []).map((h, i) => ({
    id: `live-handoff-${i}`,
    role: 'agent',
    agent_id: h.from_agent_id,
    content: h.from_response || '',
    run_id: h.run_id || null,
    ...(i > 0 ? { handoff: handoffs[i - 1] } : {}),
  }));
}
