/**
 * The rule types of a sequence guardrail and the empty config of each
 * (guardrails/models.py's _validate_sequence knows the same shapes).
 */
export const SEQUENCE_RULES = ['after', 'sum_max', 'same_as'];

export function defaultSequenceConfig(rule = 'after') {
  if (rule === 'sum_max') return { rule, tools: [], argument: '', max: 100 };
  if (rule === 'same_as') return { rule, tool: '', argument: '', source_tool: '', source_argument: '' };
  return { rule: 'after', tool: '', after_tool: '', require_success: false };
}
