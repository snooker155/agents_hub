/**
 * Hub-wide reads: health, docs, logs, marketplace, web logs, orchestrator, stats, costs.
 */
import api from './index';

// System health snapshot: DB reachability + store counts, background-service
// liveness, on-disk state sizes, and agent build-cache hit/miss stats.
//
// Also serves as the "is the backend up?" probe. It deliberately goes through
// the shared `/api` client rather than the bare API root (`GET /`): that root
// banner was the only URL in the app outside `/api`, so it could fail on its
// own — behind a proxy that forwards just `/api`, or against a stale CORS
// config — and produce a browser CORS error nothing else in the app would hit.
export const getSystemHealth = () => api.get('/health');

// One document of the documentation corpus the agents read (docs/index.json),
// e.g. 'changelog'. Returns { id, title, content, lang, truncated }.
// lang defaults to the current UI language ('en', 'ru', or 'de'); the backend falls back to English.
export const getDoc = (id, lang = '') => api.get(`/docs/${id}`, { params: { ...(lang ? { lang } : {}) } });
// Service health, and the Service Agent's chat about it.
export const getHealth = () => api.get('/health');
export const getLogs = (runId) => api.get(`/logs/${runId}`);

// Marketplace — catalog of agents published across workspaces
export const getMarketplaceAgents = (workspace) =>
  api.get('/marketplace/agents', { params: workspace ? { workspace } : {} });
export const getMarketplaceAgent = (id, workspace) =>
  api.get(`/marketplace/agents/${encodeURIComponent(id)}`, { params: workspace ? { workspace } : {} });
export const getMarketplaceFlows = (workspace) =>
  api.get('/marketplace/flows', { params: workspace ? { workspace } : {} });
export const getMarketplaceSkills = (workspace) =>
  api.get('/marketplace/skills', { params: workspace ? { workspace } : {} });

// Web access log — recorded web_search / fetch_url calls, their responses and
// the security flags raised against them.
export const getWebLogs = (params) => api.get('/web-logs', { params });
export const getWebLogStats = () => api.get('/web-logs/stats');
export const getWebLogEntry = (id) => api.get(`/web-logs/${encodeURIComponent(id)}`);
export const clearWebLogs = () => api.delete('/web-logs');

// Orchestrator
export const getOrchestratorSettings = (workspace) =>
  api.get('/orchestrator/settings', { params: workspace ? { workspace } : {} });
export const updateOrchestratorSettings = (data, workspace) =>
  api.post('/orchestrator/settings', data, { params: workspace ? { workspace } : {} });
export const getOrchestratorRoutingLog = (workspace) =>
  api.get('/orchestrator/routing-log', { params: workspace ? { workspace } : {} });

// Stats
export const getStats = (workspace) => api.get('/stats', { params: { workspace } });
export const getStatsOverview = (workspace, days = 14) => api.get('/stats/overview', { params: { workspace, days } });

// Costs API — spend breakdowns + per-workspace budget caps
export const getCosts = (params) => api.get('/costs', { params });
export const getBudget = (workspace) => api.get('/costs/budget', { params: { workspace } });
export const setBudget = (workspace, data) => api.post('/costs/budget', data, { params: { workspace } });
