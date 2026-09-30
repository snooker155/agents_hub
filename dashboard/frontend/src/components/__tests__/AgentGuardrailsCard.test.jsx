import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  getGuardrails: vi.fn(),
  updateAgentGuardrails: vi.fn(),
}));
vi.mock('../../api/guardrails', () => api);
vi.mock('../workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: 'w1' }) }));
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k) }),
}));

import AgentGuardrailsCard from '../agent/AgentGuardrailsCard';

const ALWAYS_ON = {
  id: 'g-all', name: 'no-secrets-out', description: '', workspace: null,
  stage: 'output', kind: 'pii', config: { detectors: ['api_key'] },
  action: 'block', applies_to: 'all', enabled: true, fail_closed: true, model: null,
};
const SELECTABLE = {
  id: 'g-sel', name: 'tone-check', description: '', workspace: 'w1',
  stage: 'output', kind: 'judge', config: { instruction: 'be polite' },
  action: 'warn', applies_to: 'selected', enabled: true, fail_closed: false, model: null,
};

describe('AgentGuardrailsCard', () => {
  beforeEach(() => {
    api.getGuardrails.mockReset();
    api.updateAgentGuardrails.mockReset();
    api.getGuardrails.mockResolvedValue({ data: [ALWAYS_ON, SELECTABLE] });
    api.updateAgentGuardrails.mockResolvedValue({ data: { guardrails: ['g-sel'] } });
  });

  it('lists the guardrails already applying and the selectable ones', async () => {
    render(<AgentGuardrailsCard agentId="a1" agent={{ id: 'a1', guardrails: [] }} />);
    await screen.findByText('no-secrets-out');
    expect(screen.getByText('tone-check')).toBeInTheDocument();
    expect(api.getGuardrails).toHaveBeenCalledWith('w1', false);
  });

  it('saves the selected guardrail ids and calls onSaved', async () => {
    const onSaved = vi.fn();
    render(<AgentGuardrailsCard agentId="a1" agent={{ id: 'a1', guardrails: [] }} onSaved={onSaved} />);
    await screen.findByText('tone-check');

    const checkbox = screen.getByRole('checkbox');
    fireEvent.click(checkbox);
    const save = screen.getByRole('button', { name: 'guardrails.agentCard.save' });
    fireEvent.click(save);

    await waitFor(() => expect(api.updateAgentGuardrails).toHaveBeenCalledWith('a1', ['g-sel']));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it('disables save until something changes', async () => {
    render(<AgentGuardrailsCard agentId="a1" agent={{ id: 'a1', guardrails: [] }} />);
    await screen.findByText('tone-check');
    expect(screen.getByRole('button', { name: 'guardrails.agentCard.save' })).toBeDisabled();
  });
});
