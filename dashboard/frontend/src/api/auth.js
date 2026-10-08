/**
 * Identity, sessions, API keys, groups, audit log, workspace secrets and membership.
 */
import api, { API_ORIGIN } from './index';

// Identity API (see docs/identity.md and dashboard/backend/routes/auth.py).
// `getAuthMode` is public in every mode and is what the frontend renders from.
export const getAuthMode = () => api.get('/auth/mode');
export const authBootstrap = (data) => api.post('/auth/bootstrap', data);
export const authLogin = (data) => api.post('/auth/login', data);
export const authLogout = () => api.post('/auth/logout');
export const getMe = () => api.get('/auth/me');
export const getUsers = () => api.get('/auth/users');
export const createUser = (data) => api.post('/auth/users', data);
export const updateUser = (id, data) => api.patch(`/auth/users/${id}`, data);
// Spend limit per person (common/user_budget.py): every person's limit and
// month spend plus the hub default (admin), and the caller's own.
export const getSpendLimits = () => api.get('/auth/spend-limits');
export const setDefaultSpendLimit = (limitUsd) => api.put('/auth/spend-limits/default', { limit_usd: limitUsd });
export const getOwnSpend = () => api.get('/auth/spend');
export const deleteUser = (id) => api.delete(`/auth/users/${id}`);
export const resetUserPassword = (id, password) =>
  api.post(`/auth/users/${id}/password`, { password });

// Workspace membership: who may read, write or administer one workspace.
export const getWorkspaceMembers = (name) => api.get(`/workspaces/${name}/members`);

// ── Stage 3 identity (docs/identity.md) ──────────────────────────────────────
// Single sign-on: the browser is sent to /api/auth/oidc/start (a redirect to
// the provider) and comes back at /login/oidc with the session in the URL
// fragment; see pages/OidcCallback.jsx.
export const oidcStartUrl = (next = '') =>
  `${API_ORIGIN}/api/auth/oidc/start${next ? `?next=${encodeURIComponent(next)}` : ''}`;

// The viewer's own account: sessions and personal API keys (routes/account.py).
export const getMySessions = () => api.get('/auth/sessions');
export const revokeMySession = (id) => api.delete(`/auth/sessions/${id}`);
export const revokeOtherSessions = () => api.post('/auth/sessions/revoke-others');
export const changeMyPassword = (current_password, password) =>
  api.post('/auth/password', { current_password, password });
export const getMyApiKeys = () => api.get('/auth/keys');
export const createMyApiKey = (data) => api.post('/auth/keys', data);
export const revokeMyApiKey = (id) => api.delete(`/auth/keys/${id}`);
// Administrators: every key of one account.
export const getUserApiKeys = (userId) => api.get(`/auth/users/${userId}/keys`);
export const revokeUserApiKey = (userId, keyId) => api.delete(`/auth/users/${userId}/keys/${keyId}`);

// Groups and the rules that turn a group into a role (routes/groups.py).
export const getGroups = () => api.get('/auth/groups');
export const createGroup = (data) => api.post('/auth/groups', data);
export const deleteGroup = (id) => api.delete(`/auth/groups/${id}`);
export const getGroupMembers = (id) => api.get(`/auth/groups/${id}/members`);
export const setGroupMembers = (id, user_ids) => api.put(`/auth/groups/${id}/members`, { user_ids });
export const getGroupMappings = () => api.get('/auth/group-mappings');
export const createGroupMapping = (data) => api.post('/auth/group-mappings', data);
export const deleteGroupMapping = (id) => api.delete(`/auth/group-mappings/${id}`);

// The audit trail (routes/audit.py). `params`: actor, action, workspace,
// object_type, object_id, since, until, text, result, limit, offset.
export const getAuditLog = (params) => api.get('/audit', { params });
export const getAuditActions = () => api.get('/audit/actions');
// The export URL carries no credential: the page downloads it through
// navigateWithAuthTicket, which adds a one-time ticket (the response is an
// attachment, so the page stays where it is).
export const auditExportUrl = (params, format = 'csv') => {
  const query = new URLSearchParams({ ...(params || {}), format });
  return `${API_ORIGIN}/api/audit/export?${query.toString()}`;
};

// Workspace secrets (routes/secrets.py): names and hints only, never values.
export const getWorkspaceSecrets = (name) => api.get(`/workspaces/${name}/secrets`);
export const setWorkspaceSecret = (name, secret, data) =>
  api.put(`/workspaces/${name}/secrets/${encodeURIComponent(secret)}`, data);
export const deleteWorkspaceSecret = (name, secret, params) =>
  api.delete(`/workspaces/${name}/secrets/${encodeURIComponent(secret)}`, { params });
// Where a secret may be sent (common/secrets.py "Secrets bound to hosts").
export const setWorkspaceSecretHosts = (name, secret, data) =>
  api.put(`/workspaces/${name}/secrets/${encodeURIComponent(secret)}/hosts`, data);
// An agent's allowlist: the secret names a run of it may receive.
export const getAgentSecrets = (id) => api.get(`/agents/${id}/secrets`);
export const updateAgentSecrets = (id, secrets, extra = {}) =>
  api.put(`/agents/${id}/secrets`, { secrets, ...extra });
export const setWorkspaceMember = (name, data) => api.put(`/workspaces/${name}/members`, data);
export const removeWorkspaceMember = (name, userId) =>
  api.delete(`/workspaces/${name}/members/${userId}`);
