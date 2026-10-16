/**
 * Two-way sync with the linked hub (hubsync/, routes/sync.py): the status, the
 * workspace to space mappings, the end-to-end key and the open conflicts.
 */
import api from './index';

// { linked, remote, remote_name, enabled, encrypted, key_set, key_id,
//   key_unlocked, mappings: [{workspace, space}], last_pass_at,
//   last_result: {pulled, applied, pushed, conflicts, errors}, last_error,
//   conflicts (open count), catalog }
export const getHubSync = () => api.get('/hublink/sync');
export const saveHubSync = (data) => api.put('/hublink/sync', data);
// { passphrase } or { generate: true }; generate returns generated_passphrase once.
// When the account already has a key on the linked hub, generate answers 409
// with the detail object { message, code: 'key_exists', replace_unused };
// replace_unused is true only when no record is sealed with that key, and then
// { generate: true, replace_unused: true } replaces it. Other 409s keep a
// plain string detail.
export const setHubSyncKey = (data) => api.post('/hublink/sync/key', data);
export const forgetHubSyncKey = () => api.delete('/hublink/sync/key');
export const runHubSync = () => api.post('/hublink/sync/run');
export const listHubSyncConflicts = () => api.get('/hublink/sync/conflicts');
export const getHubSyncConflict = (id) => api.get(`/hublink/sync/conflicts/${encodeURIComponent(id)}`);
export const resolveHubSyncConflict = (id) => api.post(`/hublink/sync/conflicts/${encodeURIComponent(id)}/resolve`);

/** "3 minutes ago" in the viewer's language; an empty string for no time. */
export function relativeTime(iso, language) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return String(iso);
  const secs = Math.round((then - Date.now()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(language || 'en', { numeric: 'auto' });
  const abs = Math.abs(secs);
  if (abs < 60) return rtf.format(secs, 'second');
  if (abs < 3600) return rtf.format(Math.round(secs / 60), 'minute');
  if (abs < 86400) return rtf.format(Math.round(secs / 3600), 'hour');
  return rtf.format(Math.round(secs / 86400), 'day');
}

/** Tell the header indicator to refetch after a change made on the Settings card. */
export const notifySyncChanged = () => window.dispatchEvent(new Event('hubsync:changed'));
