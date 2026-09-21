import { useEffect, useRef, useState } from 'react';
import { getChatLive } from '../api';
import { useChannel, useStream } from './stream';

/**
 * The turn a conversation is in the middle of, as seen by someone who did not
 * start it.
 *
 * A chat turn used to be visible only in the window that asked for it: the
 * events came down that one request's response body. So the same conversation,
 * open in a second tab or on a second device, sat still until the answer was
 * over, and a turn started from Telegram or from an agent's inbox never showed
 * at all. The pipeline now puts every event on `chat:<conversation_id>`
 * (chat/broadcast.py); this is the consumer.
 *
 * What it produces is deliberately *not* part of the stored conversation. The
 * tab that ran the turn writes the transcript, with everything a bubble carries;
 * a follower only mirrors the generation while it happens and then steps aside
 * once the authored version arrives (`resolvedRunIds`). Nothing here is ever
 * saved, so two tabs watching one answer cannot write two versions of it.
 */

/** Streamed text kept in a live turn. Matches common/live_runs.MAX_TEXT. */
export const MAX_LIVE_TEXT = 40000;
/** How long a finished turn stays on screen when the transcript never catches
 *  up (the tab that ran it went away before saving). */
export const LIVE_TURN_LINGER_MS = 15000;

const clip = (text) => (text.length > MAX_LIVE_TEXT ? text.slice(-MAX_LIVE_TEXT) : text);

const blank = (fields = {}) => ({
  runId: null,
  user: '',
  text: '',
  thinking: [],
  thinkingLive: '',
  tools: [],
  status: 'running',
  agentId: null,
  source: 'chat',
  startedAt: Date.now(),
  ...fields,
});

/** The server's catch-up snapshot in the shape the reducer keeps. */
export function fromSnapshot(snapshot) {
  if (!snapshot) return null;
  return blank({
    runId: snapshot.run_id || null,
    user: snapshot.user_message || '',
    text: snapshot.text || '',
    thinking: snapshot.thinking || [],
    thinkingLive: snapshot.thinking_live || '',
    tools: snapshot.tools || [],
    status: snapshot.status || 'running',
    agentId: snapshot.agent_id || null,
    source: snapshot.source || 'chat',
    startedAt: snapshot.started_at ? snapshot.started_at * 1000 : Date.now(),
  });
}

/**
 * Fold one broadcast event into the live turn. Pure: no turn yet and an event
 * that implies one starts it, because a tab can join a conversation halfway
 * through and `turn_start` is long gone by then.
 */
export function reduceLiveTurn(turn, event) {
  if (!event || !event.type) return turn;
  const type = event.type;

  if (type === 'turn_start') {
    return blank({
      user: event.message || '',
      source: event.source || 'chat',
      agentId: event.agent_id || null,
    });
  }
  if (type === 'chat_saved' || type === 'heartbeat') return turn;
  if (type === 'chat_stream_end') {
    if (!turn || turn.status !== 'running') return turn;
    return { ...turn, status: 'finished', thinkingLive: '' };
  }

  const t = turn || blank();

  switch (type) {
    case 'meta':
      return { ...t, runId: event.run_id || t.runId, agentId: event.agent_id || t.agentId };
    case 'token':
      return { ...t, text: clip(t.text + (event.token || '')), thinkingLive: '' };
    case 'think':
    case 'plan':
      return {
        ...t,
        thinking: [...t.thinking, { kind: type, step: event.step, content: event.content || '' }],
        thinkingLive: '',
      };
    case 'think_delta':
      return { ...t, thinkingLive: clip(t.thinkingLive + (event.delta || '')) };
    case 'tool_start':
      return {
        ...t,
        tools: [...t.tools, { step: event.step, tool: event.tool, input: event.input || '', output: null, error: null }],
      };
    case 'tool_end':
    case 'tool_error': {
      const tools = [...t.tools];
      for (let i = tools.length - 1; i >= 0; i -= 1) {
        if (tools[i].step === event.step || tools[i].tool === event.tool) {
          tools[i] = type === 'tool_end'
            ? { ...tools[i], output: event.output ?? '' }
            : { ...tools[i], error: event.error || '' };
          break;
        }
      }
      return { ...t, tools };
    }
    case 'node_start':
      return {
        ...t,
        thinking: [...t.thinking, { kind: 'node', content: event.label || event.agent_id || event.node_id || '' }],
      };
    // A node of an *imported agent's own* graph, not of a hub flow. The mirror
    // shows the path through the graph in the same trail; the tab that owns the
    // turn renders it as timeline steps (Chat.jsx), which is the authored
    // version this one steps aside for.
    case 'graph_node_start':
      return {
        ...t,
        thinking: [...t.thinking, { kind: 'node', content: event.node || '' }],
      };
    case 'graph_node_end':
      return t;
    case 'team_message': {
      // A team answers as a conversation; the mirror shows it as it is said.
      const who = event.sender || event.agent_id || '';
      const body = event.content || '';
      if (!body) return t;
      const block = `**${who}**\n${body}`;
      return { ...t, text: clip(t.text ? `${t.text}\n\n${block}` : block) };
    }
    case 'error':
      return { ...t, status: 'failed', error: event.error || event.message || '' };
    case 'done':
      return {
        ...t,
        runId: event.run_id || t.runId,
        text: (event.response || '').trim() || t.text,
        status: event.ok === false ? 'failed' : 'finished',
        error: event.error || t.error,
        thinkingLive: '',
      };
    default:
      return t;
  }
}

/**
 * Follow the open conversation's live turn.
 *
 * @param {string|null} conversationId the chat being read.
 * @param {{muted?: boolean, resolvedRunIds?: Set<string>|null}} options
 *   `muted` for the tab that is running the turn itself — it renders its own
 *   stream and must not mirror its echo. `resolvedRunIds` are the runs the
 *   stored transcript already holds, which is how a mirrored turn knows the
 *   authored version has arrived and it can step aside.
 * @returns {object|null} the live turn, or null when the conversation is idle.
 */
export function useLiveChatTurn(conversationId, { muted = false, resolvedRunIds = null } = {}) {
  const { clientId } = useStream();
  // The turn is stored with the conversation (and the muted flag) it belongs
  // to, so switching chats or starting to type drops it without an effect that
  // resets state — which would render the previous chat's turn for a frame.
  const key = `${conversationId || ''}|${muted ? 'muted' : 'live'}`;
  const [state, setState] = useState({ key, turn: null });
  const turn = state.key === key ? state.turn : null;
  const clientIdRef = useRef(clientId);
  useEffect(() => { clientIdRef.current = clientId; }, [clientId]);

  // Catch up on a turn already in progress, then follow it. Without this, a tab
  // that opens a chat mid-answer joins the broadcast in the middle of a word.
  useEffect(() => {
    if (!conversationId || muted) return undefined;
    let cancelled = false;
    getChatLive(conversationId)
      .then(({ data }) => {
        if (cancelled || !data?.turn) return;
        // Only as a starting point: events that arrived while this was in
        // flight are ahead of the snapshot and must not be rolled back.
        setState((prev) => (prev.key === key && prev.turn
          ? prev
          : { key, turn: fromSnapshot(data.turn) }));
      })
      .catch(() => { /* no catch-up available; follow from here */ });
    return () => { cancelled = true; };
  }, [conversationId, muted, key]);

  useChannel(conversationId && !muted ? `chat:${conversationId}` : null, (event) => {
    if (!event) return;
    // Our own echo: this tab is rendering these events from its own stream.
    if (event.origin_client && event.origin_client === clientIdRef.current) return;
    setState((prev) => ({
      key,
      turn: reduceLiveTurn(prev.key === key ? prev.turn : null, event),
    }));
  });

  // A finished turn that the transcript never took over (the tab that ran it
  // went away before saving) is dropped rather than left on screen for good.
  useEffect(() => {
    if (!turn || turn.status === 'running') return undefined;
    const timer = setTimeout(() => setState({ key, turn: null }), LIVE_TURN_LINGER_MS);
    return () => clearTimeout(timer);
  }, [turn, key]);

  // Once the stored transcript holds this run, the authored bubbles are the
  // real thing and the mirror steps aside.
  if (turn?.runId && resolvedRunIds?.has(String(turn.runId))) return null;
  return turn;
}

export default useLiveChatTurn;
