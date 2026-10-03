// A tool call waiting for a person inside a chat turn (common/tool_approvals.py,
// components/chat/ToolApprovalCard.jsx): the card under the live bubble.
export default {
  title: 'Approval needed',
  status: {
    pending: 'Waiting for you',
    approved: 'Approved',
    denied: 'Denied',
    expired: 'Nobody answered in time',
    cancelled: 'The run was stopped',
  },
  by: {
    hook: 'A workspace hook',
    policy: 'The tool policy',
    auto: 'The policy classifier',
    guardrail: 'A guardrail',
  },
  noteLabel: 'Note for the agent',
  notePlaceholder: 'A note for the agent (optional)',
  approve: 'Approve',
  deny: 'Deny',
  waitsUntil: 'Waits until {{time}}, then the call is refused.',
  decidedBy: 'Answered by {{name}}',
  noteShown: 'Note: {{note}}',
  waiting: 'Waiting for your approval of {{tool}}',
  errors: {
    forbidden: 'Only the run\'s owner or an admin can answer this call.',
    gone: 'This call is no longer waiting for an answer.',
    failed: 'Could not send the answer. Try again.',
  },
};
