// A tool call waiting for a person inside a chat turn
// (dashboard/backend/routes/tool_approvals.py): the chat card answers it,
// and a chat reopened while its turn waits reads the waiting calls back.
import api from './index';

export const decideToolApproval = (approvalId, decision, note = '') => (
  api.post(`/tool-approvals/${encodeURIComponent(approvalId)}`, { decision, note })
);
export const listRunApprovals = (runId, status) => (
  api.get(`/runs/${encodeURIComponent(runId)}/tool-approvals`, { params: status ? { status } : {} })
);
// The calls the nodes of a background flow run (a loop's pass too) wait on:
// the run's page, the loop's page and the chat that started it show them.
export const listFlowRunApprovals = (flowRunId, status) => (
  api.get(`/flow-runs/${encodeURIComponent(flowRunId)}/tool-approvals`, { params: status ? { status } : {} })
);
// The calls the members of a team run wait on: the team page and the chat
// that started the run show them.
export const listTeamRunApprovals = (teamRunId, status) => (
  api.get(`/team-runs/${encodeURIComponent(teamRunId)}/tool-approvals`, { params: status ? { status } : {} })
);
