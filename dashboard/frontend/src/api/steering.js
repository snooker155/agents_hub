// Steering a running run (dashboard/backend/routes/steering.py): a message
// sent while the agent works is either injected before its next model step
// (`inject`) or stops the run and carries on from the message (`interrupt`).
// The list says where each message is: pending, delivered at step N,
// expired (the run ended first), interrupted or failed.
import api from './index';

const base = (runId) => `/runs/${encodeURIComponent(runId)}/steer`;

// `send`: for an interrupted chat turn, the server sends the message as the
// conversation's next turn (the run page asks for it; the chat sends its own).
// `runId` may be a team run id: an inject goes on the team's board.
export const steerRun = (runId, message, mode = 'inject', { send = false } = {}) => (
  api.post(base(runId), send ? { message, mode, send } : { message, mode })
);
export const listRunSteering = (runId) => api.get(base(runId));
