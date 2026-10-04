// The two per-call fields a live tool_end / tool_error event carries
// (tools/permission_policy.py), ready to spread onto the call's record.
export function policyVerdict(event) {
  if (!event || !event.evaluated_permission) return {};
  return { evaluated_permission: event.evaluated_permission, reason_code: event.reason_code || '' };
}
