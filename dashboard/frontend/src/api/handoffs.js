/**
 * Conversation handoff (chat/handoff.py, docs/handoffs.md): the agents an
 * agent may give the conversation to, and how much of it the receiver sees
 * by default. Separate from api/index.js because the whole surface belongs to
 * the handoff feature; the agent list the card picks from is re-exported so
 * the card imports from one place.
 */
import api from './index';

export { getAgents } from './index';

const agentPath = (agentId) => `/agents/${encodeURIComponent(agentId)}/handoffs`;

// { handoffs: [agent ids], handoff_history: "full" | "summary" | "last_n:<N>" | "none" }
export const getAgentHandoffs = (agentId) => api.get(agentPath(agentId));

// Only the keys present in `payload` change. Answers like the GET.
export const updateAgentHandoffs = (agentId, payload) => api.post(agentPath(agentId), payload);

/** `{kind, n}` for a stored history filter; `last_n` keeps its count. */
export const parseHistoryFilter = (value) => {
  const text = String(value || 'full').trim().toLowerCase();
  const match = /^last_n:(\d+)$/.exec(text);
  if (match) return { kind: 'last_n', n: Number(match[1]) };
  if (['full', 'summary', 'none'].includes(text)) return { kind: text, n: 0 };
  return { kind: 'full', n: 0 };
};

/** The stored spelling of a filter picked in the card. */
export const formatHistoryFilter = (kind, n) => (kind === 'last_n' ? `last_n:${n}` : kind);
