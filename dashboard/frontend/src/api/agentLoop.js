/**
 * Per-agent loop settings (agents/agent_loop.py, fourth-cycle stage 2):
 * fallback models tried in order on a refusal/429/5xx, the JSON Schema the
 * final answer must match, and the two tri-state loop toggles (tool search,
 * compaction). Separate from api/index.js per the fourth-cycle stage 2 file
 * split; the models-catalog call it needs to build the fallback picker is
 * re-exported from there so LoopSettingsCard imports from one place.
 */
import api from './index';

export { getModelsCatalog } from './index';

const agentPath = (agentId) => `/agents/${encodeURIComponent(agentId)}/loop-settings`;

// { fallback_models: [ids], output_schema: object|null, tool_search: bool|null, compaction: bool|null }
export const getAgentLoopSettings = (agentId) => api.get(agentPath(agentId));

// Only the keys present in `payload` are changed; the rest of the agent's
// loop settings are left as they were. Answers like the GET.
export const updateAgentLoopSettings = (agentId, payload) => api.put(agentPath(agentId), payload);

/**
 * Every enabled catalog model as `{id, provider, model}` (`id` is
 * `provider/model`, the value this feature stores in `fallback_models`),
 * from the raw catalog document `getModelsCatalog()` resolves — the same
 * shape `tools/delegation.enabled_models` derives on the backend.
 */
export const flattenModelCatalog = (raw) => {
  const out = [];
  Object.entries(raw || {}).forEach(([provider, entry]) => {
    (entry && entry.models ? entry.models : []).forEach((m) => {
      if (m && m.enabled && m.id) out.push({ id: `${provider}/${m.id}`, provider, model: m.id });
    });
  });
  return out;
};
