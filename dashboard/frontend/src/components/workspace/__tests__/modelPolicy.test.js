import { describe, it, expect } from 'vitest';
import {
  buildPolicyBody, filterByPolicy, formFromPolicy, groupByProvider, isSingleModelPolicy,
  policyAllows, policyFormDirty,
} from '../modelPolicy';

const catalog = [
  { provider: 'openai', model: 'gpt-5' },
  { provider: 'openai', model: 'gpt-5-mini' },
  { provider: 'anthropic', model: 'claude-sonnet' },
];
const policy = {
  models: [{ provider: 'anthropic', model: 'claude-sonnet' }, { provider: 'openai', model: 'gpt-5' }],
  default: { provider: 'anthropic', model: 'claude-sonnet' },
  temperature: 0.2, effort: 'medium', max_tokens: null,
};

describe('model policy helpers', () => {
  it('offers every model when there is no policy', () => {
    expect(filterByPolicy(catalog, null)).toBe(catalog);
    expect(filterByPolicy(catalog, { models: [] })).toBe(catalog);
    expect(policyAllows(null, 'openai', 'x')).toBe(true);
  });

  it('keeps only the policy models, in catalog order', () => {
    expect(filterByPolicy(catalog, policy)).toEqual([catalog[0], catalog[2]]);
    expect(policyAllows(policy, 'openai', 'gpt-5-mini')).toBe(false);
  });

  it('knows a one model policy', () => {
    expect(isSingleModelPolicy({ models: [policy.models[0]] })).toBe(true);
    expect(isSingleModelPolicy(policy)).toBe(false);
    expect(isSingleModelPolicy(null)).toBe(false);
  });

  it('groups by provider in first seen order', () => {
    expect(groupByProvider(catalog)).toEqual([
      { provider: 'openai', models: ['gpt-5', 'gpt-5-mini'] },
      { provider: 'anthropic', models: ['claude-sonnet'] },
    ]);
  });

  it('round trips a policy through the form and the PUT body', () => {
    const form = formFromPolicy(policy);
    expect(form.selected).toEqual(['anthropic/claude-sonnet', 'openai/gpt-5']);
    expect(buildPolicyBody(form)).toEqual({
      models: policy.models, default: policy.default, temperature: 0.2, effort: 'medium', max_tokens: null,
      lock_special_models: true,
    });
    expect(policyFormDirty(form, policy)).toBe(false);
    expect(policyFormDirty({ ...form, effort: 'high' }, policy)).toBe(true);
    expect(policyFormDirty({ ...form, lockSpecialModels: false }, policy)).toBe(true);
    expect(buildPolicyBody(formFromPolicy({ ...policy, lock_special_models: false })).lock_special_models).toBe(false);
  });

  it('defaults to the first model and keeps slashes in model ids', () => {
    const body = buildPolicyBody({ selected: ['ollama/org/model:7b'], defaultKey: 'gone/x', temperature: '', effort: '', maxTokens: '4096' });
    expect(body.models).toEqual([{ provider: 'ollama', model: 'org/model:7b' }]);
    expect(body.default).toEqual({ provider: 'ollama', model: 'org/model:7b' });
    expect(body.max_tokens).toBe(4096);
    expect(body.temperature).toBeNull();
  });
});
