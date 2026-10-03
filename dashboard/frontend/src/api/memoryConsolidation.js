/**
 * Memory consolidation ("dreams", fifth-cycle stage 2): start a job, poll its
 * status and diff, switch an agent's binding to the result or discard it.
 */
import api from './index';

export const startMemoryConsolidation = (memoryId, { session_limit } = {}) =>
  api.post(`/memory/${memoryId}/consolidate`, { session_limit });

// { consolidations: [{ id, memory_id, new_memory_id, status, session_limit,
//   session_ids, diff, summary, error, trigger, provider, model,
//   created_at, started_at, finished_at }, ...] }, newest first.
export const listMemoryConsolidations = (memoryId, { limit } = {}) =>
  api.get(`/memory/${memoryId}/consolidations`, { params: { limit } });

export const getMemoryConsolidation = (jobId) =>
  api.get(`/memory/consolidations/${jobId}`);

export const applyMemoryConsolidation = (jobId, { agent_id, workspace } = {}) =>
  api.post(`/memory/consolidations/${jobId}/apply`, { agent_id, workspace });

export const discardMemoryConsolidation = (jobId) =>
  api.post(`/memory/consolidations/${jobId}/discard`);
