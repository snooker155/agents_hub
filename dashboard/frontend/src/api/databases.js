import api from './index';

// Read-only database connections of a workspace, routes/databases.py. The
// DSN is write-only: a listing carries a `dsn_hint` (scheme and host) only.
// `includeDefault` adds the default workspace's connections, which every
// workspace's runs may use (docs/connectors.md "Connectors per workspace").
export const listDbConnections = (workspace, { includeDefault = false } = {}) =>
  api.get('/databases/connections', {
    params: workspace ? { workspace, ...(includeDefault ? { include_default: true } : {}) } : {},
  });
export const createDbConnection = (data) => api.post('/databases/connections', data);
export const updateDbConnection = (id, data) => api.patch(`/databases/connections/${id}`, data);
export const deleteDbConnection = (id) => api.delete(`/databases/connections/${id}`);
export const testDbConnection = (id) => api.post(`/databases/connections/${id}/test`);
export const getDbSchema = (id) => api.get(`/databases/connections/${id}/schema`);
export const runDbQuery = (id, sql) => api.post(`/databases/connections/${id}/query`, { sql });
