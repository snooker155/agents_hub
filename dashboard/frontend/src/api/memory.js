/**
 * Shared memory, memory chat, blocks, notes, files, episodes and graph.
 */
import api from './index';

// Shared Memory
export const getSharedMemories = (workspace) => api.get('/shared-memory', { params: workspace ? { workspace } : {} });
export const createSharedMemory = (data) => api.post('/shared-memory', data);
export const getSharedMemory = (id) => api.get(`/shared-memory/${id}`);
export const deleteSharedMemory = (id) => api.delete(`/shared-memory/${id}`);
export const getRagConfig = () => api.get('/shared-memory/rag-config');
// The Memory Agent's chat — workspace-scoped, because the agent's pool binding
// is resolved per workspace rather than per pool row.
// `memory_id` is the pool open on the page: it keys the transcript and binds the
// agent's memory tools to that pool for the turn.
const memoryChatParams = (workspace, memoryId) => {
  const params = {};
  if (workspace) params.workspace = workspace;
  if (memoryId) params.memory_id = memoryId;
  return params;
};
export const getMemoryChat = (workspace, memoryId) =>
  api.get('/shared-memory/chat', { params: memoryChatParams(workspace, memoryId) });
export const clearMemoryChat = (workspace, memoryId) =>
  api.delete('/shared-memory/chat', { params: memoryChatParams(workspace, memoryId) });
export const stopMemoryChat = (workspace, memoryId) =>
  api.post('/shared-memory/chat/stop', null, { params: memoryChatParams(workspace, memoryId) });
export const memoryChatUrl = (workspace, memoryId) => {
  const qs = new URLSearchParams(memoryChatParams(workspace, memoryId)).toString();
  return '/shared-memory/chat' + (qs ? `?${qs}` : '');
};
// Core memory blocks — always-in-context text rendered into the agent's prompt.
export const listMemoryBlocks = (id) => api.get(`/shared-memory/${id}/blocks`);
export const upsertMemoryBlock = (id, name, data) =>
  api.put(`/shared-memory/${id}/blocks/${encodeURIComponent(name)}`, data);
export const deleteMemoryBlock = (id, name) =>
  api.delete(`/shared-memory/${id}/blocks/${encodeURIComponent(name)}`);
export const addMemoryNote = (id, data) => api.post(`/shared-memory/${id}/notes`, data);
// A note in the caller's own personal pool, created on first use.
export const addPersonalMemoryNote = (data) => api.post('/shared-memory/personal/notes', data);
export const updateMemoryNote = (id, noteId, data) => api.put(`/shared-memory/${id}/notes/${noteId}`, data);
export const deleteMemoryNote = (id, noteId) => api.delete(`/shared-memory/${id}/notes/${noteId}`);
export const upsertMemoryStructuredSlot = (id, slot, data) => api.put(`/shared-memory/${id}/structured/${encodeURIComponent(slot)}`, data);
export const deleteMemoryStructuredSlot = (id, slot) => api.delete(`/shared-memory/${id}/structured/${encodeURIComponent(slot)}`);
export const listMemoryFiles = (id, workspace) => api.get(`/shared-memory/${id}/files`, { params: { workspace } });
export const uploadMemoryFile = (id, workspace, file) => {
  const form = new FormData();
  form.append('workspace', workspace);
  form.append('file', file);
  return api.post(`/shared-memory/${id}/files/upload`, form, { headers: { 'Content-Type': 'multipart/form-data' } });
};
// fileRef: the file id from listMemoryFiles (file_id), or its name when it has none.
export const indexMemoryFile = (id, fileRef, workspace) => api.post(`/shared-memory/${id}/files/${encodeURIComponent(fileRef)}/index`, null, { params: { workspace } });
export const deindexMemoryFile = (id, fileRef) => api.delete(`/shared-memory/${id}/files/${encodeURIComponent(fileRef)}/index`);
export const deleteMemoryFile = (id, fileRef, workspace) => api.delete(`/shared-memory/${id}/files/${encodeURIComponent(fileRef)}`, { params: { workspace } });
export const listMemoryEpisodes = (id, params) => api.get(`/shared-memory/${id}/episodes`, { params });
export const getMemoryEpisodesStats = (id) => api.get(`/shared-memory/${id}/episodes/stats`);
export const deleteMemoryEpisode = (id, episodeId) => api.delete(`/shared-memory/${id}/episodes/${episodeId}`);

// Graph memory
export const getMemoryGraph = (id) => api.get(`/shared-memory/${id}/graph`);
export const getMemoryGraphStats = (id) => api.get(`/shared-memory/${id}/graph/stats`);
export const linkMemoryGraph = (id, data) => api.post(`/shared-memory/${id}/graph/link`, data);
export const deleteMemoryGraphNode = (id, nodeId) => api.delete(`/shared-memory/${id}/graph/nodes/${nodeId}`);
export const deleteMemoryGraphEdge = (id, edgeId) => api.delete(`/shared-memory/${id}/graph/edges/${edgeId}`);
export const extractMemoryGraph = (id, text) => api.post(`/shared-memory/${id}/graph/extract`, { text });
export const mergeMemoryGraphSlots = (id) => api.post(`/shared-memory/${id}/graph/merge-slots`);
export const mergeMemoryGraphNodes = (id, keepId, dropId) => api.post(`/shared-memory/${id}/graph/merge-nodes`, { keep_id: keepId, drop_id: dropId });
export const pruneMemoryGraphMirrors = (id, { dryRun = false } = {}) => api.post(`/shared-memory/${id}/graph/prune`, null, { params: { dry_run: dryRun } });
