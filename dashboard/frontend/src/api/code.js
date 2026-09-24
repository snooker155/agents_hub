/**
 * Code views: the `kind: "code"` view envelope's own endpoints (version
 * history, diff, run, persisted run history, save to a project). Separate
 * from api/index.js because this whole surface belongs to the code panel
 * feature; everything else about a view (fetch, list, delete, snapshot) still
 * goes through the shared `getView` / `listViews` in api/index.js.
 */
import api from './index';

// Version history for a code view, oldest to newest as the backend returns
// them: [{version, body, author: "agent"|"user", note, created_at}].
export const getCodeVersions = (viewId) => api.get(`/views/${viewId}/code/versions`);

// Save the edited body as a new version (author "user" on the backend).
// Returns the updated view envelope.
export const saveCodeVersion = (viewId, body, note = '') =>
  api.post(`/views/${viewId}/code/versions`, { body, note });

// Unified diff between two versions of the same view.
export const getCodeDiff = (viewId, a, b) =>
  api.get(`/views/${viewId}/code/diff`, { params: { a, b } });

// Run the current (or a given) body in the sandbox.
export const runCode = (viewId, { body, mountWorkspace } = {}) =>
  api.post(`/views/${viewId}/code/run`, {
    ...(body != null ? { body } : {}),
    ...(mountWorkspace != null ? { mount_workspace: mountWorkspace } : {}),
  });

// Past runs of this code view, newest first.
export const getCodeRuns = (viewId) => api.get(`/views/${viewId}/code/runs`);

// Write the current body to a path inside a project's workspace.
export const saveCodeToProject = (viewId, projectId, path, overwrite = false) =>
  api.post(`/views/${viewId}/code/save`, { project_id: projectId, path, overwrite });
