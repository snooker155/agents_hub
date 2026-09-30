/**
 * The version history of a memory pool's blocks, notes and structured slots
 * (fourth-cycle stage 2, feature G): list, read one, restore, redact.
 */
import api from './index';

// { versions: [{ id, memory_id, kind, item_key, version, op, value, actor_kind,
//   actor_id, run_id, at, redacted }, ...] }, newest first.
export const listMemoryVersions = (memoryId, { kind, item_key, limit } = {}) =>
  api.get(`/memory/${memoryId}/versions`, { params: { kind, item_key, limit } });

export const getMemoryVersion = (memoryId, versionId) =>
  api.get(`/memory/${memoryId}/versions/${versionId}`);

export const restoreMemoryVersion = (memoryId, versionId) =>
  api.post(`/memory/${memoryId}/versions/${versionId}/restore`);

export const redactMemoryVersion = (memoryId, versionId, { also_current = false } = {}) =>
  api.post(`/memory/${memoryId}/versions/${versionId}/redact`, { also_current });
