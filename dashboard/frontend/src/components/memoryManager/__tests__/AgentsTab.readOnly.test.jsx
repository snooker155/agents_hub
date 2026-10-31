import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { AgentsTab } from '../AgentsTab';
import { agentPools, agentReadOnlyPools } from '../helpers';
import { I18nProvider } from '../../../i18n';

const updateAgentMemory = vi.fn(() => Promise.resolve({ data: {} }));

vi.mock('../../../api', () => ({
  getAgents: () => Promise.resolve({
    data: [{
      id: 'a1', name: 'Scout', memory_type: 'shared',
      memory_data: ['p1', { id: 'p2', read_only: true }, 'p3'],
    }],
  }),
  updateAgentMemory: (...a) => updateAgentMemory(...a),
  getWorkspacePersonalMemory: () => Promise.resolve({ data: null }),
}));

const MEMORIES = [
  { id: 'p1', name: 'Primary pool', description: '', notes: [], structured_data: {} },
  { id: 'p2', name: 'Locked pool', description: 'Save facts', notes: [], structured_data: {} },
  { id: 'p3', name: 'Bare pool', description: '', notes: [], structured_data: {} },
];

describe('agentPools with dict entries', () => {
  it('returns ids for both shapes and the read only set', () => {
    const a = { memory_type: 'shared', memory_data: ['p1', { id: 'p2', read_only: true }, { id: 'p3' }] };
    expect(agentPools(a)).toEqual(['p1', 'p2', 'p3']);
    expect([...agentReadOnlyPools(a)]).toEqual(['p2']);
    expect(agentPools({ memory_type: 'shared', memory_data: { id: 'p9', read_only: true } })).toEqual(['p9']);
    expect(agentReadOnlyPools({ memory_type: 'shared', memory_data: 'p1' }).size).toBe(0);
  });
});

describe('AgentsTab read only pools', () => {
  it('keeps read_only entries when changing the assignment and sends them back', async () => {
    render(<I18nProvider><AgentsTab memories={MEMORIES} workspaceFilter="default" /></I18nProvider>);
    await waitFor(() => expect(screen.getByText('Scout')).toBeInTheDocument());
    const row = screen.getByText('Scout').closest('tr');
    expect(row.textContent).toMatch(/Locked pool \(read only\)/);
    expect(row.textContent).not.toMatch(/Bare pool \(writes\)/);
    fireEvent.click(screen.getByText('Change'));
    // The notes under the additional pools: switch, and no description.
    expect(screen.getByText('Read only: the switch is on.')).toBeInTheDocument();
    expect(screen.getByText('Read only until the pool gets a description.')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(updateAgentMemory).toHaveBeenCalledWith('a1', {
      memory_type: 'shared',
      memory_data: ['p1', { id: 'p2', read_only: true }, 'p3'],
      workspace: 'default',
    }));
  });

  it('sends a primary pool marked read only as an entry', async () => {
    updateAgentMemory.mockClear();
    render(<I18nProvider><AgentsTab memories={MEMORIES} workspaceFilter="default" /></I18nProvider>);
    await waitFor(() => expect(screen.getByText('Scout')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Change'));
    const boxes = screen.getAllByLabelText('Read only');
    fireEvent.click(boxes[0]); // primary
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(updateAgentMemory).toHaveBeenCalled());
    expect(updateAgentMemory.mock.calls[0][1].memory_data[0]).toEqual({ id: 'p1', read_only: true });
  });
});
