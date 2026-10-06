/**
 * What every page shows about the selected workspace, in one request
 * (GET /api/workspaces/{name}/summary, dashboard/backend/routes/workspaces.py):
 *
 *   { name, allowed_agents, personal_memory_enabled, isolated, palette,
 *     model: { global_default, workspace_default, override } }
 *
 * The header (model picker, "Isolated" badge), the palette resolution in
 * theme.js and the chat page all read it, often in the same tick on page
 * load. `loadWorkspaceSummary` hands every caller the one request already in
 * flight, and an answer younger than FRESH_MS without asking again, so a page
 * load costs one request however many places ask. A write that changes one of
 * these fields calls `patchWorkspaceSummary` (or `invalidateWorkspaceSummary`)
 * so the next read does not serve the old value.
 */
import api from './index';

const FRESH_MS = 3000;

const cache = new Map(); // workspace -> { data, at }
const pending = new Map(); // workspace -> Promise<data>
const listeners = new Map(); // workspace -> Set<(data) => void>

export const getWorkspaceSummary = (name) => api.get(`/workspaces/${encodeURIComponent(name)}/summary`);

function notify(workspace) {
  const data = cache.get(workspace)?.data ?? null;
  (listeners.get(workspace) || new Set()).forEach((fn) => fn(data));
}

/** The last answer for a workspace, however old, or null. */
export function cachedWorkspaceSummary(workspace) {
  return (workspace && cache.get(workspace)?.data) || null;
}

/** Resolve to the workspace's summary. Rejects when the request fails. */
export function loadWorkspaceSummary(workspace, { force = false } = {}) {
  if (!workspace) return Promise.resolve(null);
  if (pending.has(workspace)) return pending.get(workspace);
  const hit = cache.get(workspace);
  if (!force && hit && Date.now() - hit.at < FRESH_MS) return Promise.resolve(hit.data);
  const request = getWorkspaceSummary(workspace)
    .then(({ data }) => {
      cache.set(workspace, { data, at: Date.now() });
      notify(workspace);
      return data;
    })
    .finally(() => {
      if (pending.get(workspace) === request) pending.delete(workspace);
    });
  pending.set(workspace, request);
  return request;
}

/** Merge fields a successful write just changed, and tell every subscriber. */
export function patchWorkspaceSummary(workspace, patch) {
  const hit = cache.get(workspace);
  if (!hit) return;
  cache.set(workspace, { data: { ...hit.data, ...patch }, at: hit.at });
  notify(workspace);
}

/** Forget the stored answer, so the next load asks the backend. */
export function invalidateWorkspaceSummary(workspace) {
  cache.delete(workspace);
}

/** Called with the new summary whenever one arrives or is patched. */
export function subscribeWorkspaceSummary(workspace, fn) {
  const set = listeners.get(workspace) || new Set();
  set.add(fn);
  listeners.set(workspace, set);
  return () => { set.delete(fn); };
}
