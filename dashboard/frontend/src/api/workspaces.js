/**
 * Workspaces: files, policy, roles, special models, personal memory, active workspace.
 */
import api, { API_ORIGIN, authFetchHeaders } from './index';

// Personal memory in a workspace: its switch and each agent's (memory/personal.py).
export const getWorkspacePersonalMemory = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/personal-memory`);
export const updateWorkspacePersonalMemory = (name, enabled) => api.put(`/workspaces/${encodeURIComponent(name)}/personal-memory`, { enabled });
// Workspace roles (agents/roles.py): which agent does each kind of work here.
export const getWorkspaceRoles = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/roles`);
export const updateWorkspaceRole = (name, role, agentId) => api.put(`/workspaces/${encodeURIComponent(name)}/roles/${encodeURIComponent(role)}`, { agent_id: agentId || null });
// Special models (providers/special.py): images, video, speech, transcription, own models.
export const getWorkspaceSpecialModels = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/special-models`);
export const updateWorkspaceSpecialModels = (name, config) => api.put(`/workspaces/${encodeURIComponent(name)}/special-models`, config);
export const checkWorkspaceSpecialModel = (name, body) => api.post(`/workspaces/${encodeURIComponent(name)}/special-models/check`, body);
export const discoverWorkspaceSpecialModels = (name, purpose, provider) => api.get(
  `/workspaces/${encodeURIComponent(name)}/special-models/discover`, { params: { purpose, provider } },
);
/** The voices of one model: `{voices, own, language, languages}`. */
export const getWorkspaceSpecialModelVoices = (name, provider, model, config = {}) => api.get(
  `/workspaces/${encodeURIComponent(name)}/special-models/voices`, { ...config, params: { purpose: 'speech', provider, model } },
);
/**
 * A short line read by a speech model the form holds,
 * `{provider, model, voice, options, language}`: `{blob, language, text}`.
 * Its own fetch, so a refusal's message survives a binary response type.
 */
export async function sampleWorkspaceSpecialModel(name, body, { signal } = {}) {
  const response = await fetch(`${API_ORIGIN}/api/workspaces/${encodeURIComponent(name)}/special-models/sample`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authFetchHeaders() },
    signal,
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = '';
    try { detail = (await response.json())?.detail; } catch { /* no body */ }
    throw new Error(typeof detail === 'string' && detail ? detail : (detail?.message || `HTTP ${response.status}`));
  }
  let text = '';
  try { text = decodeURIComponent(response.headers.get('X-Sample-Text') || ''); } catch { /* left out */ }
  return { blob: await response.blob(), language: response.headers.get('X-Sample-Language') || '', text };
}

// Workspaces
// Callers that ask at the same moment (the header, a page, React's double
// mount in development) share the request in flight.
let workspacesInFlight = null;
export const getWorkspaces = () => {
  if (!workspacesInFlight) {
    workspacesInFlight = api.get('/workspaces').finally(() => { workspacesInFlight = null; });
  }
  return workspacesInFlight;
};
export const createWorkspace = (name) => api.post('/workspaces', { name });
export const getWorkspace = (name) => api.get(`/workspaces/${encodeURIComponent(name)}`);
export const getWorkspaceFilesByName = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/files`);
// A folder file is named by its registry id ({ fileId }); a bare path string
// stays for a file the registry does not follow and for folders.
const workspaceFileParams = (ref) => (
  ref && typeof ref === 'object' && ref.fileId ? { file_id: ref.fileId } : { path: typeof ref === 'object' ? ref?.path : ref });
export const getWorkspaceFileContent = (name, ref) =>
  api.get(`/workspaces/${encodeURIComponent(name)}/file-content`, { params: workspaceFileParams(ref) });
export const getWorkspaceFileRawUrl = (name, ref) => {
  const [key, value] = Object.entries(workspaceFileParams(ref))[0];
  return `${api.defaults.baseURL}/workspaces/${encodeURIComponent(name)}/file-raw?${key}=${encodeURIComponent(value || '')}`;
};
export const getWorkspaceFileId = (name, path) =>
  api.get(`/workspaces/${encodeURIComponent(name)}/file-id`, { params: { path } });
export const deleteWorkspaceFile = (name, ref) =>
  api.delete(`/workspaces/${encodeURIComponent(name)}/files`, { params: workspaceFileParams(ref) });
export const uploadWorkspaceFile = (name, file, path = '') => {
  const form = new FormData();
  form.append('file', file);
  if (path) form.append('path', path);
  return api.post(`/workspaces/${encodeURIComponent(name)}/files/upload`, form, { headers: { 'Content-Type': 'multipart/form-data' } });
};
export const addAgentToWorkspace = (name, agentId) => api.post(`/workspaces/${encodeURIComponent(name)}/agents`, { agent_id: agentId });
export const removeAgentFromWorkspace = (name, agentId) => api.delete(`/workspaces/${encodeURIComponent(name)}/agents/${encodeURIComponent(agentId)}`);
export const deleteWorkspace = (name) => api.delete(`/workspaces/${encodeURIComponent(name)}`);
export const setWorkspaceAgentCapacity = (wsName, agentId, capacity) => api.put(`/workspaces/${encodeURIComponent(wsName)}/agents/${encodeURIComponent(agentId)}/capacity`, { capacity });
export const removeWorkspaceAgentCapacity = (wsName, agentId) => api.delete(`/workspaces/${encodeURIComponent(wsName)}/agents/${encodeURIComponent(agentId)}/capacity`);
export const getWorkspaceSettingsOverrides = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/settings-overrides`);
export const updateWorkspaceSettingsOverrides = (name, overrides) => api.put(`/workspaces/${encodeURIComponent(name)}/settings-overrides`, { overrides });
// The workspace's tool policy: the approval gate and the PreToolUse/PostToolUse
// hooks (see tools/approval.py and agents/hooks.py).
export const getWorkspacePolicy = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/policy`);
// The workspace's web domain policy (tools/web.py): its own allow and deny
// lists and switch, which replace the global ones for runs in the workspace.
export const getWorkspaceWebPolicy = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/web-policy`);
export const updateWorkspaceWebPolicy = (name, policy) => api.put(`/workspaces/${encodeURIComponent(name)}/web-policy`, policy);
export const updateWorkspacePolicy = (name, policy) => api.put(`/workspaces/${encodeURIComponent(name)}/policy`, policy);
// A .hooks.json in the workspace folder is never run (agents write there);
// the owner may import it as the workspace's hooks.
export const importWorkspaceHooksFile = (name) => api.post(`/workspaces/${encodeURIComponent(name)}/policy/import-hooks-file`);
export const getWorkspaceModel = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/model`);
export const updateWorkspaceModel = (name, data) => api.put(`/workspaces/${encodeURIComponent(name)}/model`, data);
export const updateWorkspaceDefaultModel = (name, data) => api.put(`/workspaces/${encodeURIComponent(name)}/default-model`, data);
export const setWorkspaceAgentMode = (name, mode) => api.put(`/workspaces/${encodeURIComponent(name)}/agent-mode`, { agent_mode: mode });
export const getWorkspaceInstructions = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/instructions`);
export const updateWorkspaceInstructions = (name, instructions) => api.put(`/workspaces/${encodeURIComponent(name)}/instructions`, { instructions });
export const getActiveWorkspace = () => api.get('/settings/workspace');
export const setActiveWorkspace = (workspace) => api.put('/settings/workspace', { workspace });
