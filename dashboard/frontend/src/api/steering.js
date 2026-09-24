// Steering a running run (dashboard/backend/routes/steering.py): a message
// sent while the agent works is either injected before its next model step
// (`inject`) or stops the run and carries on from the message (`interrupt`).
// The list says where each message is: pending, delivered at step N,
// expired (the run ended first), interrupted or failed.
import api from './index';

const base = (runId) => `/runs/${encodeURIComponent(runId)}/steer`;

export const steerRun = (runId, message, mode = 'inject') => api.post(base(runId), { message, mode });
export const listRunSteering = (runId) => api.get(base(runId));
