/**
 * The assistant (dashboard/backend/routes/assistant.py, docs/assistant.md):
 * one thread per person, `mode` picks an administrator's service thread,
 * `workspace` is where the next turn runs (it decides the voice models).
 *
 * A turn streams like every entity chat, but goes through its own fetch so a
 * refusal keeps its status and code (402 `budget`, 409 `busy`): the page says
 * those aloud instead of showing a raw error. Voice is two more calls:
 * `transcribe` sends a recording as the request body, `speak` returns one
 * stretch of speech as a Blob (null for 204, nothing left to say).
 */
import api, { API_ORIGIN, authFetchHeaders, consumeSSE } from './index';

const params = (mode) => (mode && mode !== 'personal' ? { mode } : {});

export const getAssistant = (mode, workspace) => api.get('/assistant', {
  params: { ...params(mode), ...(workspace ? { workspace } : {}) },
});
export const clearAssistant = (mode) => api.delete('/assistant', { params: params(mode) });
/** Clear the transcript: the conversation in progress is dropped, not kept among the past ones. */
export const forgetAssistantConversation = (mode) => api.delete('/assistant/conversation', { params: params(mode) });
export const stopAssistant = (mode) => api.post('/assistant/stop', null, { params: params(mode) });

/** A refusal from the assistant's routes: `status`, and `code` when the body had one. */
export class AssistantError extends Error {
  constructor(status, detail) {
    const message = typeof detail === 'string' ? detail : (detail?.message || `HTTP ${status}`);
    super(message);
    this.name = 'AssistantError';
    this.status = status;
    this.code = typeof detail === 'object' && detail ? detail.code || '' : '';
    // The structured refusal of a 402 (chat/refusals.py), for the RefusalCard.
    this.refusal = typeof detail === 'object' && detail ? detail.refusal || null : null;
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
/** The browser's IANA timezone, so "every morning at 8" is the person's own morning. */
function browserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  } catch {
    return '';
  }
}

export async function streamAssistantTurn({ body, onEvent, signal }) {
  const response = await fetch(`${API_ORIGIN}/api/assistant`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authFetchHeaders() },
    signal,
    body: JSON.stringify({ timezone: browserTimezone(), ...body }),
  });
  if (!response.ok || !response.body) throw await refusal(response);
  await consumeSSE(response, onEvent);
}

/**
 * A recording as text: `{text, language, run_id, cost_usd, consent}`.
 * `purpose` says what the open microphone was listening for (`wake`, a
 * `monitor` for a spoken stop); it only names the cost run.
 */
export async function transcribeRecording(blob, { mode, workspace, language, purpose, signal } = {}) {
  const query = new URLSearchParams({
    ...params(mode), ...(workspace ? { workspace } : {}), ...(language ? { language } : {}),
    ...(purpose === 'wake' || purpose === 'monitor' ? { purpose } : {}),
  });
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

/**
 * A short line read with the turns' speech model and `voice`, in the voice's
 * own language or `language`: `{blob, language, text}`.
 */
export async function sampleAssistantVoice({ voice, language, workspace, mode }, { signal } = {}) {
  const response = await fetch(`${API_ORIGIN}/api/assistant/voice-sample`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authFetchHeaders() },
    signal,
    body: JSON.stringify({ voice: voice || '', language: language || '', workspace: workspace || '', ...params(mode) }),
  });
  if (!response.ok) throw await refusal(response);
  let text = '';
  try { text = decodeURIComponent(response.headers.get('X-Sample-Text') || ''); } catch { /* left out */ }
  return { blob: await response.blob(), language: response.headers.get('X-Sample-Language') || '', text };
}
