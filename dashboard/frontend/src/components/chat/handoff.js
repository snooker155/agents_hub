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

/**
 * The handing agent's bubble as the person should read it: it says whom the
 * chat goes to (`line`, localized by the caller). When the model wrote nothing
 * of its own, the turn's answer is just the handoff reason, which reads as if
 * the handing agent will do the work itself; that text is replaced by the
 * line (the reason stays on the divider above the receiving bubble). Text of
 * its own is kept, with the line after it. `next` is the message after `msg`:
 * the receiving bubble carries the handoff. Returns `msg` unchanged when it
 * did not hand over.
 */
export function withHandoffLine(msg, next, line) {
  const handoff = next?.role === 'agent' ? next.handoff : null;
  // The person handed the chat over (@agent): the agent before said nothing about it.
  if (!handoff || msg?.role !== 'agent' || handoff.by === 'user') return msg;
  if (handoff.from_agent_id && msg.agent_id && handoff.from_agent_id !== msg.agent_id) return msg;
  const reason = String(handoff.reason || '').trim();
  const own = (text) => {
    const v = String(text || '').trim();
    return v && v !== reason ? v : '';
  };
  const content = own(msg.content);
  const out = { ...msg, content: content ? `${content}\n\n${line}` : line };
  if (Array.isArray(msg.timeline) && msg.timeline.length) {
    out.timeline = [
      ...msg.timeline.filter((e) => e?.type !== 'text' || own(e.text)),
      { type: 'text', text: line },
    ];
  }
  return out;
}

/**
 * The person handed the chat to `agent` with @ in the composer
 * (agentMention.js): the handoff a receiving bubble, or the line of a chat
 * handed over without a message, carries. `by: 'user'` tells it from one an
 * agent made, which has a reason and a handing bubble.
 */
export function personHandoff(agent, fromAgentId) {
  return {
    by: 'user',
    from_agent_id: fromAgentId || null,
    to_agent_id: agent.id,
    to_agent_name: agent.name || agent.id,
  };
}

/**
 * Point the conversation `convId` at `agent`, the person's pick: the next
 * turns go to it. With `marker` (a chat handed over without a message), the
 * line that says so is appended as a system message.
 */
export function applyPersonHandoff(conversations, convId, agent, marker = null) {
  return conversations.map((c) => (c.id !== convId ? c : {
    ...c,
    agent_id: agent.id,
    target_mode: 'agent',
    ...(marker ? { messages: [...(c.messages || []), marker] } : {}),
  }));
}

/**
 * What the page should talk to for the open conversation `conv`, or null
 * when nothing can be decided yet: `{key, targetMode, agentId|flowId|teamId}`.
 * `key` changes whenever the stored target does (opened by link, moved by a
 * handoff, changed in another tab). An agent target waits for the agent list
 * (`selectableIds`), and one the workspace does not offer gives no agent.
 */
export function conversationTarget(conv, selectableIds) {
  if (!conv) return null;
  const key = [conv.id, conv.target_mode || '', conv.agent_id || '', conv.flow_id || '', conv.team_id || ''].join('|');
  if (conv.target_mode === 'flow' && conv.flow_id) return { key, targetMode: 'flow', flowId: conv.flow_id };
  if (conv.target_mode === 'team' && conv.team_id) return { key, targetMode: 'team', teamId: conv.team_id };
  if (!selectableIds || selectableIds.length === 0) return null;
  if (conv.agent_id && selectableIds.includes(conv.agent_id)) return { key, targetMode: 'agent', agentId: conv.agent_id };
  return { key, targetMode: null };
}

