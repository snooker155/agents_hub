/**
 * The hub link (hublink/, docs/hub-link.md): this hub's attachment to another
 * hub, and the nodes attached to this one.
 */
import api from './index';

// { linked, link: {url, api_key (masked), name, use_models, use_agents, mirror,
//   target_workspace, node: {enabled, token (masked), share_models, sandbox,
//   shared_providers}, connection, linked_at, last_sync_at, last_error},
//   outbox: {pending, by_kind, last_error}, runtime: {drainer, node: {...}} }
export const getHubLink = () => api.get('/hublink');
// Create or change the link. A blank api_key keeps the stored one when the
// URL is unchanged; node.token likewise.
export const saveHubLink = (data) => api.put('/hublink', data);
export const unlinkHub = () => api.delete('/hublink');
// Send the outbox now and say hello as a node: { drained: {sent, failed}, hello }.
export const syncHubLink = () => api.post('/hublink/sync');

// Nodes of this hub. A node token is returned by create and rotate only.
export const listNodes = () => api.get('/nodes');
export const createNode = (data) => api.post('/nodes', data);
export const deleteNode = (id) => api.delete(`/nodes/${encodeURIComponent(id)}`);
export const rotateNodeToken = (id) => api.post(`/nodes/${encodeURIComponent(id)}/rotate`);
// Any of { trust: 'untrusted'|'trusted' (admin only), max_parallel: 1..64|null,
// workspaces: [..]|null, disabled: bool }.
export const updateNode = (id, data) => api.patch(`/nodes/${encodeURIComponent(id)}`, data);
// The pool of nodes: { backend_id, sandbox_provider, models, online, capacity,
// in_flight, and with `workspace`: workspace, data_class, eligible, excluded }.
export const getNodePool = (params) => api.get('/nodes/pool', { params });
