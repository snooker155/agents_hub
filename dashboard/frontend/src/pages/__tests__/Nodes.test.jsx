import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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

vi.mock('../../api/hubLink', () => ({
  listNodes: (...args) => listNodes(...args),
  createNode: (...args) => createNode(...args),
  deleteNode: vi.fn(),
  rotateNodeToken: vi.fn(),
}));

import Nodes from '../Nodes';

const show = () => render(
  <I18nProvider><MemoryRouter><Nodes /></MemoryRouter></I18nProvider>,
);

beforeEach(() => {
  listNodes.mockClear();
  createNode.mockClear();
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
    expect(screen.getByText(/online/i)).toBeInTheDocument();
    expect(screen.getByText('node-laptop')).toBeInTheDocument();
    expect(screen.getByText('node:node-abc')).toBeInTheDocument();
    expect(screen.getByText('team-a')).toBeInTheDocument();
  });
});
