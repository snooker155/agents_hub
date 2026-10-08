import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, it, expect, vi } from 'vitest';
import { AgentsTab } from '../AgentsTab';
import { I18nProvider } from '../../../i18n';

vi.mock('../../../api', () => ({
  getAgents: () => Promise.resolve({
    data: [
      { id: 'a1', name: 'Scout', domain: 'jobs', memory_type: 'shared', memory_data: 'pool-1' },
      { id: 'a2', name: 'Helper', domain: 'support', memory_type: 'none', memory_data: null },
      { id: 'main', name: 'Main', domain: 'system', memory_type: 'none', memory_data: null },
    ],
  }),
  updateAgentMemory: () => Promise.resolve({ data: {} }),
  getWorkspacePersonalMemory: vi.fn(),
}));

import { getWorkspacePersonalMemory } from '../../../api';

const MEMORIES = [
  { id: 'pool-1', name: 'Team pool', notes: [], structured_data: {} },
  { id: 'pers-1', name: 'Personal memory: you', kind: 'personal', workspace: 'default', notes: [], structured_data: {} },
];

const show = (workspaceFilter = 'default') => render(
  <I18nProvider>
    <AgentsTab memories={MEMORIES} workspaceFilter={workspaceFilter} />
  </I18nProvider>,
);

// Personal memory on for Main (no pool of its own, so the personal pool is its
// only one) and for Scout (which gets it next to its own pool).
const personalOn = (enabled = true) => getWorkspacePersonalMemory.mockResolvedValue({
  data: { workspace: 'default', enabled, main_agent: 'main', agents: { main: true, a1: true } },
});

beforeEach(() => personalOn());

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

  it('counts the agents that reach the personal pool through the personal memory switch', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Main').closest('tr').textContent).toMatch(/My personal memory/));
    expect(screen.getByText('Main').closest('tr').textContent).toMatch(/Connected \(personal\)/);
    // Scout keeps its own pool as the primary one and has the personal one too.
    const scout = screen.getByText('Scout').closest('tr').textContent;
    expect(scout).toMatch(/Team pool/);
    expect(scout).toMatch(/\+ My personal memory \(personal\)/);
    expect(scout).toMatch(/Connected ×2 \(personal\)/);
    const usage = screen.getAllByText('My personal memory').find((el) => !el.closest('tr')).parentElement.parentElement;
    expect(usage.textContent).toMatch(/2 agents/);
  });

  // The default workspace (no filter) lists every workspace's pools, so a
  // personal pool names the workspace it belongs to.
  it('names the workspace of the personal pool in the default workspace', async () => {
    show(null);
    await waitFor(() => expect(screen.getByText('Main').closest('tr').textContent).toMatch(/My personal memory · default/));
  });

  it('counts nobody on the personal pool when the workspace has it off', async () => {
    personalOn(false);
    show();
    await waitFor(() => expect(screen.getByText('Main')).toBeInTheDocument());
    expect(screen.getByText('Main').closest('tr').textContent).toMatch(/None/i);
  });
});
