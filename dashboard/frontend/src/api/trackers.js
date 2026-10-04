import api from './index';

// A project's issue tracker (Jira project or Linear team), routes/trackers.py.
// Credentials live on the Connectors page (connectors/channels/store.py: the
// default workspace's credentials work everywhere, another workspace's own
// work only there), so listing a provider's projects needs that project's
// own workspace to resolve the right credentials.
export const getProjectTracker = (projectId) => api.get(`/trackers/projects/${projectId}`);
export const setProjectTracker = (projectId, data) => api.put(`/trackers/projects/${projectId}`, data);
export const syncProjectTracker = (projectId) => api.post(`/trackers/projects/${projectId}/sync`);
export const listTrackerProjects = (provider, workspace) =>
  api.get(`/trackers/${provider}/projects`, { params: workspace ? { workspace } : {} });
