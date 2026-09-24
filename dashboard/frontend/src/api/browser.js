/**
 * The user-facing browser (feature 7b): a live view of an agent run's browser
 * session, input into it, and free sessions a person drives and can hand to
 * an agent. A module of its own, like api/system.js, because the whole surface
 * belongs to the Browser page and the Browser panel in LiveRunStream.
 * See docs/browser.md.
 */
import api from './index';

// {configured, url}: whether the hub has a browser service to talk to.
export const getBrowserStatus = () => api.get('/browser/status');

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
