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
