import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The Kits tab: a kit card per manifest, with connector chips, and an
// install dialog that shows the plan before anything is created.

const ok = (data) => Promise.resolve({ data });

const getMarketplaceAgents = vi.fn(() => ok([]));
const getMarketplaceFlows = vi.fn(() => ok([]));

const KIT = {
  id: 'support',
  name: 'Customer support',
  description: 'Triages inbound support messages.',
  industry: 'Support operations',
  icon: 'headset',
  version: '1.0.0',
  connectors: {
    required: [{ name: 'mail', configured: false }],
    optional: [{ name: 'slack', configured: true }],
  },
  rubrics: 'Graded on resolving from the knowledge pool first.',
  next_steps: 'Connect a mailbox.',
};

const listKits = vi.fn(() => ok([KIT]));
const getKit = vi.fn(() => ok({
  ...KIT,
  resources: [{ kind: 'agent', key: 'triage' }, { kind: 'agent', key: 'resolver' }],
  plan: { ok: true, changes: [
    { address: 'agent/triage', kind: 'agent', key: 'triage', action: 'create', hub_id: 'triage' },
    { address: 'agent/resolver', kind: 'agent', key: 'resolver', action: 'create', hub_id: 'resolver' },
  ], counts: { create: 2 } },
}));
const installKit = vi.fn(() => ok({
  plan: { ok: true, changes: [], counts: {} },
  result: { ok: true, applied: [], failed: [] },
  agents: ['triage', 'resolver'],
}));

vi.mock('../../api', () => ({
  getMarketplaceAgents: (...args) => getMarketplaceAgents(...args),
  getMarketplaceFlows: (...args) => getMarketplaceFlows(...args),
  addAgentToWorkspace: vi.fn(() => ok({})),
  addFlowToWorkspace: vi.fn(() => ok({})),
}));

vi.mock('../../api/kits', () => ({
  listKits: (...args) => listKits(...args),
  getKit: (...args) => getKit(...args),
  installKit: (...args) => installKit(...args),
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'acme' }),
}));

import Marketplace from '../Marketplace';

const show = () => render(
  <I18nProvider><MemoryRouter><Marketplace /></MemoryRouter></I18nProvider>,
);

beforeEach(() => {
  listKits.mockClear();
  getKit.mockClear();
  installKit.mockClear();
});

describe('Marketplace — Kits tab', () => {
  it('lists a kit card with its connector chips', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/kits \(1\)/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/kits \(1\)/i));

    await waitFor(() => expect(screen.getByText('Customer support')).toBeInTheDocument());
    expect(screen.getByText('mail')).toBeInTheDocument();
    expect(screen.getByText('slack')).toBeInTheDocument();
  });

  it('opens the install dialog showing the plan, then installs', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/kits \(1\)/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/kits \(1\)/i));
    await waitFor(() => expect(screen.getByText('Customer support')).toBeInTheDocument());

    fireEvent.click(screen.getAllByText(/install/i)[0]);

    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(getKit).toHaveBeenCalledWith('support', 'acme'));
    await waitFor(() => expect(within(dialog).getByText('agent/triage')).toBeInTheDocument());
    expect(within(dialog).getAllByText('create').length).toBeGreaterThan(0);

    fireEvent.click(within(dialog).getByRole('button', { name: /install/i }));

    await waitFor(() => expect(installKit).toHaveBeenCalledWith('support', { workspace: 'acme' }));
    await waitFor(() => expect(within(dialog).getByText(/installed into workspace/i)).toBeInTheDocument());
  });
});
