// What the tool gate made of one call (tools/permission_policy.py): the badge
// on a tool node of the process graph and the line in the call's detail.
export default {
  permission: {
    allow: 'allowed',
    deny: 'denied',
    ask: 'asked',
  },
  badgeTitle: 'Tool gate: {{permission}}, {{reason}}',
  detail: 'Tool gate',
  reasons: {
    default_allow: 'nothing set, runs as usual',
    never_gated: 'never gated',
    policy_always_allow: 'tool policy: always allow',
    policy_always_ask: 'tool policy: always ask',
    approval_list: 'on the approval list',
    auto_run: 'classifier said run',
    auto_deny: 'classifier said deny',
    auto_ask: 'classifier said ask',
    auto_unclear: 'classifier could not decide',
    hook_deny: 'a hook denied it',
    hook_ask: 'a hook asked for a person',
    guardrail_deny: 'a sequence guardrail refused it',
    guardrail_ask: 'a sequence guardrail asked for a person',
    human_approved: 'a person approved this call',
    human_denied: 'a person denied it, or nobody answered in time',
    think_required: 'refused until the agent thinks first',
  },
};
