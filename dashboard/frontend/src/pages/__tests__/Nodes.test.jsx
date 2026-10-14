import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The Nodes page (docs/hub-link.md, "Nodes"): a node's token is shown once,
// and a node row says what the machine offers.

const ok = (data) => Promise.resolve({ data });

const listNodes = vi.fn(() => ok({ nodes: [] }));
const createNode = vi.fn(() => ok({
  node: { id: 'node-abc', name: 'laptop', online: false, models: [], capabilities: {} },
  token: 'ahnode_secret-token-value',
}));

const updateNode = vi.fn(() => ok({}));
const getNodePool = vi.fn(() => ok({ online: 1, capacity: 4, in_flight: 1, models: [{ id: 'm', nodes: ['node-abc'] }], backend_id: 'nodes', sandbox_provider: 'nodes' }));

vi.mock('../../api/hubLink', () => ({
  listNodes: (...args) => listNodes(...args),
  createNode: (...args) => createNode(...args),
  deleteNode: vi.fn(),
  rotateNodeToken: vi.fn(),
  updateNode: (...args) => updateNode(...args),
  getNodePool: (...args) => getNodePool(...args),
}));
vi.mock('../../api/workspaces', () => ({
  getWorkspaces: () => ok([{ name: 'default' }, { name: 'team-a' }]),
}));

import Nodes from '../Nodes';

const show = () => render(
  <I18nProvider><MemoryRouter><Nodes /></MemoryRouter></I18nProvider>,
);

beforeEach(() => {
  listNodes.mockClear();
  createNode.mockClear();
  updateNode.mockClear();
  getNodePool.mockClear();
  listNodes.mockImplementation(() => ok({ nodes: [] }));
});

describe('Nodes', () => {
  it('starts empty and shows the token once after creating a node', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/no nodes yet/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /add a node/i }));
    fireEvent.change(screen.getByPlaceholderText('laptop'), { target: { value: 'laptop' } });
    fireEvent.click(screen.getByRole('button', { name: /create and show the token/i }));

    await waitFor(() => expect(screen.getByTestId('node-token')).toBeInTheDocument());
    expect(screen.getByText('ahnode_secret-token-value')).toBeInTheDocument();
    expect(createNode).toHaveBeenCalledWith({ name: 'laptop', workspaces: null });
  });

  it('lists what a node offers', async () => {
    listNodes.mockImplementation(() => ok({ nodes: [{
      id: 'node-abc', name: 'laptop', online: true, host: 'mac', version: '0.10.0',
      models: [{ id: 'hub-local/qwen' }], backend_id: 'node-laptop',
      capabilities: { sandbox: true, sandbox_provider: 'docker' }, sandbox_provider: 'node:node-abc',
      workspaces: ['team-a'], last_seen: '2026-10-12T10:00:00Z',
    }] }));
    show();
    await waitFor(() => expect(screen.getByTestId('node-row')).toBeInTheDocument());
    expect(screen.getByText('laptop')).toBeInTheDocument();
    expect(screen.getByText('Online')).toBeInTheDocument();
    expect(screen.getByText('node-laptop')).toBeInTheDocument();
    expect(screen.getByText('node:node-abc')).toBeInTheDocument();
    expect(within(screen.getByTestId('node-row')).getByText('team-a')).toBeInTheDocument();
  });

  const node = (extra = {}) => ({
    id: 'node-abc', name: 'laptop', online: true, models: [], capabilities: {},
    trust: 'untrusted', capacity: 4, in_flight: 1, max_parallel: null, announced_capacity: 4,
    disabled: false, workspaces: null, ...extra,
  });

  it('shows the trust badge, the load and lets an admin change trust', async () => {
    listNodes.mockImplementation(() => ok({ nodes: [node()], can_set_trust: true }));
    show();
    await waitFor(() => expect(screen.getByTestId('node-row')).toBeInTheDocument());
    expect(screen.getByTestId('node-trust-badge')).toHaveTextContent('Untrusted');
    expect(screen.getByTestId('node-load')).toHaveTextContent('1 / 4');
    const select = screen.getByTestId('node-trust-select');
    expect(select).not.toBeDisabled();
    expect(screen.getByRole('option', { name: /attested/i })).toBeDisabled();
    fireEvent.change(select, { target: { value: 'trusted' } });
    await waitFor(() => expect(updateNode).toHaveBeenCalledWith('node-abc', { trust: 'trusted' }));
  });

  it('locks the trust select for a non admin', async () => {
    listNodes.mockImplementation(() => ok({ nodes: [node({ trust: 'trusted' })], can_set_trust: false }));
    show();
    await waitFor(() => expect(screen.getByTestId('node-trust-select')).toBeDisabled());
  });

  it('edits the cap and workspaces, and toggles disabled', async () => {
    listNodes.mockImplementation(() => ok({ nodes: [node()], can_set_trust: true }));
    show();
    await waitFor(() => expect(screen.getByTestId('node-edit')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('node-edit'));
    fireEvent.change(screen.getByTestId('node-cap-input'), { target: { value: '2' } });
    fireEvent.change(screen.getByTestId('node-ws-input'), { target: { value: 'team-a, team-b' } });
    fireEvent.click(screen.getByTestId('node-edit-save'));
    await waitFor(() => expect(updateNode).toHaveBeenCalledWith('node-abc', { max_parallel: 2, workspaces: ['team-a', 'team-b'] }));
    fireEvent.click(screen.getByTestId('node-toggle'));
    await waitFor(() => expect(updateNode).toHaveBeenCalledWith('node-abc', { disabled: true }));
  });

  it('shows the pool card and why a node is excluded for a workspace', async () => {
    listNodes.mockImplementation(() => ok({ nodes: [node()], can_set_trust: true }));
    getNodePool.mockImplementation((params) => ok(params?.workspace ? {
      online: 1, capacity: 4, in_flight: 1, models: [], backend_id: null, sandbox_provider: 'nodes',
      workspace: 'team-a', data_class: 'internal', eligible: [],
      excluded: [{ id: 'node-abc', name: 'laptop', kind: 'policy', reason: 'node is untrusted, work is internal' }],
    } : {
      online: 1, capacity: 4, in_flight: 1, models: [{ id: 'm', nodes: ['node-abc'] }], backend_id: 'nodes', sandbox_provider: 'nodes',
    }));
    show();
    await waitFor(() => expect(screen.getByTestId('pool-online')).toHaveTextContent('1'));
    expect(screen.getByTestId('pool-capacity')).toHaveTextContent('4');
    expect(screen.getByRole('link', { name: /open models/i })).toHaveAttribute('href', '/models');
    await waitFor(() => expect(screen.getByRole('option', { name: 'team-a' })).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('pool-workspace'), { target: { value: 'team-a' } });
    await waitFor(() => expect(screen.getByTestId('pool-eligibility')).toBeInTheDocument());
    expect(screen.getByTestId('pool-eligibility')).toHaveTextContent('Data class: internal');
    expect(screen.getByTestId('pool-eligibility')).toHaveTextContent('node is untrusted, work is internal');
    expect(getNodePool).toHaveBeenCalledWith({ workspace: 'team-a' });
  });
});
