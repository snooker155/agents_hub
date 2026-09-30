import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getAgentHandoffs: vi.fn(),
  updateAgentHandoffs: vi.fn(),
  getAgents: vi.fn(),
}));
vi.mock('../../api/handoffs', async (importOriginal) => ({ ...(await importOriginal()), ...api }));
vi.mock('../workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: 'default' }) }));
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k) }),
}));

import HandoffsCard from '../agent/HandoffsCard';

const AGENTS = [
  { id: 'front', name: 'Front desk' },
  { id: 'billing', name: 'Billing', description: 'Invoices' },
  { id: 'refunds', name: 'Refunds' },
];

describe('HandoffsCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getAgents.mockResolvedValue({ data: AGENTS });
    api.getAgentHandoffs.mockResolvedValue({ data: { handoffs: [], handoff_history: 'full' } });
    api.updateAgentHandoffs.mockImplementation(async (_id, payload) => ({ data: payload }));
  });

  it('lists the other agents of the workspace, not the agent itself', async () => {
    render(<HandoffsCard agentId="front" agent={{ id: 'front' }} />);
    await screen.findByText('Billing');
    expect(screen.getByText('Refunds')).toBeInTheDocument();
    expect(screen.queryByText('Front desk')).toBeNull();
    expect(screen.getByText('handoffs.noTargets')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'handoffs.save' })).toBeDisabled();
  });

  it('saves the picked targets and a last_n filter with its count', async () => {
    const onSaved = vi.fn();
    render(<HandoffsCard agentId="front" agent={{ id: 'front' }} onSaved={onSaved} />);
    await screen.findByText('Billing');

    fireEvent.click(screen.getByLabelText('handoffs.allowTarget {"agent":"Billing"}'));
    fireEvent.change(screen.getByLabelText('handoffs.historyTitle'), { target: { value: 'last_n' } });
    fireEvent.change(screen.getByLabelText('handoffs.lastNLabel'), { target: { value: '6' } });
    fireEvent.click(screen.getByRole('button', { name: 'handoffs.save' }));

    await waitFor(() => expect(api.updateAgentHandoffs).toHaveBeenCalledWith('front', {
      handoffs: ['billing'], handoff_history: 'last_n:6',
    }));
    expect(await screen.findByText('handoffs.saved')).toBeInTheDocument();
    expect(onSaved).toHaveBeenCalled();
  });

  it('shows the stored filter and refuses a count out of range', async () => {
    api.getAgentHandoffs.mockResolvedValue({ data: { handoffs: ['refunds'], handoff_history: 'last_n:4' } });
    render(<HandoffsCard agentId="front" agent={{ id: 'front' }} />);
    await screen.findByText('Billing');
    expect(screen.getByLabelText('handoffs.historyTitle')).toHaveValue('last_n');
    expect(screen.getByLabelText('handoffs.lastNLabel')).toHaveValue(4);
    expect(screen.getByLabelText('handoffs.allowTarget {"agent":"Refunds"}')).toBeChecked();

    fireEvent.change(screen.getByLabelText('handoffs.lastNLabel'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'handoffs.save' }));
    expect(await screen.findByText(/handoffs.lastNInvalid/)).toBeInTheDocument();
    expect(api.updateAgentHandoffs).not.toHaveBeenCalled();
  });

  it('keeps a saved target the workspace no longer lists, so it can be removed', async () => {
    api.getAgentHandoffs.mockResolvedValue({ data: { handoffs: ['gone'], handoff_history: 'full' } });
    render(<HandoffsCard agentId="front" agent={{ id: 'front' }} />);
    const box = await screen.findByLabelText('handoffs.allowTarget {"agent":"gone"}');
    expect(box).toBeChecked();
    fireEvent.click(box);
    fireEvent.click(screen.getByRole('button', { name: 'handoffs.save' }));
    await waitFor(() => expect(api.updateAgentHandoffs).toHaveBeenCalledWith('front', {
      handoffs: [], handoff_history: 'full',
    }));
  });
});
