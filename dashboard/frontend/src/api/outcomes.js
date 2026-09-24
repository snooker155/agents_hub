// A task's outcome (dashboard/backend/routes/outcomes.py): the rubric an
// independent grader checks every finished run against, how many attempts
// the agent gets, and the gradings so far. gradeTaskOutcome grades the
// latest completed run on request and records it without relaunching.
import api from './index';

const base = (taskId) => `/tasks/${encodeURIComponent(taskId)}/outcome`;

export const getTaskOutcome = (taskId) => api.get(base(taskId));
export const setTaskOutcome = (taskId, body) => api.put(base(taskId), body);
export const deleteTaskOutcome = (taskId) => api.delete(base(taskId));
export const getOutcomeEvaluations = (taskId) => api.get(`${base(taskId)}/evaluations`);
export const gradeTaskOutcome = (taskId) => api.post(`${base(taskId)}/grade`);
