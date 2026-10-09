/**
 * Cluster members, service chat and services.
 */
import api from './index';

// The cluster map: members (replicas and workers), leases, the launch
// queue and where runs, instances and containers live (docs/deployment.md).
// `/api/deployment` still answers the same document as an alias.
export const getCluster = () => api.get('/cluster');
export const getMemberLogs = (memberId, tail = 500) =>
  api.get(`/cluster/members/${encodeURIComponent(memberId)}/logs`, { params: { tail } });
export const forgetMember = (memberId) =>
  api.delete(`/cluster/members/${encodeURIComponent(memberId)}`);
export const getServiceChat = () => api.get('/health/chat');
export const clearServiceChat = () => api.delete('/health/chat');
export const stopServiceChat = () => api.post('/health/chat/stop');
export const serviceChatUrl = () => '/health/chat';

// Services API — agents kept running as replicas, and the runner every chat
// turn goes to (docs/services.md). A service is the desired state; its
// replicas are resident instances carrying its id.
export const getServices = (params) => api.get('/services', { params });
// Where a chat turn for the agent (or any agent, without one) in the
// workspace would run, and whether it can: the warning beside the agent
// picker and on the Services page.
export const getChatRoute = (params) => api.get('/services/chat-route', { params });
export const getService = (serviceId) => api.get(`/services/${serviceId}`);
export const createService = (data) => api.post('/services', data);
export const updateService = (serviceId, data) => api.patch(`/services/${serviceId}`, data);
export const pauseService = (serviceId) => api.post(`/services/${serviceId}/pause`);
export const resumeService = (serviceId) => api.post(`/services/${serviceId}/resume`);
export const deleteService = (serviceId) => api.delete(`/services/${serviceId}`);
export const getServiceReplicas = (serviceId, params) => api.get(`/services/${serviceId}/replicas`, { params });
export const addServiceReplica = (serviceId) => api.post(`/services/${serviceId}/replicas`);
export const getServiceEvents = (serviceId, params) => api.get(`/services/${serviceId}/events`, { params });
export const getServiceConversations = (serviceId) => api.get(`/services/${serviceId}/conversations`);
export const messageService = (serviceId, data) => api.post(`/services/${serviceId}/message`, data);
export const getServiceMessage = (serviceId, msgId) => api.get(`/services/${serviceId}/messages/${msgId}`);
export const publishService = (serviceId) => api.post(`/services/${serviceId}/publish`);
export const unpublishService = (serviceId) => api.delete(`/services/${serviceId}/publish`);
export const setServiceInboundSecret = (serviceId, secret) => api.put(`/services/${serviceId}/inbound-secret`, { secret });
export const clearServiceInboundSecret = (serviceId) => api.delete(`/services/${serviceId}/inbound-secret`);
export const getServiceConnections = (serviceId) => api.get(`/services/${serviceId}/connections`);
