import api from './index';

// A project's issue tracker (Jira project or Linear team), routes/trackers.py.
// Credentials live on the Connectors page; this is the per-project link and
// the sync that imports issues as tasks.
export const getProjectTracker = (projectId) => api.get(`/trackers/projects/${projectId}`);
export const setProjectTracker = (projectId, data) => api.put(`/trackers/projects/${projectId}`, data);
export const syncProjectTracker = (projectId) => api.post(`/trackers/projects/${projectId}/sync`);
export const listTrackerProjects = (provider) => api.get(`/trackers/${provider}/projects`);
