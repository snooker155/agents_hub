import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { AgentsTab } from '../AgentsTab';
import { I18nProvider } from '../../../i18n';

vi.mock('../../../api', () => ({
  getAgents: () => Promise.resolve({
    data: [
      { id: 'a1', name: 'Scout', domain: 'jobs', memory_type: 'shared', memory_data: 'pool-1' },
      { id: 'a2', name: 'Helper', domain: 'support', memory_type: 'none', memory_data: null },
    ],
  }),
  updateAgentMemory: () => Promise.resolve({ data: {} }),
}));

const MEMORIES = [{ id: 'pool-1', name: 'Team pool', notes: [], structured_data: {} }];

const show = () => render(
  <I18nProvider>
    <AgentsTab memories={MEMORIES} workspaceFilter="default" />
  </I18nProvider>,
);

describe('AgentsTab', () => {
  it('lists agents from the API and names the pool each is connected to', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Scout')).toBeInTheDocument());
    expect(screen.getByText('Helper')).toBeInTheDocument();
    // "Team pool" also names the pool-usage row above the agent table, so this
    // checks the agent row specifically rather than the whole page.
    const scoutRow = screen.getByText('Scout').closest('tr');
    expect(scoutRow.textContent).toMatch(/Team pool/);
  });

  it('marks an agent with no assigned pool as having none', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Helper')).toBeInTheDocument());
    // "Helper" has no memory_data, so its pool cell reads the "none" placeholder
    // rather than a pool name.
    const row = screen.getByText('Helper').closest('tr');
    expect(row.textContent).toMatch(/None/i);
  });
});
