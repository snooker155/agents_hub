import { useEffect, useRef } from 'react';
import { suggestChatTitle } from '../../api';
import { CONNECTOR_ORIGINS } from './connectorChats';

/**
 * A conversation is titled with its first message until its first answer is
 * in; then the chat agent's model names what it is about (chat/titles.py) and
 * the title replaces it. Asked once per conversation, by the tab whose turn
 * just ended, and saved with the conversation (`title_named` marks it done,
 * whether a title came or not). A connector's thread, the Assistant's and a
 * mirrored copy keep the titles their owners give them.
 */
export function namingRequest(conv) {
  if (!conv || conv.title_named) return null;
  if (CONNECTOR_ORIGINS.has(conv.origin) || conv.origin === 'assistant' || conv.origin === 'mirror') return null;
  const messages = conv.messages || [];
  const first = messages.find((m) => m.role === 'user' && String(m.content || '').trim());
  const reply = messages.find((m) => m.role === 'agent' && !m.error && String(m.content || '').trim());
  if (!first || !reply) return null;
  return { message: first.content, reply: reply.content, agentId: conv.agent_id || null };
}

export function useChatTitles({ turns, conversations, setConversations }) {
  const latest = useRef(conversations);
  useEffect(() => { latest.current = conversations; }, [conversations]);
  const seen = useRef(new Set());
  const asked = useRef(new Set());

  useEffect(() => {
    const now = new Set(Object.keys(turns || {}));
    const ended = [...seen.current].filter((id) => !now.has(id));
    seen.current = now;
    for (const id of ended) {
      if (asked.current.has(id)) continue;
      const request = namingRequest(latest.current.find((c) => c.id === id));
      if (!request) continue;
      asked.current.add(id);
      suggestChatTitle(request)
        .then(({ data }) => data?.title || null)
        .catch(() => null)
        .then((title) => setConversations((prev) => prev.map((c) => (c.id !== id ? c : {
          ...c, title_named: true, ...(title ? { title } : {}),
        }))));
    }
  }, [turns, setConversations]);
}

export default useChatTitles;
