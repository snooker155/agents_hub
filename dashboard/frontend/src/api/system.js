/**
 * Self diagnostics (doctor) and the system workspace: a separate module for
 * the same reason api/code.js is, this whole surface belongs to the "feature
 * 3" panel on the Health page rather than to the general health snapshot in
 * api/index.js.
 */
import api from './index';

// A structured self check, distinct from the plain up/down snapshot at
// GET /api/health: each entry carries its own status, a human summary, an
// optional detail map, and a doc anchor to read more.
export const getDoctor = () => api.get('/health/doctor');

// The system workspace: its clone state, its scheduled loop and its recent
// branches. 404s when SYSTEM_WORKSPACE is off — callers read e.response.status.
export const getSystem = () => api.get('/system');

// Re-clone / re-sync the system workspace's repo. Returns the clone object.
export const syncSystem = () => api.post('/system/sync');

// Turn the recurring loop on or off and set its interval. Returns the loop
// object.
export const setSystemSchedule = (enabled, everyHours) =>
  api.post('/system/schedule', { enabled, every_hours: everyHours });

// Delete branches older than the given number of days. Returns the deleted
// branch names.
export const pruneSystemBranches = (olderThanDays) =>
  api.post('/system/prune', { older_than_days: olderThanDays });

// The branches list on its own, for a refresh after sync/prune without
// refetching the whole /system payload.
export const getSystemBranches = () => api.get('/system/branches');
