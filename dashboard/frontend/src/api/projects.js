/**
 * Projects, project graph, tasks chat, repository files and deployments.
 */
import api, { API_ORIGIN, authFetchHeaders } from './index';
import { consumeSSE } from './chat';

// Projects API
export const getProjects = (workspace) => api.get('/projects', { params: workspace ? { workspace } : {} });
export const createProject = (data) => api.post('/projects', data);
export const getProject = (id) => api.get(`/projects/${id}`);
export const updateProject = (id, data) => api.put(`/projects/${id}`, data);
export const deleteProject = (id) => api.delete(`/projects/${id}`);
export const getProjectTasks = (id) => api.get(`/projects/${id}/tasks`);
// The project registry's own chat — workspace-scoped, unlike the per-project
// graph and task chats.
export const getProjectRegistryChat = (workspace) =>
  api.get('/projects/registry/chat', { params: workspace ? { workspace } : {} });
export const clearProjectRegistryChat = (workspace) =>
  api.delete('/projects/registry/chat', { params: workspace ? { workspace } : {} });
export const stopProjectRegistryChat = (workspace) =>
  api.post('/projects/registry/chat/stop', null, { params: workspace ? { workspace } : {} });
export const projectRegistryChatUrl = (workspace) =>
  '/projects/registry/chat' + (workspace ? `?workspace=${encodeURIComponent(workspace)}` : '');
export const cloneProjectRepo = (id) => api.post(`/projects/${id}/clone-repo`);
export const getProjectGitStatus = (id) => api.get(`/projects/${id}/git-status`);
export const pullProjectRepo = (id) => api.post(`/projects/${id}/git-pull`);
// Commit, push a branch and open a PR/MR: the same path the git_publish tool takes.
export const publishProjectBranch = (id, data) => api.post(`/projects/${id}/git/publish`, data);
export const getProjectSwaggerSpec = (id, baseUrl) => api.get(`/projects/${id}/swagger-spec`, { params: baseUrl ? { base_url: baseUrl } : {} });
export const getProjectSpecFromCode = (id) => api.get(`/projects/${id}/spec-from-code`);
export const proxyProjectApiRequest = (id, data) => api.post(`/projects/${id}/api-request`, data);
// Project deployments (docs/project-deployments.md): run the project's
// frontend and backend from inside the hub, watch them, share the link.
export const getProjectDeployment = (id, refresh = true) =>
  api.get(`/projects/${id}/deployment`, { params: { refresh } });
export const updateProjectDeployment = (id, data) => api.put(`/projects/${id}/deployment`, data);
export const detectProjectDeployment = (id) => api.post(`/projects/${id}/deployment/detect`);
export const deployProject = (id, build = true) =>
  api.post(`/projects/${id}/deployment/deploy`, null, { params: { build } });
export const restartProjectDeployment = (id) => api.post(`/projects/${id}/deployment/restart`);
export const stopProjectDeployment = (id) => api.post(`/projects/${id}/deployment/stop`);
export const removeProjectDeployment = (id) => api.delete(`/projects/${id}/deployment`);
export const getProjectDeploymentLogs = (id, service, tail = 200) =>
  api.get(`/projects/${id}/deployment/logs`, { params: { ...(service ? { service } : {}), tail } });
export const getProjectDeploymentEvents = (id, limit = 100) =>
  api.get(`/projects/${id}/deployment/events`, { params: { limit } });
export const setProjectDeploymentVisibility = (id, visibility) =>
  api.put(`/projects/${id}/deployment/visibility`, { visibility });
export const resetProjectDeploymentLink = (id) => api.post(`/projects/${id}/deployment/link/reset`);
export const listDeployedApps = (workspace) =>
  api.get('/deployments/apps', { params: workspace ? { workspace } : {} });

export const getProjectGraph = (id, view = 'architecture') => api.get(`/projects/${id}/graph`, { params: { view } });
export const saveProjectGraph = (id, view, data) => api.put(`/projects/${id}/graph`, data, { params: { view } });
export const resetProjectGraph = (id, view) => api.delete(`/projects/${id}/graph`, { params: { view } });
export const generateProjectGraph = (id, view) => api.post(`/projects/${id}/graph/generate`, null, { params: { view } });
export const projectGraphStreamUrl = (id, view) => `${api.defaults.baseURL}/projects/${id}/graph/generate/stream?view=${encodeURIComponent(view)}`;
export const relayoutProjectGraph = (id, view, data) => api.post(`/projects/${id}/graph/relayout`, data, { params: { view } });
export const getProjectGraphMessages = (id, view) => api.get(`/projects/${id}/graph/messages`, { params: { view } });
export const saveProjectGraphTrace = (id, view, trace) => api.put(`/projects/${id}/graph/trace`, { trace }, { params: { view } });
export const clearProjectGraphMessages = (id, view) => api.delete(`/projects/${id}/graph/messages`, { params: { view } });
export const stopProjectGraphChat = (id) => api.post(`/projects/${id}/graph/chat/stop`);

// Interactive graph build: POST a message, read the SSE stream of agent events
// (tool calls, live graph_node/graph_edge mutations, assistant reply).
export const streamProjectGraphChat = async ({ projectId, view, message, onEvent, signal }) => {
  const response = await fetch(`${API_ORIGIN}/api/projects/${projectId}/graph/chat?view=${encodeURIComponent(view)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authFetchHeaders() },
    signal,
    body: JSON.stringify({ message }),
  });
  if (!response.ok || !response.body) {
    const detail = await response.text();
    throw new Error(detail || 'Failed to open graph chat stream');
  }
  await consumeSSE(response, onEvent);
};

// Generate tasks from the project's structure views: POST and read the SSE
// stream of Planner-agent events (tool calls, thinking, final summary message).
// The planner always reads both graphs; its chat lives on the Tasks tab.
export const getProjectTasksChat = (id) => api.get(`/projects/${id}/tasks/chat`);
export const clearProjectTasksChat = (id) => api.delete(`/projects/${id}/tasks/chat`);
export const streamProjectTasksGenerate = async ({ projectId, message, onEvent, signal }) => {
  const response = await fetch(`${API_ORIGIN}/api/projects/${projectId}/tasks/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authFetchHeaders() },
    body: JSON.stringify({ message: message || null }),
    signal,
  });
  if (!response.ok || !response.body) {
    const detail = await response.text();
    throw new Error(detail || 'Failed to start task generation');
  }
  await consumeSSE(response, onEvent);
};
export const getProjectFiles = (id) => api.get(`/projects/${id}/files`);
// { path, size, content, kind: 'text' | 'pdf' | 'binary', mime_type, truncated }: read the
// way workspace files are, a PDF's text extracted.
// ``ref`` is { fileId } (the registry id the file list hands out) or a path
// string for a file the registry does not follow.
const projectFileParams = (ref) => (ref && typeof ref === 'object' ? { file_id: ref.fileId } : { path: ref });
export const getProjectFileContent = (id, ref) => api.get(`/projects/${id}/file-content`, { params: projectFileParams(ref) });
// The bytes as a Blob (an image, a PDF or an HTML page to render), through the
// authenticated client like api/files.js's getWorkspaceFileBlob.
export const getProjectFileBlob = (id, ref) =>
  api.get(`/projects/${id}/file-raw`, { params: projectFileParams(ref), responseType: 'blob' });
export const getProjectFileId = (id, path) => api.get(`/projects/${id}/file-id`, { params: { path } });
export const importProjectFromRepo = (data) => api.post('/projects/import-from-repo', data);
export const connectProjectRepo = (id, data) => api.post(`/projects/${id}/connect-repo`, data);
export const syncProjectIssues = (id) => api.post(`/projects/${id}/sync-issues`);
