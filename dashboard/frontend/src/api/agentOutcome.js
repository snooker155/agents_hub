/**
 * An agent's own default outcome rubric (routes/agent_outcome.py,
 * tasks/outcome.py's shape). A task inherits it the first time it is
 * assigned this agent and has no outcome of its own yet.
 */
import api from './index';

const path = (agentId) => `/agents/${encodeURIComponent(agentId)}/default-outcome`;

// { default_outcome: { rubric, max_iterations, grader, threshold } | null }
export const getAgentDefaultOutcome = (agentId) => api.get(path(agentId));

// An empty payload ({}) clears it.
export const updateAgentDefaultOutcome = (agentId, payload) => api.put(path(agentId), payload);
