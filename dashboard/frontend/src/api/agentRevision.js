// Optimistic concurrency for the agent editor (agents/revision.py on the
// backend). Reading an agent (GET /agents/{id}) and every successful write
// under it return the agent's definition hash as an ETag; this module keeps
// the last one per agent and sends it back as If-Match on the writes that
// change the agent's record or definition. When somebody else changed the
// agent in between, the backend answers 409 `version_conflict` and nothing is
// written: a mounted conflict handler (AgentConflictDialog) asks the user to
// reload or overwrite, and an overwrite resends the same request against the
// current hash.

const revisions = new Map();
let conflictHandler = null;

// Sub paths of /agents/{id} whose writes change the agent's record or its
// definition. Run controls (proactive/wake, experiment, versions/rollback)
// and deletes are left alone: they are not edits of a form.
const GUARDED = new Set([
  '', 'definition', 'description', 'memory', 'skills-config', 'episodic-config',
  'personal-memory', 'response-format', 'clarify-gate', 'self-delegation',
  'capability-override', 'tools', 'delegates', 'handoffs', 'reasoning', 'model',
  'sharing', 'loop-settings', 'proactive', 'guardrails', 'secrets', 'tool-policy',
]);
const NOT_AN_AGENT = new Set(['tools', 'workspace-capacities', 'capability-check', 'create']);
const WRITES = new Set(['put', 'post', 'patch']);

// `{ id, sub }` for a request URL under /agents/{id}, else null.
export function parseAgentUrl(url) {
  const path = String(url || '').split('?')[0].replace(/^.*?\/agents\//, '/agents/');
  const match = path.match(/^\/agents\/([^/]+)(?:\/(.*))?$/);
  if (!match) return null;
  const id = decodeURIComponent(match[1]);
  if (NOT_AN_AGENT.has(id)) return null;
  return { id, sub: (match[2] || '').replace(/\/+$/, '') };
}

const cleanTag = (value) => String(value || '').replace(/^W\//, '').replace(/"/g, '').trim();

export const agentRevision = (id) => revisions.get(id) || null;
export const rememberAgentRevision = (id, tag) => {
  const clean = cleanTag(tag);
  if (id && clean) revisions.set(id, clean);
};
export const forgetAgentRevisions = () => revisions.clear();

// Registers the function asked what to do on a conflict. It receives
// `{ agentId, currentVersion, currentHash, message }` and resolves to
// 'overwrite' or anything else (reload or cancel). Returns the unregister call.
export function setAgentConflictHandler(fn) {
  conflictHandler = fn;
  return () => {
    if (conflictHandler === fn) conflictHandler = null;
  };
}

const setHeader = (config, name, value) => {
  if (config.headers && typeof config.headers.set === 'function') {
    config.headers.set(name, value);
  } else {
    config.headers = { ...(config.headers || {}), [name]: value };
  }
};

const hasHeader = (config, name) => {
  const headers = config.headers || {};
  if (typeof headers.has === 'function') return headers.has(name);
  return Object.keys(headers).some((k) => k.toLowerCase() === name.toLowerCase());
};

export function installAgentRevision(api) {
  api.interceptors.request.use((config) => {
    const method = String(config.method || 'get').toLowerCase();
    const target = parseAgentUrl(config.url);
    if (!target || !WRITES.has(method) || !GUARDED.has(target.sub)) return config;
    const tag = revisions.get(target.id);
    if (tag && !hasHeader(config, 'If-Match')) setHeader(config, 'If-Match', `"${tag}"`);
    return config;
  });

  api.interceptors.response.use(
    (response) => {
      const target = parseAgentUrl(response?.config?.url);
      const method = String(response?.config?.method || 'get').toLowerCase();
      const tag = response?.headers?.etag;
      // The editor's own read and every write it makes move the token; other
      // reads of the agent leave it, so a form loaded earlier still conflicts.
      if (target && tag && ((method === 'get' && target.sub === '') || WRITES.has(method))) {
        rememberAgentRevision(target.id, tag);
      }
      return response;
    },
    async (error) => {
      const data = error?.response?.data;
      const config = error?.config;
      if (error?.response?.status !== 409 || data?.error !== 'version_conflict' || !config) {
        return Promise.reject(error);
      }
      const target = parseAgentUrl(config.url);
      if (!target || !conflictHandler || config.__revisionRetried) return Promise.reject(error);
      let decision;
      try {
        decision = await conflictHandler({
          agentId: target.id,
          currentVersion: data.current_version ?? null,
          currentHash: data.current_hash || '',
          message: data.detail || '',
        });
      } catch {
        decision = 'cancel';
      }
      if (decision !== 'overwrite' || !data.current_hash) return Promise.reject(error);
      rememberAgentRevision(target.id, data.current_hash);
      const retry = { ...config, __revisionRetried: true };
      setHeader(retry, 'If-Match', `"${data.current_hash}"`);
      return api.request(retry);
    },
  );
}
