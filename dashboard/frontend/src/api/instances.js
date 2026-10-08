/**
 * Instances and messages.
 */
import api from './index';

// Instances API — the live copies of agents. A run is what a copy did; an
// instance is the copy itself, and unlike a run it can still be written to
// after it has finished.
export const getInstances = (params) => api.get('/instances', { params });
export const getInstancesSummary = (params) => api.get('/instances/summary', { params });
export const getInstance = (instanceId) => api.get(`/instances/${instanceId}`);
export const getInstanceRuns = (instanceId, params) =>
  api.get(`/instances/${instanceId}/runs`, { params });
export const getInstanceTimeline = (instanceId, params) =>
  api.get(`/instances/${instanceId}/timeline`, { params });
export const getInstanceContext = (instanceId, params) =>
  api.get(`/instances/${instanceId}/context`, { params });
export const getInstanceInbox = (instanceId, params) =>
  api.get(`/instances/${instanceId}/inbox`, { params });
export const getInstanceLogs = (instanceId) => api.get(`/instances/${instanceId}/logs`);
export const messageInstance = (instanceId, data) =>
  api.post(`/instances/${instanceId}/message`, data);
export const stopInstance = (instanceId) => api.post(`/instances/${instanceId}/stop`);
export const renameInstance = (instanceId, label) =>
  api.patch(`/instances/${instanceId}`, { label });
export const deleteInstance = (instanceId) => api.delete(`/instances/${instanceId}`);

// Messages API (individual agent run logs)
export const getMessages = (params) => api.get('/messages', { params });
export const createMessage = (data) => api.post('/messages', data);
export const getMessage = (runId) => api.get(`/messages/${runId}`);
export const getMessageLogs = (runId) => api.get(`/messages/${runId}/logs`);
// The live tail of a run still in progress: what a finished run answers from
// its log and payloads, a running one can only answer from here.
export const getMessageLive = (runId) => api.get(`/messages/${runId}/live`);
export const getMessageInsights = (runId) => api.get(`/messages/${runId}/insights`);
export const stopMessage = (runId) => api.post(`/messages/${runId}/stop`);
export const deleteMessage = (runId, params) => api.delete(`/messages/${runId}`, { params });

// Resident instance actions — starting, stopping and steering the carrier
// process behind an instance (instances/carrier.py). A resident instance is
// what the agent page's Run button starts; everything here targets one by id.
export const startInstance = (data) => api.post('/instances', data);
export const restartInstance = (instanceId) => api.post(`/instances/${instanceId}/restart`);
export const interruptInstance = (instanceId) => api.post(`/instances/${instanceId}/interrupt`);
export const publishInstance = (instanceId) => api.post(`/instances/${instanceId}/publish`);
export const unpublishInstance = (instanceId) => api.delete(`/instances/${instanceId}/publish`);
// Inbound signing for a published instance. Write-only: an instance reports
// only `inbound_secret_configured`, never the value.
export const setInstanceInboundSecret = (instanceId, secret) => api.put(`/instances/${instanceId}/inbound-secret`, { secret });
export const clearInstanceInboundSecret = (instanceId) => api.delete(`/instances/${instanceId}/inbound-secret`);
export const getInstanceConnections = (instanceId) => api.get(`/instances/${instanceId}/connections`);
export const getInstanceCarriers = (instanceId) => api.get(`/instances/${instanceId}/carriers`);
export const updateInstanceInputs = (instanceId, data) => api.patch(`/instances/${instanceId}/inputs`, data);
export const getInstanceConversations = (instanceId) => api.get(`/instances/${instanceId}/conversations`);
export const getInstanceMessage = (instanceId, msgId) => api.get(`/instances/${instanceId}/messages/${msgId}`);
