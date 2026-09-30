/**
 * The user-facing browser (feature 7b): a live view of an agent run's browser
 * session, input into it, and free sessions a person drives and can hand to
 * an agent. A module of its own, like api/system.js, because the whole surface
 * belongs to the Browser page and the Browser panel in LiveRunStream.
 * See docs/browser.md.
 */
import api, { API_ORIGIN, getAuthToken, withAuthTicket } from './index';

// {configured, url}: whether the hub has a browser service to talk to.
export const getBrowserStatus = () => api.get('/browser/status');

// Settings → Browser: the service configured and run from the hub
// (common/browser_service.py, docs/browser.md "Setting it up").
export const getBrowserService = () => api.get('/browser/service');
// {url?, token?, mode?: 'local'|'container', generate_token?}: writes .env and applies at once.
export const configureBrowserService = (body) => api.put('/browser/service/config', body);
export const startBrowserService = () => api.post('/browser/service/start');
export const stopBrowserService = () => api.post('/browser/service/stop');
export const installBrowserChromium = () => api.post('/browser/service/install-chromium');
export const getBrowserServiceJob = (id) => api.get(`/browser/service/jobs/${encodeURIComponent(id)}`);
export const getBrowserServiceLog = (tail = 200) => api.get('/browser/service/log', { params: { tail } });

// {sessions: [{session_id, run_id, workspace, owner, label, url, title, ...}]}
export const listBrowserSessions = (workspace) =>
  api.get('/browser/sessions', { params: workspace ? { workspace } : {} });

// A session a person drives, under the workspace's domain policy.
// Returns {session_id, url, title}.
export const createBrowserSession = (workspace, url) =>
  api.post('/browser/sessions', { workspace, url: url || '' });

export const getBrowserSession = (id) => api.get(`/browser/sessions/${encodeURIComponent(id)}`);

export const closeBrowserSession = (id) => api.delete(`/browser/sessions/${encodeURIComponent(id)}`);

// {url, title, width, height, image}: image is a JPEG data URL of the viewport.
export const getBrowserFrame = (id) => api.get(`/browser/sessions/${encodeURIComponent(id)}/frame`);

// One input in viewport pixels: {kind: click|dblclick|mousemove|type|key|scroll|
// navigate|back|forward|reload, x, y, text, key, dx, dy, url}.
export const sendBrowserInput = (id, input) =>
  api.post(`/browser/sessions/${encodeURIComponent(id)}/input`, input);

// The session an agent run is on; 404 when it has none.
export const getRunBrowserSession = (runId) =>
  api.get(`/browser/runs/${encodeURIComponent(runId)}/session`);

// Hand a session to an agent as a task. Returns {task_id, run_id}.
export const handoffBrowserSession = (id, { agentId, message, workspace }) =>
  api.post(`/browser/sessions/${encodeURIComponent(id)}/handoff`,
    { agent_id: agentId, message: message || '', workspace: workspace || '' });

// Take (on) or release (off) control of a session. While held, the agent's
// browser tools wait; the hold lapses when the person stops watching.
export const setBrowserControl = (id, on) =>
  api.post(`/browser/sessions/${encodeURIComponent(id)}/control`, { on: !!on });

// The WebSocket the hub relays the service's frame stream on. A browser
// cannot put a header on a WebSocket, so the credential rides the query
// string: browserStreamUrlWithTicket puts a one-time ticket there (the only
// form AUTH_MODE=multi accepts), browserStreamUrl the legacy token.
const streamBase = (id) => {
  const origin = API_ORIGIN || (typeof window !== 'undefined' ? window.location.origin : '');
  const url = new URL(`${origin}/api/browser/sessions/${encodeURIComponent(id)}/ws`);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url;
};

export const browserStreamUrl = (id) => {
  const url = streamBase(id);
  const token = getAuthToken();
  if (token) url.searchParams.set('token', token);
  return url.toString();
};

export const browserStreamUrlWithTicket = (id) => withAuthTicket(streamBase(id).toString());
