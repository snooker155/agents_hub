import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The page someone lands on when they are deciding whether this product can
// watch the agents they already have. Two things are worth holding onto: the
// empty state has to read as a choice rather than a form, and the token has to
// be impossible to miss, because it is shown exactly once.

const ok = (data) => Promise.resolve({ data });

const listConnections = vi.fn(() => ok({ connections: [], kinds: [] }));
const createConnection = vi.fn(() => ok({
  connection: { id: 'billing-graph', name: 'Billing graph', kind: 'langgraph' },
  token: 'ahc_secret-token-value',
}));

vi.mock('../../api', () => ({
  listConnections: (...args) => listConnections(...args),
  createConnection: (...args) => createConnection(...args),
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'default' }),
}));

import Connections from '../Connections';

const show = () => render(
  <I18nProvider><MemoryRouter><Connections /></MemoryRouter></I18nProvider>,
);

beforeEach(() => {
  listConnections.mockClear();
  createConnection.mockClear();
  listConnections.mockImplementation(() => ok({ connections: [], kinds: [] }));
});

describe('Connections — the empty state', () => {
  it('offers the three ways in, not a form', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/nothing connected yet/i)).toBeInTheDocument());

    expect(screen.getByText(/watch a graph you run/i)).toBeInTheDocument();
    expect(screen.getByText(/import an agent repo/i)).toBeInTheDocument();
    expect(screen.getByText(/call a running service/i)).toBeInTheDocument();
    // The reassurance is the point of the screen, not decoration.
    expect(screen.getByText(/leaves your code where it is/i)).toBeInTheDocument();
  });

  it('starts the observe path from the first tile', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/watch a graph you run/i)).toBeInTheDocument());

    fireEvent.click(screen.getByText(/watch a graph you run/i));

    expect(screen.getByText(/new connection/i)).toBeInTheDocument();
  });
});

describe('Connections — creating one', () => {
  it('shows the token once, with a snippet that already carries it', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/nothing connected yet/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^connect$/i }));

    fireEvent.change(screen.getByPlaceholderText('billing-graph'), { target: { value: 'billing-graph' } });
    fireEvent.click(screen.getByRole('button', { name: /create connection/i }));

    await waitFor(() => expect(screen.getByText('ahc_secret-token-value')).toBeInTheDocument());
    expect(screen.getByText(/never shown again/i)).toBeInTheDocument();
    // A snippet the token has to be pasted into by hand is a snippet that turns
    // a two-minute setup into a support conversation.
    const snippet = document.querySelector('pre').textContent;
    expect(snippet).toContain('ahc_secret-token-value');
    expect(snippet).toContain('HubTracer');
  });

  it('sends the workspace only when it is not the default', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/nothing connected yet/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^connect$/i }));
    fireEvent.change(screen.getByPlaceholderText('billing-graph'), { target: { value: 'c1' } });
    fireEvent.click(screen.getByRole('button', { name: /create connection/i }));

    await waitFor(() => expect(createConnection).toHaveBeenCalled());
    expect(createConnection.mock.calls[0][0].workspace).toBeNull();
  });
});

describe('Connections — the list', () => {
  it('says a connection has not reported rather than leaving it blank', async () => {
    listConnections.mockImplementation(() => ok({
      connections: [
        { id: 'fresh', name: 'Fresh', kind: 'langgraph', last_seen: null, stats: { runs: 0 } },
        { id: 'busy', name: 'Busy', kind: 'crewai', last_seen: '2026-09-20T10:00:00Z',
          stats: { runs: 12, failed: 2 } },
      ],
    }));
    show();

    await waitFor(() => expect(screen.getByText('Fresh')).toBeInTheDocument());
    expect(screen.getByText(/has not reported yet/i)).toBeInTheDocument();
    expect(screen.getByText(/12 runs/i)).toBeInTheDocument();
    expect(screen.getByText(/2 failed/i)).toBeInTheDocument();
  });

  it('marks a disabled connection, because its token is being refused', async () => {
    listConnections.mockImplementation(() => ok({
      connections: [{ id: 'off', name: 'Off', kind: 'http', disabled: true, stats: {} }],
    }));
    show();

    await waitFor(() => expect(screen.getByText('Off')).toBeInTheDocument());
    expect(screen.getByText(/disabled/i)).toBeInTheDocument();
  });
});
