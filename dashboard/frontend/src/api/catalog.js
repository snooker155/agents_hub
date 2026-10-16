/**
 * Catalog only (hublink/catalog.py, routes/catalog.py): the linked hub as a
 * library. Browse its agents, skills, flows, teams and loops, install copies
 * into a local workspace, and update them from the hub with a diff first.
 */
import api from './index';

export const CATALOG_KINDS = ['agents', 'skills', 'flows', 'teams', 'loops'];

// { linked, enabled, url }
export const getCatalogSettings = () => api.get('/catalog/settings');
// Admin only. { enabled }
export const saveCatalogSettings = (enabled) => api.put('/catalog/settings', { enabled });

// { items: [{kind, id, name, description, author, updated_at, agents, meta, installed_as}] }
export const browseCatalog = (kind, q = '') => api.get(`/catalog/${kind}`, { params: q ? { q } : {} });
// The card plus { portable, text, hash }: what an install would write.
export const getCatalogItem = (kind, id) => api.get(`/catalog/${kind}/${encodeURIComponent(id)}`);
// { steps: [{kind, remote_id, name, local_id, action, reason, suggested_id, tools_dropped}],
//   blocked, conflicts, can_install }
export const planCatalogInstall = (kind, id, workspace, newId) => api.post(
  `/catalog/${kind}/${encodeURIComponent(id)}/plan`, { workspace, new_id: newId || null });
export const installCatalogItem = (kind, id, workspace, newId) => api.post(
  `/catalog/${kind}/${encodeURIComponent(id)}/install`, { workspace, new_id: newId || null });

// { items: [{kind, local_id, remote_id, name, workspace, update_available, locally_modified, ...}] }
export const getCatalogUpdates = (workspace) => api.get('/catalog/updates', { params: workspace ? { workspace } : {} });
// { diff (unified text), local, remote, ...state }
export const getCatalogDiff = (kind, localId) => api.get(`/catalog/${kind}/${encodeURIComponent(localId)}/diff`);
export const updateCatalogItem = (kind, localId) => api.post(`/catalog/${kind}/${encodeURIComponent(localId)}/update`);

/** A readable message out of an error whose detail may be a string or a plan. */
export function catalogError(e, fallback = '') {
  const d = e?.response?.data?.detail;
  if (typeof d === 'string') return d;
  if (d && typeof d === 'object') {
    const step = (d.plan?.steps || []).find((s) => s.action === 'conflict' || s.action === 'missing');
    return step?.reason || d.message || fallback;
  }
  return e?.message || fallback;
}
