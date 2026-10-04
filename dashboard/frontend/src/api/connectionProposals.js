// A connection an agent proposes from the chat (dashboard/backend/routes/
// connection_proposals.py): a `tool_approvals` row whose `tool` is
// `propose_connection` (see components/chat/ConnectionProposalCard.jsx). The
// person edits the fields, types the secrets, and applies it as themself; the
// agent never sees the secret values.
import api from './index';

export const listConnectionProposals = (params = {}) => (
  api.get('/connection-proposals', { params })
);
export const applyConnectionProposal = (approvalId, { values = {}, secrets = {} } = {}) => (
  api.post(`/connection-proposals/${encodeURIComponent(approvalId)}/apply`, { values, secrets })
);
