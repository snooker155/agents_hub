/**
 * MCP servers, notifications, tools.
 */
import api from './index';

// Every call carries the workspace it is made from.
const inWorkspace = (workspace) => ({ params: workspace ? { workspace } : {} });

// ── MCP servers: tool collections somebody else runs ─────────────────────────
// Configured per workspace, so every call carries one. Credentials in headers
// and env arrive masked (last four characters) and may be sent straight back:
// the backend reads the masked form as "unchanged" rather than overwriting the
// stored value, which is what lets a form be saved without holding the secret.
// Only testMcpServer and listMcpServerTools actually connect to a server.
export const listMcpServers = (workspace) => api.get('/mcp/servers', inWorkspace(workspace));
export const createMcpServer = (data, workspace) =>
  api.post('/mcp/servers', data, inWorkspace(workspace));
export const updateMcpServer = (id, data, workspace) =>
  api.patch(`/mcp/servers/${encodeURIComponent(id)}`, data, inWorkspace(workspace));
export const deleteMcpServer = (id, workspace) =>
  api.delete(`/mcp/servers/${encodeURIComponent(id)}`, inWorkspace(workspace));
// Connect now and report every tool the server offers, including the ones the
// allowlist would filter out: the point of the button is to help write it.
export const testMcpServer = (id, workspace) =>
  api.post(`/mcp/servers/${encodeURIComponent(id)}/test`, null, inWorkspace(workspace));
export const listMcpServerTools = (id, workspace, refresh = false) =>
  api.get(`/mcp/servers/${encodeURIComponent(id)}/tools`,
          { params: { ...(workspace ? { workspace } : {}), ...(refresh ? { refresh: true } : {}) } });

// ── Notifications: outbound endpoints + alert rules ──────────────────────────
// Same masking convention as MCP servers: a webhook's secret comes back as its
// last four characters, and sending that back unchanged keeps the stored value.
export const listNotifyEndpoints = (workspace) => api.get('/notify/endpoints', inWorkspace(workspace));
export const createNotifyEndpoint = (data, workspace) =>
  api.post('/notify/endpoints', data, inWorkspace(workspace));
export const updateNotifyEndpoint = (id, data, workspace) =>
  api.patch(`/notify/endpoints/${encodeURIComponent(id)}`, data, inWorkspace(workspace));
export const deleteNotifyEndpoint = (id, workspace) =>
  api.delete(`/notify/endpoints/${encodeURIComponent(id)}`, inWorkspace(workspace));
export const testNotifyEndpoint = (id, workspace) =>
  api.post(`/notify/endpoints/${encodeURIComponent(id)}/test`, null, inWorkspace(workspace));

export const listNotifyRules = (workspace) => api.get('/notify/rules', inWorkspace(workspace));
export const createNotifyRule = (data, workspace) =>
  api.post('/notify/rules', data, inWorkspace(workspace));
export const updateNotifyRule = (id, data, workspace) =>
  api.patch(`/notify/rules/${encodeURIComponent(id)}`, data, inWorkspace(workspace));
export const deleteNotifyRule = (id, workspace) =>
  api.delete(`/notify/rules/${encodeURIComponent(id)}`, inWorkspace(workspace));
// With a workspace the MCP servers attached to it are listed as well.
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
