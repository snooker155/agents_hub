/**
 * Scheduled plan jobs and cron preview.
 */
import api from './index';

// Plan API — scheduled jobs (future notifications / agent tasks)
// `kinds` narrows the listing to a comma list ('agent_task,flow,loop', or an
// array the same set of values) — what the Deployments page uses to leave
// plain notifications out of its table.
export const getPlanJobs = (workspace, status, kinds) =>
  api.get('/plan/jobs', { params: {
    ...(workspace ? { workspace } : {}),
    ...(status ? { status } : {}),
    ...(kinds ? { kinds: Array.isArray(kinds) ? kinds.join(',') : kinds } : {}),
  } });
export const createPlanJob = (data) => api.post('/plan/jobs', data);
export const getPlanJob = (id) => api.get(`/plan/jobs/${id}`);
export const updatePlanJob = (id, data) => api.patch(`/plan/jobs/${id}`, data);
export const deletePlanJob = (id) => api.delete(`/plan/jobs/${id}`);
export const pausePlanJob = (id) => api.post(`/plan/jobs/${id}/pause`);
export const resumePlanJob = (id) => api.post(`/plan/jobs/${id}/resume`);
export const cancelPlanJob = (id) => api.post(`/plan/jobs/${id}/cancel`);
export const runPlanJobNow = (id) => api.post(`/plan/jobs/${id}/run-now`);
// The firing journal: one record per attempt, newest first. Per job, or across
// every job in a workspace (the Deployments detail drawer and a future
// cross-job view respectively).
export const getJobFires = (id, params) => api.get(`/plan/jobs/${id}/fires`, { params });
export const getFires = (params) => api.get('/plan/fires', { params });
// Live preview of a cron field: the next few fire times, computed with the
// scheduler's own next-run logic (see plans/service.py upcoming_runs), so the
// hint shown while typing never disagrees with what the job will actually do.
export const previewCron = (cron, timezone, count = 3, start, recurrence = 'cron') =>
  api.get('/plan/cron/preview', { params: {
    cron,
    recurrence,
    ...(timezone ? { timezone } : {}),
    count,
    ...(start ? { start } : {}),
  } });
