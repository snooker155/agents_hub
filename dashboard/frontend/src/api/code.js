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

// Overwrite the current version's body in place (no new version).
// Returns the updated view envelope.
export const saveCodeBody = (viewId, body) =>
  api.put(`/views/${viewId}/code/body`, { body });

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

// A reply's block written into a project as a file, no view made of it.
export const saveSnippetToProject = ({ projectId, path, body, overwrite = false }) =>
  api.post('/views/code/save', { project_id: projectId, path, body, overwrite });

// A code view made from a fenced block of a chat reply, owned by that reply's
// run so the conversation's Code panel lists it. The same body under the same
// run returns the view made the first time.
export const createCodeFromReply = ({ body, language, filename, runId, workspace }) =>
  api.post('/views/code', {
    body,
    language: language || '',
    ...(filename ? { filename } : {}),
    ...(runId ? { run_id: runId } : {}),
    ...(workspace ? { workspace } : {}),
  });

// ── A reply's block on its own: run, versions and diff without a view ──────
// `key` is the panel's own name for the block (conversation, message, index),
// `workspace` the conversation's; the backend keeps the history beside the
// workspace's views (views/store.py, list_snippet_versions).

export const runSnippet = ({ language, body, mountWorkspace = false, workspace }) =>
  api.post('/views/code/run', {
    language: language || '', body, mount_workspace: mountWorkspace,
    ...(workspace ? { workspace } : {}),
  });

export const getSnippetVersions = (key, workspace) =>
  api.get('/views/code/snippet/versions', { params: { key, ...(workspace ? { workspace } : {}) } });

// `base` is the reply's own text, recorded as version 1 the first time.
export const saveSnippetVersion = ({ key, body, note = '', workspace, base }) =>
  api.post('/views/code/snippet/versions', {
    key, body, note, ...(workspace ? { workspace } : {}), ...(base != null ? { base } : {}),
  });

export const getSnippetDiff = (key, a, b, workspace) =>
  api.get('/views/code/snippet/diff', { params: { key, a, b, ...(workspace ? { workspace } : {}) } });
