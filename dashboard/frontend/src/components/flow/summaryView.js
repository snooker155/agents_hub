/**
 * The flow chat's Summary view: one reply per request instead of a bubble
 * per node. A turn sent in this view ends with a summary message
 * (chat/flow_summary.py), which is the reply; a turn without one (sent in
 * the Chat or Build view, or its summary could not be written) shows its
 * last node's answer. While the turn works, the reply is the step in
 * progress: what the node is doing, without the text it writes (the node's
 * name is under the avatar).
 * Either way the reply carries the files and links every node of the turn
 * produced.
 */
import { useCallback, useEffect, useState } from 'react';

export const SUMMARY_MODE_KEY = 'agent_hub_flow_chat_summary';
const CHANGE_EVENT = 'agent-hub:flow-chat-summary';

function readSummaryMode() {
  try { return localStorage.getItem(SUMMARY_MODE_KEY) === '1'; } catch { return false; }
}

/** [summaryMode, setSummaryMode], kept in this browser for every flow chat. */
export function useFlowSummaryMode() {
  const [on, setOn] = useState(readSummaryMode);
  useEffect(() => {
    const onChange = () => setOn(readSummaryMode());
    window.addEventListener(CHANGE_EVENT, onChange);
    window.addEventListener('storage', onChange);
    return () => {
      window.removeEventListener(CHANGE_EVENT, onChange);
      window.removeEventListener('storage', onChange);
    };
  }, []);
  const set = useCallback((next) => {
    setOn(next);
    try { localStorage.setItem(SUMMARY_MODE_KEY, next ? '1' : '0'); } catch { /* storage unavailable */ }
    try { window.dispatchEvent(new CustomEvent(CHANGE_EVENT)); } catch { /* no window */ }
  }, []);
  return [on, set];
}

// Every file and link the turn's nodes produced, each once (a file's last
// operation wins).
function turnParts(agents) {
  const files = new Map();
  const entities = new Map();
  for (const m of agents) {
    for (const f of m.files || []) if (f?.path) files.set(f.path, f);
    for (const e of m.entities || []) if (e?.id) entities.set(`${e.kind}:${e.id}`, e);
  }
  return { files: [...files.values()], entities: [...entities.values()] };
}

function hasText(m) {
  return Boolean(String(m?.content || '').trim());
}

// The conversation as turns: a user message and the replies after it.
function splitTurns(messages) {
  const turns = [];
  for (const m of messages || []) {
    if (m.role === 'user' || !turns.length) turns.push({ user: m.role === 'user' ? m : null, agents: [] });
    if (m.role !== 'user') turns[turns.length - 1].agents.push(m);
  }
  return turns;
}

/**
 * The finished turns that have no summary and would get one written: two or
 * more agent answers, or one that failed (a single clean answer already is
 * its summary, chat/flow_summary.py). Each as what the summary endpoint
 * takes: `{turn_id, user_message, responses}`. A turn without an id (sent
 * before turns had one) cannot be named and is left out.
 */
export function turnsWithoutSummary(messages, { loading = false } = {}) {
  const turns = splitTurns(messages);
  const out = [];
  turns.forEach((turn, i) => {
    if (loading && i === turns.length - 1) return;
    const turnId = turn.user?.turn_id;
    if (!turnId || turn.agents.some((m) => m.summary)) return;
    // A switch's bubble says which step it chose; it is no answer.
    const answers = turn.agents.filter((m) => !m.route);
    if (!answers.length || (answers.length < 2 && !answers.some((m) => m.error))) return;
    out.push({
      turn_id: turnId,
      user_message: String(turn.user.content || ''),
      responses: answers.map((m) => ({
        agent_label: m.agent_label || m.agent_id || '',
        agent_id: m.agent_id || null,
        response: String(m.content || ''),
        error: m.error ? String(m.content || 'failed') : null,
        is_agent: true,
      })),
    });
  });
  return out;
}

/**
 * The messages the Summary view draws.
 *
 * @param {object[]} messages the flow chat's messages, a bubble per node.
 * @param {object} opts
 * @param {string|null} opts.streamingId the node bubble being written.
 * @param {boolean} opts.loading a turn is open.
 * @param {Set<string>} [opts.writing] turns whose summary is being written now.
 * @param {string} [opts.writingLabel] what their reply says meanwhile.
 */
export function summaryViewMessages(messages, {
  streamingId = null, loading = false, writing = null, writingLabel = '',
} = {}) {
  const turns = splitTurns(messages);
  const out = [];
  turns.forEach((turn, i) => {
    if (turn.user) out.push(turn.user);
    const { agents } = turn;
    if (!agents.length) return;
    const live = loading && i === turns.length - 1;
    const summary = agents.find((m) => m.summary);
    const nodes = agents.filter((m) => !m.summary);
    const parts = turnParts(nodes.concat(summary ? [summary] : []));
    const turnId = turn.user?.turn_id;
    if (!summary && turnId && writing?.has(turnId)) {
      out.push({
        id: `summary-writing-${turnId}`, role: 'agent', summary: true, turn_id: turnId,
        content: '', timeline: [], streaming: true, working_label: writingLabel, ...parts,
      });
      return;
    }
    if (summary) {
      out.push({ ...summary, ...parts });
      return;
    }
    if (live) {
      // The node at work, as a step in progress; between nodes there is none.
      const current = nodes.find((m) => m.id === streamingId);
      if (current) out.push({ ...current, progress: true });
      return;
    }
    const last = [...nodes].reverse().find(hasText) || nodes[nodes.length - 1];
    out.push({ ...last, ...parts });
  });
  return out;
}
