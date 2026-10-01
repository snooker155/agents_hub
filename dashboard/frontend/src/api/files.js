/**
 * Workspace files (docs/files.md): a file uploaded once and referenced by its
 * id in chat, memory, tasks and evals. Backend: dashboard/backend/routes/files.py.
 */
import api from './index';

// { workspace, files: [record], usage_bytes, limits: { max_file_bytes, max_workspace_bytes } }
export const listWorkspaceFiles = (workspace, { q, source, limit } = {}) =>
  api.get('/files', { params: { workspace, q: q || undefined, source: source || undefined, limit } });

// Record plus `deduplicated` (true when these bytes were already a file of
// the workspace, which is then what comes back).
// `path` (workspace-relative, `proj/docs/plan.md`) puts the file into the
// workspace folder at that path instead of the file store, where the agents'
// file tools see it; a file already there is replaced.
export const uploadWorkspaceFileObject = (workspace, file, { source, filename, path } = {}) => {
  const form = new FormData();
  if (filename) form.append('file', file, filename);
  else form.append('file', file);
  if (source) form.append('source', source);
  if (path) form.append('path', path);
  return api.post('/files', form, {
    params: { workspace },
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

// Register the files of the workspace folder (what agents wrote before the
// registry followed their tools, or what a shell wrote) and mark the records
// of gone files deleted. { workspace, added, updated, unchanged, removed, skipped: [{path, reason}] }
export const indexWorkspaceFiles = (workspace) =>
  api.post('/files/index', null, { params: { workspace }, timeout: 300000 });

export const getWorkspaceFileRecord = (fileId) => api.get(`/files/${encodeURIComponent(fileId)}`);

// { file_id, name, mime_type, kind: 'text' | 'pdf' | 'binary', text, truncated }
export const getWorkspaceFileText = (fileId, maxChars) =>
  api.get(`/files/${encodeURIComponent(fileId)}/text`, { params: { max_chars: maxChars } });

// The bytes as a Blob, through the authenticated client, so a preview or a
// download works in every auth mode without putting a credential in a URL.
export const getWorkspaceFileBlob = (fileId) =>
  api.get(`/files/${encodeURIComponent(fileId)}/content`, { responseType: 'blob' });

// { file_id, chats, memory_pools, tasks, eval_cases, produced_by?, total }
export const getWorkspaceFileUsage = (fileId) => api.get(`/files/${encodeURIComponent(fileId)}/usage`);

export const deleteWorkspaceFileObject = (fileId) => api.delete(`/files/${encodeURIComponent(fileId)}`);

// Add a workspace file to a memory pool: copied into the knowledge folder
// and indexed, remembering the file id for citations.
export const addMemoryFileFromWorkspace = (poolId, fileId) =>
  api.post(`/shared-memory/${poolId}/files/from-workspace`, { file_id: fileId });

// Replace a task's workspace files.
export const setTaskFileIds = (taskId, fileIds) => api.patch(`/tasks/${taskId}`, { file_ids: fileIds });

/** Save a Blob under `name` in the browser (a download the user asked for). */
export const saveBlobAs = (blob, name) => {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name || 'file';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

/** "12.3 KB" style size label. */
export const formatBytes = (n) => {
  const size = Number(n) || 0;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
};
