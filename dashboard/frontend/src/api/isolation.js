/**
 * A workspace's isolation perimeter (common/isolation.py,
 * dashboard/backend/routes/isolation.py): the on/off switch, the domains an
 * isolated workspace may read from, the hub's readiness checks, the agents
 * it owns that hold a tool from outside the allowlist, and the allowlist
 * itself.
 */
import api from './index';

const path = (name) => `/workspaces/${encodeURIComponent(name)}/isolation`;

// { workspace, isolated, allow_domains, changed_at, changed_by,
//   readiness: [{id, ok, detail}], offending_agents: [{agent_id, tools}],
//   allowed_tools, gateway_tools }
export const getWorkspaceIsolation = (name) => api.get(path(name));

// Only the keys present in `payload` change ({isolated?, allow_domains?}).
// Answers like the GET, or 409/400 with {detail} when the switch cannot move.
export const updateWorkspaceIsolation = (name, payload) => api.put(path(name), payload);
