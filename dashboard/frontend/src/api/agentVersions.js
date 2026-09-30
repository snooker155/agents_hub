/**
 * Pin an agent version on a run and roll it back (dashboard/backend/routes/messages.py's
 * `/api/runs/*` router, agents/versions.py). Separate from api/index.js because
 * this whole surface belongs to the agent-version-pin feature; the agent
 * versions list and the task update call it shares with the existing
 * per-agent version history page are re-exported so both components import
 * from one place.
 */
import api from './index';

export { getAgentVersions, updateTask } from './index';

const runPath = (runId) => `/runs/${encodeURIComponent(runId)}`;

// {agent_id, version, hash, current_version, is_current, pinned} for the run
// that built this agent — version/hash are null when the run never resolved one.
export const getRunAgentVersion = (runId) => api.get(`${runPath(runId)}/agent-version`);

// Rolls the agent back to the version this run ran. Answers like the
// per-agent rollback route: {agent_id, restored_to, agent}.
export const rollbackRunAgent = (runId) => api.post(`${runPath(runId)}/rollback-agent`);
