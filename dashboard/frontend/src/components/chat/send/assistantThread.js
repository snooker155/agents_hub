/**
 * A conversation with the Assistant, continued on the Chat page.
 *
 * Its record is the Assistant's own (routes/assistant.py, `assistant~<key>~<session>`):
 * `/api/chats` never writes it, so a turn goes where the Assistant page sends
 * its turns, `POST /api/assistant`, with the conversation's workspace and
 * mode. A past conversation is made the live one first
 * (`/api/entity-chats/sessions/activate`), which files the one that was live
 * among the past ones, as reopening it on the Assistant page does.
 *
 * Another agent picked for such a conversation does not write into it: the
 * message starts an ordinary conversation with that agent, which carries the
 * Assistant conversation's text as its history (`forkMessages`).
 */
import { activateEntityChatSession } from '../../../api/chat';
import { AssistantError, stopAssistant, streamAssistantTurn } from '../../../api/assistant';

export const ASSISTANT_AGENT_ID = 'assistant';
export const ASSISTANT_CHAT_KIND = 'assistant';

/** The thread key of an Assistant conversation id (`assistant~<key>~<session>`). */
export function threadKey(convId) {
  const rest = String(convId || '').replace(/^assistant~/, '');
  const cut = rest.lastIndexOf('~');
  return cut > 0 ? rest.slice(0, cut) : '';
}

export function isAssistantThread(conv) {
  return conv?.origin === 'assistant';
}

/** The Assistant conversation's text as an ordinary conversation's messages. */
export function forkMessages(messages) {
  return (messages || [])
    .filter((m) => (m.role === 'user' || m.role === 'assistant' || m.role === 'agent') && String(m.content || '').trim())
    .map((m, i) => ({
      id: `fork-${i}-${m.id || ''}`,
      role: m.role === 'user' ? 'user' : 'agent',
      content: String(m.content || ''),
      ...(m.role === 'user' ? {} : { agent_id: m.agent_id || ASSISTANT_AGENT_ID }),
      ...(m.voice ? { voice: true } : {}),
    }));
}

/** Mark `convId` as the live one of its thread, and its siblings as past. */
export function markLive(conversations, convId) {
  const key = threadKey(convId);
  return conversations.map((c) => {
    if (!isAssistantThread(c) || threadKey(c.id) !== key) return c;
    const active = c.id === convId;
    if (Boolean(c.assistant_thread?.active) === active) return c;
    return { ...c, assistant_thread: { ...(c.assistant_thread || {}), active } };
  });
}

/** The text an error of an Assistant turn is shown with. */
export function assistantErrorText(err, t) {
  if (err instanceof AssistantError) {
    if (err.code === 'busy') return t('chat.assistantThread.busy');
    if (err.code === 'budget') return err.message || t('chat.assistantThread.budget');
    return err.message;
  }
  return err?.message || String(err);
}

/**
 * Send one turn to an Assistant conversation. `ctx` is the agent stream
 * context the Chat page's handler writes into (send/handleAgentResponse).
 */
export async function sendAssistantTurn({ conv, text, voice, signal, onEvent }) {
  const mode = conv.assistant_thread?.mode || 'personal';
  if (!conv.assistant_thread?.active) {
    await activateEntityChatSession({ kind: ASSISTANT_CHAT_KIND, id: threadKey(conv.id) },
      conv.assistant_thread?.session_id);
  }
  await streamAssistantTurn({
    body: { message: text, workspace: conv.workspace || undefined, mode, ...(voice ? { voice: true } : {}) },
    signal,
    onEvent,
  });
}

/** Stop the running turn of an Assistant conversation. */
export function stopAssistantTurn(conv) {
  return stopAssistant(conv?.assistant_thread?.mode || 'personal').catch(() => {});
}
