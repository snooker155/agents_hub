/**
 * Watchers (watchers/, docs/watchers.md): observers of outside state (a
 * mailbox over IMAP, an HTTP resource) that wake a proactive agent when
 * something changes. One record per watcher; the agents that react to it are
 * the ones whose pulse lists it as a `watch` trigger.
 */
import api from './index';

const one = (id) => `/watchers/${encodeURIComponent(id)}`;

// [{ id, workspace, name, kind, config, interval_seconds, enabled, paused_reason,
//    last_checked_at, last_changed_at, last_event, last_error, consecutive_errors,
//    fired, active, next_check_at, listeners: [{agent_id, name}] }]
export const getWatchers = (workspace) =>
  api.get('/watchers', { params: workspace ? { workspace } : {} });

// { active, paused, errors, total, watchers: [...] }: the header indicator.
export const getWatchersSummary = (workspace) =>
  api.get('/watchers/summary', { params: workspace ? { workspace } : {} });

// { kinds: [{kind, fields: [{name, type, required, default}]}], interval: {min, max, default} }
export const getWatcherKinds = () => api.get('/watchers/kinds');

export const createWatcher = (data) => api.post('/watchers', data);
export const updateWatcher = (id, patch) => api.patch(one(id), patch);
export const deleteWatcher = (id) => api.delete(one(id));
export const pauseWatcher = (id) => api.post(`${one(id)}/pause`);
export const resumeWatcher = (id) => api.post(`${one(id)}/resume`);
// A look at the source now. dry_run (default) reports without storing state
// or waking anybody; dry_run=false is a real poll ahead of schedule.
export const probeWatcher = (id, dryRun = true) =>
  api.post(`${one(id)}/probe`, null, { params: { dry_run: dryRun } });
