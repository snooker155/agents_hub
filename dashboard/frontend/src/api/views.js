/**
 * Rich views: state, ops, checkpoints, clips and chat.
 */
import api from './index';

// Rich views (charts, tables, diagrams, …) produced by agents.
export const listViews = (params = {}) => api.get('/views', { params });
export const getView = (viewId) => api.get(`/views/${viewId}`);
export const setViewState = (viewId, state) => api.patch(`/views/${viewId}/state`, { state });
export const deleteView = (viewId) => api.delete(`/views/${viewId}`);
// A slides view as a .pptx (views/slides_pptx.py), as a Blob through the authenticated client.
export const exportViewPptx = (viewId) => api.get(`/views/${viewId}/export/pptx`, { responseType: 'blob' });
export const viewAssetUrl = (viewId, path) =>
  `${api.defaults.baseURL}/views/${viewId}/assets/${String(path).split('/').map(encodeURIComponent).join('/')}`;
// Visualization Studio: live views built by the visualizer agent via ops.
export const createStudioView = (kind, title, workspace) => api.post('/views/studio', { kind, title, workspace });
export const getViewOps = (viewId, afterSeq = 0) => api.get(`/views/${viewId}/ops`, { params: { after_seq: afterSeq } });
export const applyViewOps = (viewId, ops) => api.post(`/views/${viewId}/ops`, { ops });
export const revertView = (viewId, seq) => api.post(`/views/${viewId}/revert`, { seq });
export const revertViewToCheckpoint = (viewId, name) => api.post(`/views/${viewId}/revert`, { checkpoint: name });
export const getViewCheckpoints = (viewId) => api.get(`/views/${viewId}/checkpoints`);
export const saveViewCheckpoint = (viewId, name) => api.post(`/views/${viewId}/checkpoints`, { name });
export const viewProxyUrl = (viewId) => `${api.defaults.baseURL}/views/${viewId}/proxy/`;
export const saveViewSnapshot = (viewId, dataUrl) => api.post(`/views/${viewId}/snapshot`, { data_url: dataUrl });
export const getViewClips = (viewId) => api.get(`/views/${viewId}/clips`);
export const getViewClip = (viewId, name) => api.get(`/views/${viewId}/clips/${encodeURIComponent(name)}`);
// The Studio build chat: the Visualizer pinned to one view, stored server-side
// like every other entity chat so the floating page-chat panel can host it.
// The streaming turn goes through `streamEntityChat`.
export const getViewChat = (viewId) => api.get(`/views/${viewId}/chat`);
export const clearViewChat = (viewId) => api.delete(`/views/${viewId}/chat`);
export const stopViewChat = (viewId) => api.post(`/views/${viewId}/chat/stop`);
export const viewChatUrl = (viewId) => `/views/${viewId}/chat`;
