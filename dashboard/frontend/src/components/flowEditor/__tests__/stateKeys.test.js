import { describe, expect, it } from 'vitest';
import { humanizeKey, initialFlowState, stateKeyLabel } from '../stateKeys';

describe('state key labels', () => {
  it('writes a declared key out as words', () => {
    expect(humanizeKey('market_findings')).toBe('Market findings');
    expect(humanizeKey('draftReport')).toBe('Draft report');
    expect(humanizeKey('answer')).toBe('Answer');
  });

  it('names the hub\'s own keys, falling back to the words', () => {
    const t = (key, { defaultValue }) => (key === 'flowEditor.stateKeys.user_request' ? 'Запрос' : defaultValue);
    expect(stateKeyLabel('user_request', t)).toBe('Запрос');
    expect(stateKeyLabel('brief_text', t)).toBe('Brief text');
  });
});

describe('a flow\'s starting state', () => {
  it('always holds the request, empty until something is asked', () => {
    expect(initialFlowState({})).toEqual({ user_request: '' });
    expect(initialFlowState({ state: null })).toEqual({ user_request: '' });
  });

  it('takes the flow\'s declared keys, its own request included', () => {
    expect(initialFlowState({ state: { topic: 'coffee', user_request: 'Weekly digest' } }))
      .toEqual({ user_request: 'Weekly digest', topic: 'coffee' });
  });
});
