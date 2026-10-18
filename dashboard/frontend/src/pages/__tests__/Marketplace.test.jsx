import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The Agents and Flows tabs: a card per published agent or flow, and the
// Add button that puts one into the selected workspace.

const ok = (data) => Promise.resolve({ data });

const AGENT = {
  id: 'triage',
  name: 'Triage',
  description: 'Sorts inbound requests.',
  domain: 'support',
  tools: ['web_search'],
  owner_workspace: 'ops',
  in_workspace: false,
};

const FLOW = {
  id: 'flow-1',
  name: 'Weekly digest',
  description: 'Collects the week and writes it up.',
  nodes_count: 2,
  agents: [{ id: 'triage', name: 'Triage' }],
  in_workspace: false,
};

const getMarketplaceAgents = vi.fn(() => ok([AGENT]));
const getMarketplaceFlows = vi.fn(() => ok([FLOW]));
const addAgentToWorkspace = vi.fn(() => ok({}));
const addFlowToWorkspace = vi.fn(() => ok({ added_agents: ['triage'] }));

vi.mock('../../api', () => ({
  getMarketplaceAgents: (...args) => getMarketplaceAgents(...args),
  getMarketplaceFlows: (...args) => getMarketplaceFlows(...args),
  addAgentToWorkspace: (...args) => addAgentToWorkspace(...args),
  addFlowToWorkspace: (...args) => addFlowToWorkspace(...args),
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'acme' }),
}));

import Marketplace from '../Marketplace';

const show = () => render(
  <I18nProvider><MemoryRouter><Marketplace /></MemoryRouter></I18nProvider>,
);

beforeEach(() => {
  getMarketplaceAgents.mockClear();
  getMarketplaceFlows.mockClear();
  addAgentToWorkspace.mockClear();
  addFlowToWorkspace.mockClear();
});

describe('Marketplace', () => {
  it('lists published agents for the selected workspace and adds one', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Triage')).toBeInTheDocument());
    expect(getMarketplaceAgents).toHaveBeenCalledWith('acme');
    expect(screen.getByText(/agents \(1\)/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));
    await waitFor(() => expect(addAgentToWorkspace).toHaveBeenCalledWith('acme', 'triage'));
  });

  it('switches to the Flows tab and adds a flow with its agents', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/flows \(1\)/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/flows \(1\)/i));

    await waitFor(() => expect(screen.getByText('Weekly digest')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /add with agents/i }));
    await waitFor(() => expect(addFlowToWorkspace).toHaveBeenCalledWith('acme', 'flow-1'));
  });
});
