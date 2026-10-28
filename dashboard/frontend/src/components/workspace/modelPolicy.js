/**
 * Pure helpers for a workspace's model policy (workspace/model_policy.py):
 * which models the pickers may offer and the body the policy card saves.
 * A policy is `{models: [{provider, model}], default, temperature, effort,
 * max_tokens}` or null.
 */

export const modelKey = (provider, model) => `${provider}/${model}`;

/** Whether the policy lets `provider`/`model` be chosen; no policy allows all. */
export function policyAllows(policy, provider, model) {
  if (!policy?.models?.length) return true;
  return policy.models.some((m) => m.provider === provider && m.model === model);
}

/** `models` ([{provider, model}]) cut down to the policy's list, order kept. */
export function filterByPolicy(models, policy) {
  if (!policy?.models?.length) return models;
  return models.filter((m) => policyAllows(policy, m.provider, m.model));
}

/** A policy with exactly one model: it is the only choice everywhere. */
export const isSingleModelPolicy = (policy) => (policy?.models?.length || 0) === 1;

/** The policy's list as the pickers show it, in the policy's own order. */
export const policyModels = (policy) => policy?.models || [];

/** [{provider, models: [id]}] in first-seen order, for grouped lists. */
export function groupByProvider(models) {
  const groups = [];
  for (const { provider, model } of models) {
    let group = groups.find((g) => g.provider === provider);
    if (!group) { group = { provider, models: [] }; groups.push(group); }
    group.models.push(model);
  }
  return groups;
}

/** The card's form state from a stored policy (or an empty one). */
export function formFromPolicy(policy) {
  return {
    selected: (policy?.models || []).map((m) => modelKey(m.provider, m.model)),
    defaultKey: policy?.default ? modelKey(policy.default.provider, policy.default.model) : '',
    temperature: policy?.temperature != null ? String(policy.temperature) : '',
    effort: policy?.effort || '',
    maxTokens: policy?.max_tokens != null ? String(policy.max_tokens) : '',
    // On unless the policy says otherwise: special models are the
    // administrator's while a policy holds.
    lockSpecialModels: policy?.lock_special_models !== false,
  };
}

const parseKey = (key) => {
  const at = key.indexOf('/');
  return { provider: key.slice(0, at), model: key.slice(at + 1) };
};

/** The PUT body for the form. The default falls back to the first model. */
export function buildPolicyBody(form) {
  const selected = form.selected || [];
  const defaultKey = selected.includes(form.defaultKey) ? form.defaultKey : selected[0];
  const temperature = parseFloat(form.temperature);
  const maxTokens = parseInt(form.maxTokens, 10);
  return {
    models: selected.map(parseKey),
    ...(defaultKey ? { default: parseKey(defaultKey) } : {}),
    temperature: Number.isNaN(temperature) ? null : temperature,
    effort: form.effort || null,
    max_tokens: Number.isNaN(maxTokens) ? null : maxTokens,
    lock_special_models: form.lockSpecialModels !== false,
  };
}

/** Whether the form differs from the stored policy. */
export function policyFormDirty(form, policy) {
  return JSON.stringify(buildPolicyBody(form)) !== JSON.stringify(buildPolicyBody(formFromPolicy(policy)));
}
