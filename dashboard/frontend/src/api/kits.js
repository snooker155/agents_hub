/**
 * Industry agent kits (routes/kits.py, kits/, docs/kits.md): a ready set of
 * agents for one line of work, installed into a workspace in one step.
 */
import api from './index';

// [{ id, name, description, industry, icon, version, connectors: { required, optional },
//    rubrics, next_steps }]
export const listKits = (workspace) =>
  api.get('/kits', { params: workspace ? { workspace } : undefined });

// { ...manifest, connectors, resources: [{kind, key}], plan: { ok, changes, counts } }
export const getKit = (kitId, workspace) =>
  api.get(`/kits/${encodeURIComponent(kitId)}`, { params: workspace ? { workspace } : undefined });

// { plan, result?, agents? } — dry_run true plans only, false (default) also applies.
export const installKit = (kitId, { workspace, dryRun = false } = {}) =>
  api.post(`/kits/${encodeURIComponent(kitId)}/install`, { workspace, dry_run: dryRun });
