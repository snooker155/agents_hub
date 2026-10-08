/**
 * Loops and teams.
 */
import api from './index';

// Loops API — a flow re-run until an agent judges the exit criterion met
// (see loops/ and routes/loops.py).
export const getLoops = (workspace) =>
  api.get('/loops', { params: workspace ? { workspace } : {} });
export const createLoop = (data) => api.post('/loops', data);
export const getLoop = (id) => api.get(`/loops/${id}`);
export const updateLoop = (id, data) => api.put(`/loops/${id}`, data);
export const deleteLoop = (id) => api.delete(`/loops/${id}`);
export const estimateLoop = (id) => api.post(`/loops/${id}/estimate`);
// The loop's own build chat — same shape as the scenario's.
export const getLoopChat = (id) => api.get(`/loops/${id}/chat`);
export const clearLoopChat = (id) => api.delete(`/loops/${id}/chat`);
export const stopLoopChat = (id) => api.post(`/loops/${id}/chat/stop`);
export const loopChatUrl = (id) => `/loops/${id}/chat`;
export const startLoop = (id, data) => api.post(`/loops/${id}/run`, data || {});
export const getLoopRuns = (loopId) =>
  api.get('/loops/runs', { params: loopId ? { loop_id: loopId } : {} });
export const getLoopRun = (runId) => api.get(`/loops/runs/${runId}`);
export const getLoopIterations = (runId, since = 0) =>
  api.get(`/loops/runs/${runId}/iterations`, { params: { since } });
export const stopLoopRun = (runId) => api.post(`/loops/runs/${runId}/stop`);
export const resumeLoopRun = (runId) => api.post(`/loops/runs/${runId}/resume`);

// Teams API — a bounded roster of agents that know each other and talk
// (see teams/ and routes/teams.py).
export const getTeams = (workspace) =>
  api.get('/teams', { params: workspace ? { workspace } : {} });
export const createTeam = (data) => api.post('/teams', data);
export const getTeam = (id) => api.get(`/teams/${id}`);
export const updateTeam = (id, data) => api.put(`/teams/${id}`, data);
export const deleteTeam = (id) => api.delete(`/teams/${id}`);
export const getTeamBriefing = (id, agentId) =>
  api.get(`/teams/${id}/briefing`, { params: agentId ? { agent_id: agentId } : {} });
export const suggestTeamManifest = (agentId) => api.get(`/teams/manifest/${agentId}`);
// The team's own build chat — same shape as the loop's.
export const getTeamChat = (id) => api.get(`/teams/${id}/chat`);
export const clearTeamChat = (id) => api.delete(`/teams/${id}/chat`);
export const stopTeamChat = (id) => api.post(`/teams/${id}/chat/stop`);
export const teamChatUrl = (id) => `/teams/${id}/chat`;
export const estimateTeam = (id) => api.post(`/teams/${id}/estimate`);
export const startTeamRun = (id, data) => api.post(`/teams/${id}/run`, data || {});
export const getTeamRuns = (teamId) =>
  api.get('/teams/runs', { params: teamId ? { team_id: teamId } : {} });
export const getTeamRun = (runId) => api.get(`/teams/runs/${runId}`);
export const getTeamMessages = (runId, since = 0) =>
  api.get(`/teams/runs/${runId}/messages`, { params: { since } });
export const stopTeamRun = (runId) => api.post(`/teams/runs/${runId}/stop`);
export const resumeTeamRun = (runId) => api.post(`/teams/runs/${runId}/resume`);
