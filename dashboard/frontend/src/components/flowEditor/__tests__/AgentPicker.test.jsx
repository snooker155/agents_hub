import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n';
import { AgentPicker } from '../NodeInspector';
import { isUnchosenAgent } from '../graphHelpers';

// The palette has one abstract agent node; the inspector's picker is where it
// becomes a particular agent.

const AGENTS = [
  { id: 'writer', name: 'Writer', description: 'Writes things', group: 'design' },
  { id: 'coder', name: 'Coder', description: 'Writes code', group: 'development' },
];

const show = (data, onPatch = vi.fn()) => {
  render(
    <I18nProvider>
      <AgentPicker node={{ id: 'n', data }} agents={AGENTS} onPatch={onPatch} />
    </I18nProvider>,
  );
  return onPatch;
};

describe('AgentPicker', () => {
  it('asks for an agent while none is chosen', () => {
    show({ label: 'Agent', category: 'agent', agent_id: '' });
    expect(screen.getByText(/cannot run until this node has an agent/)).toBeInTheDocument();
  });

  it('picks an agent and takes its name while the label is the default', () => {
    const onPatch = show({ label: 'Agent', category: 'agent', agent_id: '' });
    fireEvent.change(screen.getByTestId('node-agent-picker'), { target: { value: 'writer' } });
    expect(onPatch).toHaveBeenCalledWith({
      agent_id: 'writer', label: 'Writer', description: 'Writes things', domain: 'design',
    });
  });

  it('keeps a label the person wrote', () => {
    const onPatch = show({ label: 'Draft the reply', category: 'agent', agent_id: 'writer' });
    fireEvent.change(screen.getByTestId('node-agent-picker'), { target: { value: 'coder' } });
    expect(onPatch.mock.calls[0][0].label).toBeUndefined();
    expect(onPatch.mock.calls[0][0].agent_id).toBe('coder');
  });
});

describe('isUnchosenAgent', () => {
  it('is true only for an agent node with no agent', () => {
    expect(isUnchosenAgent({ category: 'agent', agent_id: '' })).toBe(true);
    expect(isUnchosenAgent({ category: 'agent', agent_id: 'writer' })).toBe(false);
    expect(isUnchosenAgent({ category: 'processor', entity_id: 'summarize' })).toBe(false);
    expect(isUnchosenAgent({})).toBe(false);
  });
});
