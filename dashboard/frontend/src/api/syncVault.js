/**
 * The vault (routes/sync.py, hubsync/vault.py): data a person synced from their
 * own hub to this one. In an encrypted space the server holds envelopes it
 * cannot read; these calls only fetch them. Opening happens in the browser
 * (lib/syncCrypto.js) and nothing decrypted is ever sent back.
 */
import api from './index';

// { spaces: [{ space, encrypted, key_id, origin, created_at, last_push_at,
//   counts: { total, encrypted, bytes, by_kind } }] }
export const listSyncSpaces = () => api.get('/sync/spaces');

// { synced: false } or { synced: true, space, key: { key_id, kdf, salt, iterations, verifier } | null }
export const getSyncHere = (workspace) => api.get('/sync/here', { params: { workspace } });

// { keys: [{ key_id, kdf, salt, iterations, verifier, ... }] }
export const listSyncKeys = () => api.get('/sync/keys');

// { records: [{ kind, record_id, version, encrypted, key_id, modified_at, size, deleted }] }
export const listSyncRecords = (space, kind) =>
  api.get(`/sync/spaces/${encodeURIComponent(space)}/records`, { params: kind ? { kind } : {} });

// One record with its payload.
export const getSyncRecord = (space, kind, recordId) =>
  api.get(`/sync/spaces/${encodeURIComponent(space)}/records/${encodeURIComponent(kind)}/${encodeURIComponent(recordId)}`);
