/**
 * Eval sets, runs and prompt suggestions.
 */
import api from './index';

// Evals API — datasets, sweeps, score matrices (see evals/ and routes/evals.py)
export const getEvalSets = (workspace) =>
  api.get('/evals', { params: workspace ? { workspace } : {} });
export const createEvalSet = (data) => api.post('/evals', data);
export const getEvalSet = (id) => api.get(`/evals/${id}`);
export const updateEvalSet = (id, data) => api.put(`/evals/${id}`, data);
export const deleteEvalSet = (id) => api.delete(`/evals/${id}`);
// The Eval Agent's chat — workspace-scoped, because the first thing anyone
// wants is a set that does not exist yet.
export const getEvalChat = (workspace) =>
  api.get('/evals/chat', { params: workspace ? { workspace } : {} });
export const clearEvalChat = (workspace) =>
  api.delete('/evals/chat', { params: workspace ? { workspace } : {} });
export const stopEvalChat = (workspace) =>
  api.post('/evals/chat/stop', null, { params: workspace ? { workspace } : {} });
export const evalChatUrl = (workspace) =>
  '/evals/chat' + (workspace ? `?workspace=${encodeURIComponent(workspace)}` : '');
export const addEvalCase = (id, data) => api.post(`/evals/${id}/cases`, data);
export const deleteEvalCase = (id, caseId) => api.delete(`/evals/${id}/cases/${caseId}`);
export const estimateEvalRun = (id, data) => api.post(`/evals/${id}/estimate`, data);
// A sweep is len(cases) x len(configs) LLM calls -- it can take minutes, so the
// default axios timeout does not apply here.
export const runEvalSet = (id, data) => api.post(`/evals/${id}/run`, data, { timeout: 0 });
export const getEvalRuns = (id) => api.get(`/evals/${id}/runs`);
export const getEvalRun = (runId) => api.get(`/eval-runs/${runId}`);
export const getEvalRunDiff = (runAId, runBId) =>
  api.get(`/evals/runs/${runAId}/diff/${runBId}`);
export const getEvalGraders = () => api.get('/eval-graders');
// "To eval case" on any run: which eval sets fit it (same target kind), plus
// what kind of run it is and whether it failed.
export const getEvalSetsForRun = (runId) => api.get(`/evals/for-run/${runId}`);
// Prompt suggestion from an eval run's failed cases (evals/prompt_suggest.py).
// Suggesting is a real model call, billable; apply/dismiss are free, except
// applying with rerun: true, which sweeps the set again.
export const suggestPromptFix = (evalRunId) =>
  api.post(`/eval-runs/${evalRunId}/suggest-prompt`, null, { timeout: 0 });
export const getPromptSuggestions = (evalRunId) => api.get(`/eval-runs/${evalRunId}/suggestions`);
export const applyPromptSuggestion = (id, data) =>
  api.post(`/prompt-suggestions/${id}/apply`, data, { timeout: 0 });
export const dismissPromptSuggestion = (id) => api.post(`/prompt-suggestions/${id}/dismiss`);
