/**
 * Worlds, scenarios and simulation runs.
 */
import api, { API_ORIGIN, authFetchHeaders } from './index';
import { consumeSSE } from './chat';

// Playground API — multi-agent simulation (see playground/ and routes/playground.py)
// The catalogue is workspace-aware because authored worlds are in it: a
// workspace's own worlds sit alongside the shipped ones, which is the whole
// point of being able to build one.
export const getSimEnvironments = (workspace) =>
  api.get('/playground/environments', { params: workspace ? { workspace } : {} });

// Worlds — the user-authored environments (see playground/worlds.py).
export const getWorlds = (workspace) =>
  api.get('/playground/worlds', { params: workspace ? { workspace } : {} });
export const getWorld = (id) => api.get(`/playground/worlds/${id}`);
export const createWorld = (data) => api.post('/playground/worlds', data);
export const updateWorld = (id, data) => api.put(`/playground/worlds/${id}`, data);
// `force` deletes a world that scenarios are still cast in; without it the
// server refuses, because a scenario whose world is gone fails at Run.
export const deleteWorld = (id, force = false) =>
  api.delete(`/playground/worlds/${id}`, { params: force ? { force: true } : {} });
export const validateWorldDraft = (data) => api.post('/playground/worlds/validate', data);
export const getWorldTemplates = () => api.get('/playground/worlds/templates');
// Build a whole world from a plain-language description (the World Builder
// names the places, declares the values and writes the actions).
export const generateWorld = (data) => api.post('/playground/worlds/generate', data);
// The world's own build chat: transcript + rich replay trace, clearing it, and
// stopping an in-flight turn. The streaming turn goes through `streamEntityChat`.
export const getWorldChat = (id) => api.get(`/playground/worlds/${id}/chat`);
export const clearWorldChat = (id) => api.delete(`/playground/worlds/${id}/chat`);
export const stopWorldChat = (id) => api.post(`/playground/worlds/${id}/chat/stop`);
export const worldChatUrl = (id) => `/playground/worlds/${id}/chat`;
export const getScenarios = (workspace) =>
  api.get('/playground/scenarios', { params: workspace ? { workspace } : {} });
export const createScenario = (data) => api.post('/playground/scenarios', data);
export const getScenario = (id) => api.get(`/playground/scenarios/${id}`);
export const updateScenario = (id, data) => api.put(`/playground/scenarios/${id}`, data);
export const deleteScenario = (id) => api.delete(`/playground/scenarios/${id}`);
export const estimateScenario = (id) => api.post(`/playground/scenarios/${id}/estimate`);
// Build a whole scenario from a plain-language description (the Scenario
// Creator picks the environment, casts the roles and sets the limits).
export const generateScenario = (data) => api.post('/playground/scenarios/generate', data);
/**
 * The same build, narrated (SSE).
 *
 * Designing a scenario is a minute of tool calls, and a spinner cannot tell a
 * slow run from a stuck one. This streams the Creator's steps as they happen
 * and closes with one `result` frame carrying `outcome` — the scenario it
 * made, the limitations it ran into, or an error.
 *
 * @param {object} opts
 * @param {object} opts.body      same payload as `generateScenario`.
 * @param {function} opts.onEvent called with each parsed event object.
 * @param {AbortSignal} [opts.signal]
 */
export const streamGenerateScenario = async ({ body, onEvent, signal }) => {
  const response = await fetch(`${API_ORIGIN}/api/playground/scenarios/generate/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authFetchHeaders() },
    signal,
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    const detail = await response.text();
    throw new Error(detail || 'Failed to start scenario generation');
  }
  await consumeSSE(response, onEvent);
};
// The scenario's own build chat: transcript + rich replay trace, clearing it,
// and stopping an in-flight turn. The streaming turn itself goes through
// `streamEntityChat`.
export const getScenarioChat = (id) => api.get(`/playground/scenarios/${id}/chat`);
export const clearScenarioChat = (id) => api.delete(`/playground/scenarios/${id}/chat`);
export const stopScenarioChat = (id) => api.post(`/playground/scenarios/${id}/chat/stop`);
export const scenarioChatUrl = (id) => `/playground/scenarios/${id}/chat`;
export const startSimulation = (id, workspace) =>
  api.post(`/playground/scenarios/${id}/run`, null, { params: workspace ? { workspace } : {} });
// One scenario's run history, or — with no scenario id — every scenario's,
// which is what the history page lists. Runs come back newest first.
export const getSimRuns = (scenarioId, { workspace, limit } = {}) =>
  api.get('/playground/runs', {
    params: {
      ...(scenarioId ? { scenario_id: scenarioId } : {}),
      ...(workspace ? { workspace } : {}),
      ...(limit ? { limit } : {}),
    },
  });
export const getSimRun = (runId) => api.get(`/playground/runs/${runId}`);
export const getSimTicks = (runId, since = -1) =>
  api.get(`/playground/runs/${runId}/ticks`, { params: { since } });
export const stopSimulation = (runId) => api.post(`/playground/runs/${runId}/stop`);
export const resumeSimulation = (runId) => api.post(`/playground/runs/${runId}/resume`);
// Poke one agent in a running simulation from outside the world. In triggered
// mode this is what wakes them; in synchronous mode it is a message like any
// other, delivered on the next tick.
export const triggerSimAgent = (runId, agent, text) =>
  api.post(`/playground/runs/${runId}/trigger`, { agent, text });
// The run as one piece of prose: the chronicle is composed from the tick log
// on every request (free, exact, works mid-run), the narration is a model's
// retelling of it and is kept once written.
export const getSimStory = (runId, lang) =>
  api.get(`/playground/runs/${runId}/story`, { params: { lang } });
export const narrateSimStory = (runId, lang) =>
  api.post(`/playground/runs/${runId}/story/narrate`, null,
           { params: { lang }, timeout: 0 });
