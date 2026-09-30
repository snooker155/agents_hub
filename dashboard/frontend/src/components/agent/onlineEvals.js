/**
 * Pure helpers for the online eval rule form and the experiment card, kept
 * out of the component files so those export components only.
 */

// The one parameter each grader kind needs from the form. Kinds with none
// grade the output (or the run's tool trail) on their own. The API accepts
// every grader evals know; the form offers the ones a single field covers.
export const GRADER_FIELDS = {
  exact: 'expected',
  substring: 'expected',
  regex: 'pattern',
  llm_judge: 'rubric',
  json_valid: null,
  tool_called: 'tool',
  tool_not_called: 'tool',
  max_tool_calls: 'limit',
  no_error_tool_results: null,
};

/** One grader spec as the API stores it, from a form row. */
export function graderSpec(row) {
  const field = GRADER_FIELDS[row.kind];
  const params = {};
  if (field && String(row.value || '').trim()) {
    params[field] = field === 'limit' ? Number(row.value) : String(row.value).trim();
  }
  return { kind: row.kind, params, weight: Number(row.weight) > 0 ? Number(row.weight) : 1 };
}

/**
 * The two arms of an experiment from the card's form: `shareA` is a percent
 * for arm A, arm B gets the rest. Returns null when the arms are not a valid
 * pair (the same version twice, or a share outside 1..99).
 */
export function experimentArms(versionA, versionB, shareA) {
  const a = Number(shareA);
  if (versionA === '' || versionB === '' || String(versionA) === String(versionB)) return null;
  if (!(a >= 1 && a <= 99)) return null;
  const asVersion = (v) => (v === 'current' ? 'current' : Number(v));
  const first = Math.round(a) / 100;
  return [
    { version: asVersion(versionA), share: first },
    { version: asVersion(versionB), share: Math.round((1 - first) * 100) / 100 },
  ];
}
