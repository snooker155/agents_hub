import axios from 'axios';

// Empty means same-origin: `/api/...` is served by whatever host the app was
// loaded from. In dev that is the Vite server, which proxies /api to the
// backend (see vite.config.js), so the port the backend is published on is not
// baked in here. Set VITE_API_ORIGIN to point a build at a different host.
export const API_ORIGIN = import.meta.env.VITE_API_ORIGIN ?? '';

const api = axios.create({
  baseURL: `${API_ORIGIN}/api`,
});

// Optional operator token (see common/auth.py). Off by default: an unconfigured
// backend accepts every request and this stays a no-op. Settings → System →
// API access sets it through setApiToken below, or it can be set directly with
// `localStorage.setItem('agents_hub_api_token', '<token>')`, or baked into the
// build with VITE_API_TOKEN when the same token should ship with every build.
// localStorage wins so a token can be set (or rotated) without a rebuild.
export const getApiToken = () => {
  try {
    const stored = window.localStorage.getItem('agents_hub_api_token');
    if (stored) return stored;
  } catch {
    // Privacy mode or no localStorage: fall through to the build-time value.
  }
  return import.meta.env.VITE_API_TOKEN ?? '';
};

// Sets or clears this browser's token. An empty value removes the localStorage
// key rather than storing a blank one, so getApiToken then falls back to
// VITE_API_TOKEN (if any) instead of an empty override.
export const setApiToken = (token) => {
  try {
    if (token) window.localStorage.setItem('agents_hub_api_token', token);
    else window.localStorage.removeItem('agents_hub_api_token');
  } catch {
    // Privacy mode or no localStorage: nothing to persist.
  }
};

// The session token of a logged-in user (AUTH_MODE=multi, see
// docs/identity.md). A separate key from the operator token above because they
// are separate things: one is a shared credential the operator pastes in, the
// other is issued by the backend to this browser and revoked on logout. When a
// session exists it wins, so a browser that once held an operator token does
// not keep presenting it after somebody logs in.
const SESSION_KEY = 'agents_hub_session_token';

export const getSessionToken = () => {
  try {
    return window.localStorage.getItem(SESSION_KEY) || '';
  } catch {
    return '';
  }
};

export const setSessionToken = (token) => {
  try {
    if (token) window.localStorage.setItem(SESSION_KEY, token);
    else window.localStorage.removeItem(SESSION_KEY);
  } catch {
    // Privacy mode or no localStorage: nothing to persist.
  }
};

/**
 * Whichever credential this browser currently has, session first.
 *
 * Exported because the SSE stream cannot ride the axios instance: EventSource
 * opens its own connection and cannot set headers, so `StreamContext` has to
 * put this in the query string itself.
 */
export const getAuthToken = () => getSessionToken() || getApiToken();
const activeToken = getAuthToken;

// Headers a fetch() call outside the `api` instance needs to authenticate.
// Every streaming endpoint below opens its own fetch (a long-lived response
// body axios cannot hand back incrementally), so each has to attach this
// itself rather than riding the interceptor below.
const authFetchHeaders = () => {
  const token = activeToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
};

api.interceptors.request.use((config) => {
  const token = activeToken();
  if (token) {
    config.headers = { ...config.headers, Authorization: `Bearer ${token}` };
  }
  return config;
});

// A 401 means the session this browser holds is gone: expired, logged out
// elsewhere, or revoked with a password reset. Drop it and send the app back
// to the login screen, rather than leaving every page showing its own error.
// Only sessions are handled here: an operator token that stops working is a
// configuration problem the Settings page reports, not a login to redo, and
// the auth routes themselves answer 401 as part of their normal contract.
const LOGIN_PATH = '/login';
const isAuthRoute = (url = '') => String(url).includes('/auth/');

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;
    if (status === 401 && getSessionToken() && !isAuthRoute(error?.config?.url)) {
      setSessionToken('');
      if (window.location.pathname !== LOGIN_PATH) {
        window.location.assign(LOGIN_PATH);
      }
    }
    return Promise.reject(error);
  },
);

// Identity API (see docs/identity.md and dashboard/backend/routes/auth.py).
// `getAuthMode` is public in every mode and is what the frontend renders from.
export const getAuthMode = () => api.get('/auth/mode');
export const authBootstrap = (data) => api.post('/auth/bootstrap', data);
export const authLogin = (data) => api.post('/auth/login', data);
export const authLogout = () => api.post('/auth/logout');
export const getMe = () => api.get('/auth/me');
export const getUsers = () => api.get('/auth/users');
export const createUser = (data) => api.post('/auth/users', data);
export const updateUser = (id, data) => api.patch(`/auth/users/${id}`, data);
export const deleteUser = (id) => api.delete(`/auth/users/${id}`);
export const resetUserPassword = (id, password) =>
  api.post(`/auth/users/${id}/password`, { password });

// Workspace membership: who may read, write or administer one workspace.
export const getWorkspaceMembers = (name) => api.get(`/workspaces/${name}/members`);
export const setWorkspaceMember = (name, data) => api.put(`/workspaces/${name}/members`, data);
export const removeWorkspaceMember = (name, userId) =>
  api.delete(`/workspaces/${name}/members/${userId}`);

// System health snapshot: DB reachability + store counts, background-service
// liveness, on-disk state sizes, and agent build-cache hit/miss stats.
//
// Also serves as the "is the backend up?" probe. It deliberately goes through
// the shared `/api` client rather than the bare API root (`GET /`): that root
// banner was the only URL in the app outside `/api`, so it could fail on its
// own — behind a proxy that forwards just `/api`, or against a stale CORS
// config — and produce a browser CORS error nothing else in the app would hit.
export const getSystemHealth = () => api.get('/health');

/**
 * Drain one `text/event-stream` response, calling `onEvent` per `data:` frame.
 *
 * Every streaming POST in this file speaks the same wire format — one JSON
 * object per `data: ` line, frames separated by a blank line — so the reading
 * of it lives here once. Frames that are not JSON, or carry no `type`, are
 * dropped: a stream is a best-effort narration and one malformed chunk must
 * not end the turn.
 */
const consumeSSE = async (response, onEvent) => {
  const decoder = new TextDecoder();
  const reader = response.body.getReader();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() || '';
    for (const chunk of chunks) {
      const line = chunk.split('\n').map((l) => l.trim()).find((l) => l.startsWith('data: '));
      if (!line) continue;
      let event = null;
      try { event = JSON.parse(line.slice(6)); } catch { continue; }
      if (event && event.type) onEvent(event);
    }
  }
};

/**
 * Open the chat SSE stream (POST /api/chat/stream) and invoke `onEvent` for
 * every parsed event. Shared transport for every chat surface (the full Chat
 * page, the Session details composer, and the per-node Flow chat) so each only
 * supplies its request body + an event handler and keeps its own UI state.
 *
 * @param {object}   opts
 * @param {object}   opts.body      JSON body for the chat request.
 * @param {function} opts.onEvent   called with each parsed event object.
 * @param {AbortSignal} [opts.signal] optional abort signal.
 * @throws {Error} if the response is not OK (message = server detail text).
 */
export const streamChat = async ({ body, onEvent, signal }) => {
  const response = await fetch(`${API_ORIGIN}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authFetchHeaders() },
    signal,
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    const detail = await response.text();
    throw new Error(detail || 'Failed to open chat stream');
  }

  await consumeSSE(response, onEvent);
};

/**
 * Start a chat run whose events are delivered over the shared multiplexed SSE
 * (/api/stream) instead of a dedicated streaming response. Pass the caller's SSE
 * `client_id` in the body; the server subscribes that client to a per-conversation
 * channel and returns `{ channel, conversation_id }`. The caller listens on that
 * channel (see StreamContext) for the same event shapes `streamChat` yields.
 * Avoids holding a second long-lived connection per tab.
 */
export const startChatOverSSE = (body) => api.post('/chat/stream-sse', body);

// Stored conversations. The Chat page used to keep its history in localStorage,
// which tied a chat to one browser profile and capped it at the storage quota;
// it is a service record now, so the list comes from the server. Listing omits
// transcripts — a chat's messages arrive when it is opened.
export const listChats = (params) => api.get('/chats', { params });
export const getChat = (chatId) => api.get(`/chats/${chatId}`);
// The client id travels with a save so the server can name the writer when it
// announces it: every other tab with this chat open reloads, the writer does not.
export const saveChat = (chat, clientId) => api.put(`/chats/${chat.id}`, chat, {
  headers: clientId ? { 'X-Client-Id': clientId } : undefined,
});
export const deleteChat = (chatId) => api.delete(`/chats/${chatId}`);
export const importChats = (chats) => api.post('/chats/import', { chats });
// The turn a conversation is in the middle of, for a page that arrived after it
// started: what has been generated so far, to continue from on the live channel.
export const getChatLive = (chatId) => api.get(`/chats/${chatId}/live`);

// Context references — what the chat composer can attach besides a file. The
// kind catalog and the per-kind candidate lists both come from the server so the
// picker always offers exactly what the prompt builder can render.
export const getContextKinds = () => api.get('/context/kinds');
export const getContextEntities = (kind, params) => api.get(`/context/${kind}`, { params });
export const getContextEntityPreview = (kind, id) => api.get(`/context/${kind}/${encodeURIComponent(id)}/preview`);

export const getTasks = (workspace) => api.get('/tasks', { params: { workspace } });
export const createTask = (data) => api.post('/tasks', data);
export const getTask = (id) => api.get(`/tasks/${id}`);
export const deleteTask = (id, params) => api.delete(`/tasks/${id}`, { params });
export const stopTask = (id) => api.post(`/tasks/${id}/stop`);
export const pauseTaskContainer = (id) => api.post(`/tasks/${id}/pause`);
export const resumeTaskContainer = (id) => api.post(`/tasks/${id}/resume`);
export const setTaskWorkspace = (id, payload) => api.post(`/tasks/${id}/workspace`, payload);
export const getWorkspaceFiles = (id) => api.get(`/tasks/${id}/workspace-files`);
export const getTaskFileContent = (id, path) =>
  api.get(`/tasks/${id}/file-content`, { params: { path } });
export const getTaskFileRawUrl = (id, path) =>
  `${api.defaults.baseURL}/tasks/${id}/file-raw?path=${encodeURIComponent(path)}`;

export const getAgents = (workspace) => api.get('/agents', { params: workspace ? { workspace } : {} });
export const getAgent = (id, workspace) => api.get(`/agents/${id}`, { params: workspace ? { workspace } : {} });
export const getAgentHistory = (id, workspace) => api.get(`/agents/${id}/history`, { params: workspace ? { workspace } : {} });
export const getAgentLogs = (id, params) => api.get(`/agents/${id}/logs`, { params });
// Service health, and the Service Agent's chat about it.
export const getHealth = () => api.get('/health');
// The deployment map: members (replicas and workers), leases, the launch
// queue and where runs, nodes and containers live (docs/deployment.md).
export const getDeployment = () => api.get('/deployment');
export const getMemberLogs = (memberId, tail = 500) =>
  api.get(`/deployment/members/${encodeURIComponent(memberId)}/logs`, { params: { tail } });
export const forgetMember = (memberId) =>
  api.delete(`/deployment/members/${encodeURIComponent(memberId)}`);
export const getServiceChat = () => api.get('/health/chat');
export const clearServiceChat = () => api.delete('/health/chat');
export const stopServiceChat = () => api.post('/health/chat/stop');
export const serviceChatUrl = () => '/health/chat';

export const getAgentDefinition = (id) => api.get(`/agents/${id}/definition`);
export const updateAgentDefinition = (id, data) => api.put(`/agents/${id}/definition`, data);
// The agent's own definition chat — same shape as the entity build chats.
export const getAgentDefinitionChat = (id) => api.get(`/agents/${id}/definition/chat`);
export const clearAgentDefinitionChat = (id) => api.delete(`/agents/${id}/definition/chat`);
export const stopAgentDefinitionChat = (id) => api.post(`/agents/${id}/definition/chat/stop`);
export const agentDefinitionChatUrl = (id, workspace) =>
  `/agents/${id}/definition/chat` + (workspace ? `?workspace=${encodeURIComponent(workspace)}` : '');
export const updateAgentDescription = (id, description) => api.put(`/agents/${id}/description`, { description });

// Registry version history: one row per snapshot, a diff against the current
// state or another version, and a rollback that goes back through add_agent
// (so the capability guard still runs).
export const getAgentVersions = (id) => api.get(`/agents/${id}/versions`);
export const getAgentVersionDiff = (id, version, against = 'current') =>
  api.get(`/agents/${id}/versions/${version}/diff`, { params: { against } });
export const rollbackAgentVersion = (id, version) => api.post(`/agents/${id}/versions/${version}/rollback`);
export const disconnectAgent = (id) => api.delete(`/agents/${id}`);
export const updateAgentMemory = (id, data) => api.post(`/agents/${id}/memory`, data);
export const eraseAgentMemory = (id, workspace) => api.delete(`/agents/${id}/memory`, { params: workspace ? { workspace } : {} });
export const updateAgentSkillsConfig = (id, data) => api.post(`/agents/${id}/skills-config`, data);
export const updateAgentSharing = (id, shared) => api.post(`/agents/${id}/sharing`, { shared });
export const getAgentSkills = (id, workspace) => api.get(`/agents/${id}/skills`, { params: { workspace } });
export const createAgentSkill = (id, data) => api.post(`/agents/${id}/skills`, data);
export const deleteAgentSkill = (id, skillId, workspace) => api.delete(`/agents/${id}/skills/${skillId}`, { params: { workspace } });
export const updateAgentTools = (id, data) => api.post(`/agents/${id}/tools`, data);
export const getAgentDelegates = (id) => api.get(`/agents/${id}/delegates`);
export const updateAgentDelegates = (id, delegates) => api.post(`/agents/${id}/delegates`, { delegates });
export const getAgentEpisodicConfig = (id) => api.get(`/agents/${id}/episodic-config`);
export const updateAgentEpisodicConfig = (id, episodic_write_enabled) => api.post(`/agents/${id}/episodic-config`, { episodic_write_enabled });
export const getAgentReasoning = (id) => api.get(`/agents/${id}/reasoning`);
export const updateAgentReasoning = (id, data) => api.post(`/agents/${id}/reasoning`, data);
export const updateAgentResponseFormat = (id, response_format) => api.post(`/agents/${id}/response-format`, { response_format });
export const updateAgentClarifyGate = (id, clarify_gate) => api.post(`/agents/${id}/clarify-gate`, { clarify_gate });
export const getAgentSelfDelegation = (id) => api.get(`/agents/${id}/self-delegation`);
export const updateAgentSelfDelegation = (id, allow_self_delegation) => api.post(`/agents/${id}/self-delegation`, { allow_self_delegation });
export const getAgentModel = (id) => api.get(`/agents/${id}/model`);
export const updateAgentModel = (id, data) => api.post(`/agents/${id}/model`, data);
export const testLocalModel = (provider, base_url) => api.post('/settings/test-local-model', { provider, base_url });
export const getAgentHealth = (id) => api.get(`/agents/${id}/health`);
export const createCustomAgent = (data) => api.post('/agents/create', data);
export const cloneAgentToWorkspace = (id, data) =>
  api.post(`/agents/${encodeURIComponent(id)}/clone-to-workspace`, data);
export const getAgentTools = () => api.get('/agents/tools');

// ── Importing an agent from its own git repository ──────────────────────────
// inspect() clones and analyses without registering; registerImportedAgent()
// promotes that same clone. The token returned by the first call is what ties
// the two together, so confirming an import never clones twice.
export const getAgentImportRequirements = () => api.get('/agent-import/requirements');
export const inspectAgentRepo = (data) => api.post('/agent-import/inspect', data);
export const registerImportedAgent = (data) => api.post('/agent-import/register', data);
export const discardAgentImport = (token) => api.post('/agent-import/discard', { token });
export const recheckImportedAgent = (agentId, data) =>
  api.post(`/agent-import/${encodeURIComponent(agentId)}/recheck`, data || {});
export const getAgentImportDetails = (agentId) =>
  api.get(`/agent-import/${encodeURIComponent(agentId)}`);
// Re-fetch an imported agent's own graph. Separate from recheck: this is one
// GET against the agent's service, where a recheck also re-probes the
// environment and rewrites the agent's generated documentation.
export const refreshAgentTopology = (agentId) =>
  api.post(`/agent-import/${encodeURIComponent(agentId)}/topology`);

// ── Connections: external agents that run on their own trigger and report in ─
// The opposite direction from an imported agent. These endpoints manage the
// connection and its credential; the reporting itself goes to /api/ingest,
// authenticated by that credential rather than by the dashboard's.
// The token is returned by create and rotate only, and never again.
// Every single-connection call carries the workspace it is made from. A
// connection belonging to another workspace answers 404, so this is what keeps
// one team's page from acting on another's connection by accident — see
// routes/connections._visible_or_404 for what that boundary is and is not.
const inWorkspace = (workspace) => ({ params: workspace ? { workspace } : {} });

export const listConnections = (workspace) => api.get('/connections', inWorkspace(workspace));
export const createConnection = (data) => api.post('/connections', data);
export const getConnection = (id, workspace) =>
  api.get(`/connections/${encodeURIComponent(id)}`, inWorkspace(workspace));
export const updateConnection = (id, data, workspace) =>
  api.patch(`/connections/${encodeURIComponent(id)}`, data, inWorkspace(workspace));
export const rotateConnectionToken = (id, workspace) =>
  api.post(`/connections/${encodeURIComponent(id)}/rotate`, null, inWorkspace(workspace));
export const deleteConnection = (id, workspace) =>
  api.delete(`/connections/${encodeURIComponent(id)}`, inWorkspace(workspace));
// Trim a connection's history back to its cap now, rather than waiting for the
// daily maintenance pass that normally does it.
export const pruneConnection = (id, workspace) =>
  api.post(`/connections/${encodeURIComponent(id)}/prune`, null, inWorkspace(workspace));
// Answer a reported run that stopped to ask a question. The client is not told:
// it is polling for this, because nothing here calls out to an agent that
// reports in.
export const answerConnectionRun = (id, runId, data, workspace) =>
  api.post(`/connections/${encodeURIComponent(id)}/runs/${encodeURIComponent(runId)}/answer`,
           data, inWorkspace(workspace));

// ── MCP servers: tool collections somebody else runs ─────────────────────────
// Configured per workspace, so every call carries one. Credentials in headers
// and env arrive masked (last four characters) and may be sent straight back:
// the backend reads the masked form as "unchanged" rather than overwriting the
// stored value, which is what lets a form be saved without holding the secret.
// Only testMcpServer and listMcpServerTools actually connect to a server.
export const listMcpServers = (workspace) => api.get('/mcp/servers', inWorkspace(workspace));
export const createMcpServer = (data, workspace) =>
  api.post('/mcp/servers', data, inWorkspace(workspace));
export const updateMcpServer = (id, data, workspace) =>
  api.patch(`/mcp/servers/${encodeURIComponent(id)}`, data, inWorkspace(workspace));
export const deleteMcpServer = (id, workspace) =>
  api.delete(`/mcp/servers/${encodeURIComponent(id)}`, inWorkspace(workspace));
// Connect now and report every tool the server offers, including the ones the
// allowlist would filter out: the point of the button is to help write it.
export const testMcpServer = (id, workspace) =>
  api.post(`/mcp/servers/${encodeURIComponent(id)}/test`, null, inWorkspace(workspace));
export const listMcpServerTools = (id, workspace, refresh = false) =>
  api.get(`/mcp/servers/${encodeURIComponent(id)}/tools`,
          { params: { ...(workspace ? { workspace } : {}), ...(refresh ? { refresh: true } : {}) } });

// ── Notifications: outbound endpoints + alert rules ──────────────────────────
// Same masking convention as MCP servers: a webhook's secret comes back as its
// last four characters, and sending that back unchanged keeps the stored value.
export const listNotifyEndpoints = (workspace) => api.get('/notify/endpoints', inWorkspace(workspace));
export const createNotifyEndpoint = (data, workspace) =>
  api.post('/notify/endpoints', data, inWorkspace(workspace));
export const updateNotifyEndpoint = (id, data, workspace) =>
  api.patch(`/notify/endpoints/${encodeURIComponent(id)}`, data, inWorkspace(workspace));
export const deleteNotifyEndpoint = (id, workspace) =>
  api.delete(`/notify/endpoints/${encodeURIComponent(id)}`, inWorkspace(workspace));
export const testNotifyEndpoint = (id, workspace) =>
  api.post(`/notify/endpoints/${encodeURIComponent(id)}/test`, null, inWorkspace(workspace));

export const listNotifyRules = (workspace) => api.get('/notify/rules', inWorkspace(workspace));
export const createNotifyRule = (data, workspace) =>
  api.post('/notify/rules', data, inWorkspace(workspace));
export const updateNotifyRule = (id, data, workspace) =>
  api.patch(`/notify/rules/${encodeURIComponent(id)}`, data, inWorkspace(workspace));
export const deleteNotifyRule = (id, workspace) =>
  api.delete(`/notify/rules/${encodeURIComponent(id)}`, inWorkspace(workspace));
export const updateTask = (taskId, data) => api.patch(`/tasks/${taskId}`, data);
export const assignAgent = (taskId, data) => api.post(`/tasks/${taskId}/assign`, data);
export const approveAssignment = (taskId) => api.post(`/tasks/${taskId}/approve-assignment`);
export const rejectAssignment = (taskId) => api.post(`/tasks/${taskId}/reject-assignment`);
export const stopAgent = (taskId) => api.post(`/tasks/${taskId}/stop-agent`);
export const answerTask = (taskId, answer) => api.post(`/tasks/${taskId}/answer`, { answer });
// The decision on a tool call a task is parked on (status awaiting_approval).
export const approveTaskCall = (taskId, approved, note = '') =>
  api.post(`/tasks/${taskId}/approve`, { approved, note });
export const getAgentStatus = (taskId) => api.get(`/tasks/${taskId}/agent-status`);
export const getAgentWorkspaceCapacities = (agentId) => api.get(`/agents/${encodeURIComponent(agentId)}/workspace-capacities`);
export const setDefaultChatAgent = (agentId, workspace) =>
  api.post(`/agents/${encodeURIComponent(agentId)}/set-default-chat`, null, { params: workspace ? { workspace } : {} });
export const clearDefaultChatAgent = (agentId, workspace) =>
  api.delete(`/agents/${encodeURIComponent(agentId)}/set-default-chat`, { params: workspace ? { workspace } : {} });
export const getTaskExecutionLog = (taskId) => api.get(`/tasks/${taskId}/execution-log`);
export const getTaskActivityLog = (taskId) => api.get(`/tasks/${taskId}/activity-log`);
export const getTaskResult = (taskId) => api.get(`/tasks/${taskId}/result`);
export const setTaskResult = (taskId, result) => api.put(`/tasks/${taskId}/result`, { result });
export const getLogs = (runId) => api.get(`/logs/${runId}`);
export const runDecomposer = (taskId, payload) => api.post(`/tasks/${taskId}/decompose`, payload || {});

// Marketplace — catalog of agents published across workspaces
export const getMarketplaceAgents = (workspace) =>
  api.get('/marketplace/agents', { params: workspace ? { workspace } : {} });
export const getMarketplaceAgent = (id, workspace) =>
  api.get(`/marketplace/agents/${encodeURIComponent(id)}`, { params: workspace ? { workspace } : {} });
export const getMarketplaceFlows = (workspace) =>
  api.get('/marketplace/flows', { params: workspace ? { workspace } : {} });
export const getMarketplaceSkills = (workspace) =>
  api.get('/marketplace/skills', { params: workspace ? { workspace } : {} });
export const updateFlowSharing = (id, shared) => api.post(`/flows/${encodeURIComponent(id)}/sharing`, { shared });
export const addFlowToWorkspace = (name, flowId) =>
  api.post(`/workspaces/${encodeURIComponent(name)}/flows`, { flow_id: flowId });
export const removeFlowFromWorkspace = (name, flowId) =>
  api.delete(`/workspaces/${encodeURIComponent(name)}/flows/${encodeURIComponent(flowId)}`);

// Skills catalog — reusable procedures owned by a workspace, publishable to the
// global catalog, installed onto agents as copies.
export const getSkills = (workspace, params = {}) =>
  api.get('/skills', { params: { workspace, ...params } });
export const getSkillTargets = (workspace) => api.get('/skills/agents', { params: { workspace } });
export const createSkill = (data) => api.post('/skills', data);
export const getSkillDetails = (id) => api.get(`/skills/${encodeURIComponent(id)}`);
export const updateSkill = (id, data) => api.patch(`/skills/${encodeURIComponent(id)}`, data);
export const updateSkillSharing = (id, shared) =>
  api.post(`/skills/${encodeURIComponent(id)}/sharing`, { shared });
export const installSkill = (id, data) => api.post(`/skills/${encodeURIComponent(id)}/install`, data);
export const deleteSkill = (id) => api.delete(`/skills/${encodeURIComponent(id)}`);

// Web access log — recorded web_search / fetch_url calls, their responses and
// the security flags raised against them.
export const getWebLogs = (params) => api.get('/web-logs', { params });
export const getWebLogStats = () => api.get('/web-logs/stats');
export const getWebLogEntry = (id) => api.get(`/web-logs/${encodeURIComponent(id)}`);
export const clearWebLogs = () => api.delete('/web-logs');

// Orchestrator
export const getOrchestratorSettings = (workspace) =>
  api.get('/orchestrator/settings', { params: workspace ? { workspace } : {} });
export const updateOrchestratorSettings = (data, workspace) =>
  api.post('/orchestrator/settings', data, { params: workspace ? { workspace } : {} });
export const getOrchestratorRoutingLog = (workspace) =>
  api.get('/orchestrator/routing-log', { params: workspace ? { workspace } : {} });

// Stats & Manifests
export const getStats = (workspace) => api.get('/stats', { params: { workspace } });
export const applyAgentManifest = (data) => api.post('/agents/apply', data);
// With a workspace the MCP servers attached to it are listed as well.
export const getTools = (workspace) => api.get('/tools', inWorkspace(workspace));
export const getToolSource = (toolId) => api.get(`/tools/${encodeURIComponent(toolId)}/source`);
export const updateToolSource = (toolId, data) => api.put(`/tools/${encodeURIComponent(toolId)}/source`, data);
export const getRuns = (workspace) => api.get('/runs', { params: workspace ? { workspace } : {} });

// Workspaces
export const getWorkspaces = () => api.get('/workspaces');
export const createWorkspace = (name) => api.post('/workspaces', { name });
export const getWorkspace = (name) => api.get(`/workspaces/${encodeURIComponent(name)}`);
export const getWorkspaceFilesByName = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/files`);
export const getWorkspaceFileContent = (name, path) =>
  api.get(`/workspaces/${encodeURIComponent(name)}/file-content`, { params: { path } });
export const getWorkspaceFileRawUrl = (name, path) =>
  `${api.defaults.baseURL}/workspaces/${encodeURIComponent(name)}/file-raw?path=${encodeURIComponent(path)}`;
export const deleteWorkspaceFile = (name, path) =>
  api.delete(`/workspaces/${encodeURIComponent(name)}/files`, { params: { path } });
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
export const updateWorkspacePolicy = (name, policy) => api.put(`/workspaces/${encodeURIComponent(name)}/policy`, policy);
export const getWorkspaceModel = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/model`);
export const updateWorkspaceModel = (name, data) => api.put(`/workspaces/${encodeURIComponent(name)}/model`, data);
export const updateWorkspaceDefaultModel = (name, data) => api.put(`/workspaces/${encodeURIComponent(name)}/default-model`, data);
export const setWorkspaceAgentMode = (name, mode) => api.put(`/workspaces/${encodeURIComponent(name)}/agent-mode`, { agent_mode: mode });
export const getWorkspaceInstructions = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/instructions`);
export const updateWorkspaceInstructions = (name, instructions) => api.put(`/workspaces/${encodeURIComponent(name)}/instructions`, { instructions });

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
export const indexMemoryFile = (id, filename, workspace) => api.post(`/shared-memory/${id}/files/${encodeURIComponent(filename)}/index`, null, { params: { workspace } });
export const deindexMemoryFile = (id, filename) => api.delete(`/shared-memory/${id}/files/${encodeURIComponent(filename)}/index`);
export const deleteMemoryFile = (id, filename, workspace) => api.delete(`/shared-memory/${id}/files/${encodeURIComponent(filename)}`, { params: { workspace } });
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

// Sessions API (process-level contexts)
export const getSessions = (params) => api.get('/sessions', { params });
export const createSession = (data) => api.post('/sessions', data);
export const getSession = (sessionId) => api.get(`/sessions/${sessionId}`);
export const getSessionMessages = (sessionId) => api.get(`/sessions/${sessionId}/messages`);
export const stopSession = (sessionId) => api.post(`/sessions/${sessionId}/stop`);
export const deleteSession = (sessionId, params) => api.delete(`/sessions/${sessionId}`, { params });

// Run groups API — one view over flow/loop/team runs and task containers.
export const listRunGroups = (params) => api.get('/runs/groups', { params });
export const getRunGroup = (kind, id) => api.get(`/runs/groups/${kind}/${id}`);
export const stopRunGroup = (kind, id) => api.post(`/runs/groups/${kind}/${id}/stop`);

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
// Regression replay: re-run a recorded run (optionally overriding provider/model)
// and diff outputs. Long-running (a real LLM call).
export const replayRun = (runId, data) => api.post(`/runs/${runId}/replay`, data || {}, { timeout: 300000 });

// Nodes API
export const getNodes = (workspace) => api.get('/nodes', { params: workspace ? { workspace } : {} });
export const startNode = (data) => api.post('/nodes', data);
export const getNodeById = (nodeId) => api.get(`/nodes/${nodeId}`);
export const getNodeLogs = (nodeId) => api.get(`/nodes/${nodeId}/logs`);
export const stopNode = (nodeId) => api.post(`/nodes/${nodeId}/stop`);
export const restartNode = (nodeId) => api.post(`/nodes/${nodeId}/restart`);
export const deleteNode = (nodeId) => api.delete(`/nodes/${nodeId}`);
export const exposeNode = (nodeId) => api.post(`/nodes/${nodeId}/expose`);
export const unexposeNode = (nodeId) => api.delete(`/nodes/${nodeId}/expose`);
// Inbound signing for an exposed node. Write-only: a node reports only
// `inbound_secret_configured`, never the value.
export const setNodeInboundSecret = (nodeId, secret) => api.put(`/nodes/${nodeId}/inbound-secret`, { secret });
export const clearNodeInboundSecret = (nodeId) => api.delete(`/nodes/${nodeId}/inbound-secret`);
export const getNodeConnections = (nodeId) => api.get(`/nodes/${nodeId}/connections`);
export const getNodeRuns = (nodeId, limit = 50) => api.get(`/nodes/${nodeId}/runs`, { params: { limit } });

// Legacy Factory API
export const getFactoryGraph = () => api.get('/factory/graph');
export const getActiveNode = (workspace) => api.get(`/factory/graph/active-node?workspace=${encodeURIComponent(workspace)}`);
export const checkWaiting = (workspace) => api.get(`/factory/waiting-for-input?workspace=${encodeURIComponent(workspace)}`);
export const provideInput = (data) => api.post('/factory/user-input', data);
export const runFactoryAgent = (data) => api.post('/factory/run-agent', data);
export const getFactoryLogs = (workspace) => api.get(`/factory/logs?workspace=${encodeURIComponent(workspace)}`);

// Models API — curated catalog + per-model usage stats
export const getModelsCatalog = () => api.get('/models');
export const saveModelsCatalog = (providers) => api.put('/models', { providers });
export const discoverProviderModels = (provider) => api.post(`/models/discover/${encodeURIComponent(provider)}`);
export const getModelsUsage = (params) => api.get('/models/usage', { params });

// Costs API — spend breakdowns + per-workspace budget caps
export const getCosts = (params) => api.get('/costs', { params });
export const getBudget = (workspace) => api.get('/costs/budget', { params: { workspace } });
export const setBudget = (workspace, data) => api.post('/costs/budget', data, { params: { workspace } });

// Global Settings API
export const getSettings = () => api.get('/settings');
export const updateSettings = (data) => api.put('/settings', data);
export const testProvider = (data) => api.post('/settings/test-provider', data);
export const getCustomBackends = () => api.get('/settings/custom-backends');
export const saveCustomBackend = (data) => api.post('/settings/custom-backends', data);
export const deleteCustomBackend = (id) => api.delete(`/settings/custom-backends/${encodeURIComponent(id)}`);
export const getActiveWorkspace = () => api.get('/settings/workspace');
export const setActiveWorkspace = (workspace) => api.put('/settings/workspace', { workspace });

// Flows API
export const listFlows = (workspace) => api.get('/flows', { params: workspace ? { workspace } : {} });
// Fire a flow from an external trigger (seed merged into initial state; concurrency-capped).
export const triggerFlow = (flowId, data) => api.post(`/flows/${flowId}/trigger`, data || {});
export const generateFlow = (data) => api.post('/flows/generate', data);
export const createFlow = (data) => api.post('/flows', data);
export const getFlow = (flowId) => api.get(`/flows/${encodeURIComponent(flowId)}`);
export const updateFlow = (flowId, data) => api.put(`/flows/${encodeURIComponent(flowId)}`, data);
export const deleteFlow = (flowId) => api.delete(`/flows/${encodeURIComponent(flowId)}`);
export const importFlow = (data) => api.post('/flows/import', data);
export const exportFlow = (flowId) =>
  api.get(`/flows/${encodeURIComponent(flowId)}/export`, { responseType: 'blob' });
export const runFlow = (flowId, data) => api.post(`/flows/${encodeURIComponent(flowId)}/run`, data);
export const stopFlow = (flowId) => api.post(`/flows/${encodeURIComponent(flowId)}/stop`);
export const runFlowNode = (flowId, data) => api.post(`/flows/${encodeURIComponent(flowId)}/run-node`, data);
export const getFlowLogs = (flowId, workspace) =>
  api.get(`/flows/${encodeURIComponent(flowId)}/logs`, { params: { workspace } });
export const getFlowRuns = (flowId, workspace) =>
  api.get(`/flows/${encodeURIComponent(flowId)}/runs`, { params: { workspace } });
// One record per execution (status, checkpoint, timing), unlike /runs which
// groups the log. A failed or parked run with a checkpoint can be resumed.
export const getFlowInstances = (flowId, activeOnly = false) =>
  api.get(`/flows/${encodeURIComponent(flowId)}/instances`, { params: { active_only: activeOnly } });
export const estimateFlowCost = (flowId) => api.get(`/flows/${encodeURIComponent(flowId)}/estimate`);
export const resumeFlowRun = (flowRunId, answer) =>
  api.post(`/flows/runs/${encodeURIComponent(flowRunId)}/resume`, answer ? { answer } : {});

// Flow entity registry — federated catalog of flow-usable nodes (agents,
// processors, conditions, transforms, ...). Backs the Registry menu.
export const listFlowEntities = (category, workspace) =>
  api.get('/flow-entities', { params: { ...(category ? { category } : {}), ...(workspace ? { workspace } : {}) } });
export const getFlowEntity = (entityId) => api.get(`/flow-entities/${encodeURIComponent(entityId)}`);
export const createFlowEntity = (data) => api.post('/flow-entities', data);
export const deleteFlowEntity = (entityId) => api.delete(`/flow-entities/${encodeURIComponent(entityId)}`);

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
// Rich views (charts, tables, diagrams, …) produced by agents.
export const listViews = (params = {}) => api.get('/views', { params });
export const getView = (viewId) => api.get(`/views/${viewId}`);
export const setViewState = (viewId, state) => api.patch(`/views/${viewId}/state`, { state });
export const deleteView = (viewId) => api.delete(`/views/${viewId}`);
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
/**
 * Drive one turn of an entity build chat (a scenario's, a loop's) over SSE.
 *
 * The same wire format the project graph chat uses — `data: {json}` frames —
 * but the path is a parameter, because what differs between these chats is the
 * entity behind them, not the transport. POST returns the stream directly, so
 * unlike the multiplexed chat page this holds one connection for the turn.
 *
 * @param {object} opts
 * @param {string} opts.path      API path under /api (e.g. from `scenarioChatUrl`).
 * @param {string} opts.message   the user's turn.
 * @param {object} [opts.body]    extra fields for the turn's payload. The page
 *   chat sends what the user is looking at this way (scope, route, records);
 *   an entity chat, whose subject is fixed by its path, sends nothing.
 * @param {function} opts.onEvent called with each parsed event object.
 * @param {AbortSignal} [opts.signal]
 */
export const streamEntityChat = async ({ path, message, body = null, onEvent, signal }) => {
  const response = await fetch(`${API_ORIGIN}/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authFetchHeaders() },
    signal,
    body: JSON.stringify({ ...(body || {}), message }),
  });
  if (!response.ok || !response.body) {
    const detail = await response.text();
    throw new Error(detail || 'Failed to open the chat stream');
  }
  await consumeSSE(response, onEvent);
};

// Session history, shared by every entity build chat (routes/entity_chats.py).
// The ref comes from `chat_ref` on the chat's own GET response, so a page never
// has to know its chat's storage key. Activating a session swaps it in as the
// live thread, which is what lets a past conversation be carried on.
export const getEntityChatSessions = (ref) =>
  api.get('/entity-chats/sessions', { params: { kind: ref.kind, entity_id: ref.id } });
export const activateEntityChatSession = (ref, sessionId) =>
  api.post('/entity-chats/sessions/activate', {
    kind: ref.kind, entity_id: ref.id, session_id: sessionId,
  });
export const deleteEntityChatSession = (ref, sessionId) =>
  api.delete('/entity-chats/sessions', {
    params: { kind: ref.kind, entity_id: ref.id, session_id: sessionId },
  });

// The page chat — the floating panel that follows the user from page to page.
// One agent, one thread per `scope`, and the records the page is showing sent
// as pointers with the turn (see routes/page_chat.py). The streaming turn goes
// through `streamEntityChat` with those pointers as its extra body.
export const getPageChat = (scope) => api.get('/page-chat', { params: { scope } });
export const clearPageChat = (scope) => api.delete('/page-chat', { params: { scope } });
export const stopPageChat = (scope) => api.post('/page-chat/stop', null, { params: { scope } });
export const pageChatUrl = () => '/page-chat';

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
export const getProjectFileContent = (id, path) => api.get(`/projects/${id}/file-content`, { params: { path } });
export const importProjectFromRepo = (data) => api.post('/projects/import-from-repo', data);
export const connectProjectRepo = (id, data) => api.post(`/projects/${id}/connect-repo`, data);
export const syncProjectIssues = (id) => api.post(`/projects/${id}/sync-issues`);

// Git connectors API (GitHub / GitLab)
export const getGitConfig = () => api.get('/git/config');
export const updateGitConfig = (data) => api.put('/git/config', data);
export const testGitConnection = (provider) => api.post('/git/test', { provider });
export const listGitRepos = (provider, search) => api.get('/git/repos', { params: { provider, ...(search ? { search } : {}) } });

// Blender geometry connector
export const getBlenderConfig = () => api.get('/blender/config');
export const updateBlenderConfig = (data) => api.put('/blender/config', data);
export const testBlenderBinary = (binary_path) => api.post('/blender/test', { binary_path });
export const getBlenderDaemons = () => api.get('/blender/daemons');
export const stopBlenderDaemon = (key) => api.delete(`/blender/daemons/${encodeURIComponent(key)}`);
export const stopAllBlenderDaemons = () => api.delete('/blender/daemons');

// Containers API
export const getContainerImages = () => api.get('/containers/images');
export const getContainers = () => api.get('/containers');
export const getDockerfile = (agentId) => api.get(`/containers/dockerfile/${encodeURIComponent(agentId)}`, { responseType: 'text' });
export const buildBaseImage = (data) => api.post('/containers/build-base', data);
export const buildAgentImage = (agentId, data) => api.post(`/containers/build/${encodeURIComponent(agentId)}`, data);
export const getContainerLogs = (name, tail) => api.get(`/containers/${encodeURIComponent(name)}/logs`, { params: tail ? { tail } : {}, responseType: 'text' });
export const stopContainerByName = (name) => api.post(`/containers/${encodeURIComponent(name)}/stop`);
export const removeContainer = (name) => api.delete(`/containers/${encodeURIComponent(name)}`);
export const ensureDockerNetwork = () => api.post('/containers/network/ensure');
export const getAgentsBuildStatus = () => api.get('/containers/agents-status');

// Plan API — scheduled jobs (future notifications / agent tasks)
export const getPlanJobs = (workspace, status) =>
  api.get('/plan/jobs', { params: { ...(workspace ? { workspace } : {}), ...(status ? { status } : {}) } });
export const createPlanJob = (data) => api.post('/plan/jobs', data);
export const getPlanJob = (id) => api.get(`/plan/jobs/${id}`);
export const updatePlanJob = (id, data) => api.patch(`/plan/jobs/${id}`, data);
export const deletePlanJob = (id) => api.delete(`/plan/jobs/${id}`);
export const pausePlanJob = (id) => api.post(`/plan/jobs/${id}/pause`);
export const resumePlanJob = (id) => api.post(`/plan/jobs/${id}/resume`);
export const cancelPlanJob = (id) => api.post(`/plan/jobs/${id}/cancel`);
export const runPlanJobNow = (id) => api.post(`/plan/jobs/${id}/run-now`);

// Notifications API — user inbox fed by the plan scheduler
export const getNotifications = (params) => api.get('/plan/notifications', { params });
export const getNotificationsUnreadCount = (workspace) =>
  api.get('/plan/notifications/unread-count', { params: workspace ? { workspace } : {} });
export const markNotificationRead = (id, read = true) =>
  api.post(`/plan/notifications/${id}/read`, null, { params: { read } });
export const markAllNotificationsRead = (workspace) =>
  api.post('/plan/notifications/read-all', null, { params: workspace ? { workspace } : {} });
export const deleteNotification = (id) => api.delete(`/plan/notifications/${id}`);
// Live notification push now arrives on the shared `/api/stream` connection
// (channel `__notifications__`) via the StreamProvider — no dedicated endpoint.

// Telegram API
export const getTelegramConfig = () => api.get('/telegram/config');
export const updateTelegramConfig = (data) => api.put('/telegram/config', data);
export const testTelegramToken = () => api.post('/telegram/test');
export const getTelegramStatus = () => api.get('/telegram/status');
export const getTelegramBindings = () => api.get('/telegram/bindings');
export const deleteTelegramBinding = (chatId) => api.delete(`/telegram/bindings/${chatId}`);
export const sendTelegramMessage = (chatId, text) => api.post('/telegram/send', { chat_id: chatId, text });

// Evals API — datasets, sweeps, score matrices (see evals/ and routes/evals.py)
export const getEvalSets = (workspace) =>
  api.get('/evals', { params: workspace ? { workspace } : {} });
export const createEvalSet = (data) => api.post('/evals', data);
export const getEvalSet = (id) => api.get(`/evals/${id}`);
export const updateEvalSet = (id, data) => api.put(`/evals/${id}`, data);
export const deleteEvalSet = (id) => api.delete(`/evals/${id}`);
// The Eval Agent's chat — workspace-scoped, because the first thing anyone
// wants is a set that does not exist yet.
export const getEvalChat = (workspace) =>
  api.get('/evals/chat', { params: workspace ? { workspace } : {} });
export const clearEvalChat = (workspace) =>
  api.delete('/evals/chat', { params: workspace ? { workspace } : {} });
export const stopEvalChat = (workspace) =>
  api.post('/evals/chat/stop', null, { params: workspace ? { workspace } : {} });
export const evalChatUrl = (workspace) =>
  '/evals/chat' + (workspace ? `?workspace=${encodeURIComponent(workspace)}` : '');
export const addEvalCase = (id, data) => api.post(`/evals/${id}/cases`, data);
export const deleteEvalCase = (id, caseId) => api.delete(`/evals/${id}/cases/${caseId}`);
export const estimateEvalRun = (id, data) => api.post(`/evals/${id}/estimate`, data);
// A sweep is len(cases) x len(configs) LLM calls -- it can take minutes, so the
// default axios timeout does not apply here.
export const runEvalSet = (id, data) => api.post(`/evals/${id}/run`, data, { timeout: 0 });
export const getEvalRuns = (id) => api.get(`/evals/${id}/runs`);
export const getEvalRun = (runId) => api.get(`/eval-runs/${runId}`);
export const getEvalRunDiff = (runAId, runBId) =>
  api.get(`/evals/runs/${runAId}/diff/${runBId}`);
export const getEvalGraders = () => api.get('/eval-graders');

// Playground API — multi-agent simulation (see playground/ and routes/playground.py)
// The catalogue is workspace-aware because authored worlds are in it: a
// workspace's own worlds sit alongside the shipped ones, which is the whole
// point of being able to build one.
export const getSimEnvironments = (workspace) =>
  api.get('/playground/environments', { params: workspace ? { workspace } : {} });

// Worlds — the user-authored environments (see playground/worlds.py).
export const getWorlds = (workspace) =>
  api.get('/playground/worlds', { params: workspace ? { workspace } : {} });
export const getWorld = (id) => api.get(`/playground/worlds/${id}`);
export const createWorld = (data) => api.post('/playground/worlds', data);
export const updateWorld = (id, data) => api.put(`/playground/worlds/${id}`, data);
// `force` deletes a world that scenarios are still cast in; without it the
// server refuses, because a scenario whose world is gone fails at Run.
export const deleteWorld = (id, force = false) =>
  api.delete(`/playground/worlds/${id}`, { params: force ? { force: true } : {} });
export const validateWorldDraft = (data) => api.post('/playground/worlds/validate', data);
export const getWorldTemplates = () => api.get('/playground/worlds/templates');
// Build a whole world from a plain-language description (the World Builder
// names the places, declares the values and writes the actions).
export const generateWorld = (data) => api.post('/playground/worlds/generate', data);
// The world's own build chat: transcript + rich replay trace, clearing it, and
// stopping an in-flight turn. The streaming turn goes through `streamEntityChat`.
export const getWorldChat = (id) => api.get(`/playground/worlds/${id}/chat`);
export const clearWorldChat = (id) => api.delete(`/playground/worlds/${id}/chat`);
export const stopWorldChat = (id) => api.post(`/playground/worlds/${id}/chat/stop`);
export const worldChatUrl = (id) => `/playground/worlds/${id}/chat`;
export const getScenarios = (workspace) =>
  api.get('/playground/scenarios', { params: workspace ? { workspace } : {} });
export const createScenario = (data) => api.post('/playground/scenarios', data);
export const getScenario = (id) => api.get(`/playground/scenarios/${id}`);
export const updateScenario = (id, data) => api.put(`/playground/scenarios/${id}`, data);
export const deleteScenario = (id) => api.delete(`/playground/scenarios/${id}`);
export const estimateScenario = (id) => api.post(`/playground/scenarios/${id}/estimate`);
// Build a whole scenario from a plain-language description (the Scenario
// Creator picks the environment, casts the roles and sets the limits).
export const generateScenario = (data) => api.post('/playground/scenarios/generate', data);
/**
 * The same build, narrated (SSE).
 *
 * Designing a scenario is a minute of tool calls, and a spinner cannot tell a
 * slow run from a stuck one. This streams the Creator's steps as they happen
 * and closes with one `result` frame carrying `outcome` — the scenario it
 * made, the limitations it ran into, or an error.
 *
 * @param {object} opts
 * @param {object} opts.body      same payload as `generateScenario`.
 * @param {function} opts.onEvent called with each parsed event object.
 * @param {AbortSignal} [opts.signal]
 */
export const streamGenerateScenario = async ({ body, onEvent, signal }) => {
  const response = await fetch(`${API_ORIGIN}/api/playground/scenarios/generate/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authFetchHeaders() },
    signal,
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    const detail = await response.text();
    throw new Error(detail || 'Failed to start scenario generation');
  }
  await consumeSSE(response, onEvent);
};
// The scenario's own build chat: transcript + rich replay trace, clearing it,
// and stopping an in-flight turn. The streaming turn itself goes through
// `streamEntityChat`.
export const getScenarioChat = (id) => api.get(`/playground/scenarios/${id}/chat`);
export const clearScenarioChat = (id) => api.delete(`/playground/scenarios/${id}/chat`);
export const stopScenarioChat = (id) => api.post(`/playground/scenarios/${id}/chat/stop`);
export const scenarioChatUrl = (id) => `/playground/scenarios/${id}/chat`;
export const startSimulation = (id, workspace) =>
  api.post(`/playground/scenarios/${id}/run`, null, { params: workspace ? { workspace } : {} });
// One scenario's run history, or — with no scenario id — every scenario's,
// which is what the history page lists. Runs come back newest first.
export const getSimRuns = (scenarioId, { workspace, limit } = {}) =>
  api.get('/playground/runs', {
    params: {
      ...(scenarioId ? { scenario_id: scenarioId } : {}),
      ...(workspace ? { workspace } : {}),
      ...(limit ? { limit } : {}),
    },
  });
export const getSimRun = (runId) => api.get(`/playground/runs/${runId}`);
export const getSimTicks = (runId, since = -1) =>
  api.get(`/playground/runs/${runId}/ticks`, { params: { since } });
export const stopSimulation = (runId) => api.post(`/playground/runs/${runId}/stop`);
// Poke one agent in a running simulation from outside the world. In triggered
// mode this is what wakes them; in synchronous mode it is a message like any
// other, delivered on the next tick.
export const triggerSimAgent = (runId, agent, text) =>
  api.post(`/playground/runs/${runId}/trigger`, { agent, text });
// The run as one piece of prose: the chronicle is composed from the tick log
// on every request (free, exact, works mid-run), the narration is a model's
// retelling of it and is kept once written.
export const getSimStory = (runId, lang) =>
  api.get(`/playground/runs/${runId}/story`, { params: { lang } });
export const narrateSimStory = (runId, lang) =>
  api.post(`/playground/runs/${runId}/story/narrate`, null,
           { params: { lang }, timeout: 0 });

// Loops API — a flow re-run until an agent judges the exit criterion met
// (see loops/ and routes/loops.py).
export const getLoops = (workspace) =>
  api.get('/loops', { params: workspace ? { workspace } : {} });
export const createLoop = (data) => api.post('/loops', data);
export const getLoop = (id) => api.get(`/loops/${id}`);
export const updateLoop = (id, data) => api.put(`/loops/${id}`, data);
export const deleteLoop = (id) => api.delete(`/loops/${id}`);
export const estimateLoop = (id) => api.post(`/loops/${id}/estimate`);
// The loop's own build chat — same shape as the scenario's.
export const getLoopChat = (id) => api.get(`/loops/${id}/chat`);
export const clearLoopChat = (id) => api.delete(`/loops/${id}/chat`);
export const stopLoopChat = (id) => api.post(`/loops/${id}/chat/stop`);
export const loopChatUrl = (id) => `/loops/${id}/chat`;
export const startLoop = (id, data) => api.post(`/loops/${id}/run`, data || {});
export const getLoopRuns = (loopId) =>
  api.get('/loops/runs', { params: loopId ? { loop_id: loopId } : {} });
export const getLoopRun = (runId) => api.get(`/loops/runs/${runId}`);
export const getLoopIterations = (runId, since = 0) =>
  api.get(`/loops/runs/${runId}/iterations`, { params: { since } });
export const stopLoopRun = (runId) => api.post(`/loops/runs/${runId}/stop`);
export const resumeLoopRun = (runId) => api.post(`/loops/runs/${runId}/resume`);

// Teams API — a bounded roster of agents that know each other and talk
// (see teams/ and routes/teams.py).
export const getTeams = (workspace) =>
  api.get('/teams', { params: workspace ? { workspace } : {} });
export const createTeam = (data) => api.post('/teams', data);
export const getTeam = (id) => api.get(`/teams/${id}`);
export const updateTeam = (id, data) => api.put(`/teams/${id}`, data);
export const deleteTeam = (id) => api.delete(`/teams/${id}`);
export const getTeamBriefing = (id, agentId) =>
  api.get(`/teams/${id}/briefing`, { params: agentId ? { agent_id: agentId } : {} });
export const suggestTeamManifest = (agentId) => api.get(`/teams/manifest/${agentId}`);
// The team's own build chat — same shape as the loop's.
export const getTeamChat = (id) => api.get(`/teams/${id}/chat`);
export const clearTeamChat = (id) => api.delete(`/teams/${id}/chat`);
export const stopTeamChat = (id) => api.post(`/teams/${id}/chat/stop`);
export const teamChatUrl = (id) => `/teams/${id}/chat`;
export const estimateTeam = (id) => api.post(`/teams/${id}/estimate`);
export const startTeamRun = (id, data) => api.post(`/teams/${id}/run`, data || {});
export const getTeamRuns = (teamId) =>
  api.get('/teams/runs', { params: teamId ? { team_id: teamId } : {} });
export const getTeamRun = (runId) => api.get(`/teams/runs/${runId}`);
export const getTeamMessages = (runId, since = 0) =>
  api.get(`/teams/runs/${runId}/messages`, { params: { since } });
export const stopTeamRun = (runId) => api.post(`/teams/runs/${runId}/stop`);

export default api;
