/**
 * Notifications, tools.
 */
import api from './index';

// Every call carries the workspace it is made from.
const inWorkspace = (workspace) => ({ params: workspace ? { workspace } : {} });

// ── Notifications: alert rules ───────────────────────────────────────────────
export const listNotifyRules = (workspace) => api.get('/notify/rules', inWorkspace(workspace));
export const createNotifyRule = (data, workspace) =>
  api.post('/notify/rules', data, inWorkspace(workspace));
export const updateNotifyRule = (id, data, workspace) =>
  api.patch(`/notify/rules/${encodeURIComponent(id)}`, data, inWorkspace(workspace));
export const deleteNotifyRule = (id, workspace) =>
  api.delete(`/notify/rules/${encodeURIComponent(id)}`, inWorkspace(workspace));
export const getTools = (workspace) => api.get('/tools', inWorkspace(workspace));
export const getToolSource = (toolId) => api.get(`/tools/${encodeURIComponent(toolId)}/source`);
export const updateToolSource = (toolId, data) => api.put(`/tools/${encodeURIComponent(toolId)}/source`, data);

// Notifications API — user inbox fed by the plan scheduler
export const getNotifications = (params) => api.get('/plan/notifications', { params });
export const getNotificationsUnreadCount = (workspace) =>
  api.get('/plan/notifications/unread-count', { params: workspace ? { workspace } : {} });
export const markNotificationRead = (id, read = true) =>
  api.post(`/plan/notifications/${id}/read`, null, { params: { read } });
export const markAllNotificationsRead = (workspace) =>
  api.post('/plan/notifications/read-all', null, { params: workspace ? { workspace } : {} });
export const deleteNotification = (id) => api.delete(`/plan/notifications/${id}`);
