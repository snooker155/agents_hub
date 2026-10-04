/**
 * The assistant (dashboard/backend/routes/assistant.py, docs/assistant.md):
 * one thread per person, `mode` picks an administrator's service thread.
 *
 * A turn streams like every entity chat, but goes through its own fetch so a
 * refusal keeps its status and code (402 `budget`, 409 `busy`): the page says
 * those aloud instead of showing a raw error. Voice is two more calls:
 * `transcribe` sends a recording as the request body, `speak` returns one
 * stretch of speech as a Blob (null for 204, nothing left to say).
 */
import api, { API_ORIGIN, authFetchHeaders, consumeSSE } from './index';

const params = (mode) => (mode && mode !== 'personal' ? { mode } : {});

export const getAssistant = (mode) => api.get('/assistant', { params: params(mode) });
export const clearAssistant = (mode) => api.delete('/assistant', { params: params(mode) });
export const stopAssistant = (mode) => api.post('/assistant/stop', null, { params: params(mode) });

/** A refusal from the assistant's routes: `status`, and `code` when the body had one. */
export class AssistantError extends Error {
  constructor(status, detail) {
    const message = typeof detail === 'string' ? detail : (detail?.message || `HTTP ${status}`);
    super(message);
    this.name = 'AssistantError';
    this.status = status;
    this.code = typeof detail === 'object' && detail ? detail.code || '' : '';
  }
}

async function refusal(response) {
  let detail = '';
  try {
    const body = await response.json();
    detail = body?.detail ?? body;
  } catch {
    try { detail = await response.text(); } catch { /* no body */ }
  }
  return new AssistantError(response.status, detail);
}

/** One turn: `{message, workspace, mode, voice}`; `onEvent` gets every event. */
export async function streamAssistantTurn({ body, onEvent, signal }) {
  const response = await fetch(`${API_ORIGIN}/api/assistant`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authFetchHeaders() },
    signal,
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) throw await refusal(response);
  await consumeSSE(response, onEvent);
}

/** A recording as text: `{text, language, run_id, cost_usd, consent}`. */
export async function transcribeRecording(blob, { mode, language, signal } = {}) {
  const query = new URLSearchParams({ ...params(mode), ...(language ? { language } : {}) });
  const response = await fetch(`${API_ORIGIN}/api/assistant/transcribe?${query}`, {
    method: 'POST',
    headers: { 'Content-Type': blob.type || 'audio/webm', ...authFetchHeaders() },
    signal,
    body: blob,
  });
  if (!response.ok) throw await refusal(response);
  return response.json();
}

/** Speech for `{run_id, text | approval_id | tool, agent, language, voice}`: a Blob, or null. */
export async function speakAssistant(body, { signal } = {}) {
  const response = await fetch(`${API_ORIGIN}/api/assistant/speak`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authFetchHeaders() },
    signal,
    body: JSON.stringify(body),
  });
  if (response.status === 204) return null;
  if (!response.ok) throw await refusal(response);
  return response.blob();
}
