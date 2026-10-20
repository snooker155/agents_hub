import { describe, it, expect } from 'vitest';
import { flattenModelCatalog } from '../agentLoop';

const providers = {
  openai: { default: '', models: [{ id: 'gpt-4o', enabled: true }, { id: 'gpt-off', enabled: false }] },
  anthropic: { default: '', models: [{ id: 'claude-haiku', enabled: true }] },
};
const enabled = [
  { id: 'openai/gpt-4o', provider: 'openai', model: 'gpt-4o' },
  { id: 'anthropic/claude-haiku', provider: 'anthropic', model: 'claude-haiku' },
];

describe('flattenModelCatalog', () => {
  it('reads the document GET /models answers with', () => {
    expect(flattenModelCatalog({ providers, global_default: { provider: 'openai', model: 'gpt-4o' } })).toEqual(enabled);
  });

  it('reads a bare provider map', () => {
    expect(flattenModelCatalog(providers)).toEqual(enabled);
  });

  it('reads nothing off an empty answer', () => {
    expect(flattenModelCatalog(null)).toEqual([]);
    expect(flattenModelCatalog({ providers: {} })).toEqual([]);
  });
});
