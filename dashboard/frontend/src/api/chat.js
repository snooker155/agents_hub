/**
 * Chat sessions, the chat SSE transport, entity and page chats, context pickers.
 */
import api, { API_ORIGIN, authFetchHeaders } from './index';

/**
 * Drain one `text/event-stream` response, calling `onEvent` per `data:` frame.
 *
 * Every streaming POST in this file speaks the same wire format — one JSON
 * object per `data: ` line, frames separated by a blank line — so the reading
 * of it lives here once. Frames that are not JSON, or carry no `type`, are
 * dropped: a stream is a best-effort narration and one malformed chunk must
 * not end the turn.
 */
export const consumeSSE = async (response, onEvent) => {
  const decoder = new TextDecoder();
  const reader = response.body.getReader();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() || '';
    for (const chunk of chunks) {
      const line = chunk.split('\n').map((l) => l.trim()).find((l) => l.startsWith('data: '));
      if (!line) continue;
      let event = null;
      try { event = JSON.parse(line.slice(6)); } catch { continue; }
      if (event && event.type) onEvent(event);
    }
  }
};

/**
 * Open the chat SSE stream (POST /api/chat/stream) and invoke `onEvent` for
 * every parsed event. Shared transport for every chat surface (the full Chat
 * page, the Session details composer, and the per-node Flow chat) so each only
 * supplies its request body + an event handler and keeps its own UI state.
 *
 * @param {object}   opts
 * @param {object}   opts.body      JSON body for the chat request.
 * @param {function} opts.onEvent   called with each parsed event object.
 * @param {AbortSignal} [opts.signal] optional abort signal.
 * @throws {Error} if the response is not OK (message = server detail text).
 */
export const streamChat = async ({ body, onEvent, signal }) => {
  const response = await fetch(`${API_ORIGIN}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authFetchHeaders() },
    signal,
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    const detail = await response.text();
    throw new Error(detail || 'Failed to open chat stream');
  }

  await consumeSSE(response, onEvent);
};

/**
 * Start a chat run whose events are delivered over the shared multiplexed SSE
 * (/api/stream) instead of a dedicated streaming response. Pass the caller's SSE
 * `client_id` in the body; the server subscribes that client to a per-conversation
 * channel and returns `{ channel, conversation_id }`. The caller listens on that
 * channel (see StreamContext) for the same event shapes `streamChat` yields.
 * Avoids holding a second long-lived connection per tab.
 */
export const startChatOverSSE = (body) => api.post('/chat/stream-sse', body);

// Stored conversations. The Chat page used to keep its history in localStorage,
// which tied a chat to one browser profile and capped it at the storage quota;
// it is a service record now, so the list comes from the server. Listing omits
// transcripts — a chat's messages arrive when it is opened.
export const listChats = (params) => api.get('/chats', { params });
export const getChat = (chatId) => api.get(`/chats/${chatId}`);
// The client id travels with a save so the server can name the writer when it
// announces it: every other tab with this chat open reloads, the writer does not.
export const saveChat = (chat, clientId) => api.put(`/chats/${chat.id}`, chat, {
  headers: clientId ? { 'X-Client-Id': clientId } : undefined,
});
export const deleteChat = (chatId) => api.delete(`/chats/${chatId}`);
export const importChats = (chats) => api.post('/chats/import', { chats });
// The turn a conversation is in the middle of, for a page that arrived after it
// started: what has been generated so far, to continue from on the live channel.
export const getChatLive = (chatId) => api.get(`/chats/${chatId}/live`);
// The conversations being answered right now, whoever started the turn: the
// Chat page marks them in its list (refetched on `chat_turns.changed`).
export const getRunningChats = () => api.get('/chats/running');

// Context references — what the chat composer can attach besides a file. The
// kind catalog and the per-kind candidate lists both come from the server so the
// picker always offers exactly what the prompt builder can render.
export const getContextKinds = () => api.get('/context/kinds');
export const getContextEntities = (kind, params) => api.get(`/context/${kind}`, { params });
export const getContextEntityPreview = (kind, id) => api.get(`/context/${kind}/${encodeURIComponent(id)}/preview`);
/**
 * Drive one turn of an entity build chat (a scenario's, a loop's) over SSE.
 *
 * The same wire format the project graph chat uses — `data: {json}` frames —
 * but the path is a parameter, because what differs between these chats is the
 * entity behind them, not the transport. POST returns the stream directly, so
 * unlike the multiplexed chat page this holds one connection for the turn.
 *
 * @param {object} opts
 * @param {string} opts.path      API path under /api (e.g. from `scenarioChatUrl`).
 * @param {string} opts.message   the user's turn.
 * @param {object} [opts.body]    extra fields for the turn's payload. The page
 *   chat sends what the user is looking at this way (scope, route, records);
 *   an entity chat, whose subject is fixed by its path, sends nothing.
 * @param {function} opts.onEvent called with each parsed event object.
 * @param {AbortSignal} [opts.signal]
 */
export const streamEntityChat = async ({ path, message, body = null, onEvent, signal }) => {
  const response = await fetch(`${API_ORIGIN}/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authFetchHeaders() },
    signal,
    body: JSON.stringify({ ...(body || {}), message }),
  });
  if (!response.ok || !response.body) {
    const detail = await response.text();
    throw new Error(detail || 'Failed to open the chat stream');
  }
  await consumeSSE(response, onEvent);
};

// Session history, shared by every entity build chat (routes/entity_chats.py).
// The ref comes from `chat_ref` on the chat's own GET response, so a page never
// has to know its chat's storage key. Activating a session swaps it in as the
// live thread, which is what lets a past conversation be carried on.
export const getEntityChatSessions = (ref) =>
  api.get('/entity-chats/sessions', { params: { kind: ref.kind, entity_id: ref.id } });
export const activateEntityChatSession = (ref, sessionId) =>
  api.post('/entity-chats/sessions/activate', {
    kind: ref.kind, entity_id: ref.id, session_id: sessionId,
  });
export const deleteEntityChatSession = (ref, sessionId) =>
  api.delete('/entity-chats/sessions', {
    params: { kind: ref.kind, entity_id: ref.id, session_id: sessionId },
  });

// The page chat — the floating panel that follows the user from page to page.
// One agent, one thread per `scope`, and the records the page is showing sent
// as pointers with the turn (see routes/page_chat.py). The streaming turn goes
// through `streamEntityChat` with those pointers as its extra body.
export const getPageChat = (scope) => api.get('/page-chat', { params: { scope } });
export const clearPageChat = (scope) => api.delete('/page-chat', { params: { scope } });
export const stopPageChat = (scope) => api.post('/page-chat/stop', null, { params: { scope } });
export const pageChatUrl = () => '/page-chat';
