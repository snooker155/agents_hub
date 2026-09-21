import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// One connection's page. The parts that matter: the graph it reported, the
// runs it produced (with the branch each took), and the credential, which is
// the only dangerous thing on the screen.

const ok = (data) => Promise.resolve({ data });

const connection = {
  id: 'billing-graph', name: 'Billing graph', kind: 'langgraph',
  token_hint: 'bcF4', last_seen: '2026-09-20T10:00:00Z', disabled: false,
  stats: { runs: 3, failed: 1, running: 0 },
  retention: { runs: 2000, source: 'default' },
  topology: {
    framework: 'langgraph',
    nodes: [{ id: 'triage', label: 'triage', kind: 'node' },
            { id: 'answer', label: 'answer', kind: 'node' }],
    edges: [{ source: 'triage', target: 'answer', conditional: true }],
  },
};

const runs = [
  { run_id: 'ing-1', status: 'completed', started_at: '2026-09-20T10:00:00Z',
    graph_path: ['triage', 'pricing', 'answer'],
    process: { token_usage: { total_tokens: 150 }, duration_ms: 2400 } },
  { run_id: 'ing-2', status: 'failed', started_at: '2026-09-20T09:00:00Z',
    graph_path: ['triage'], process: { token_usage: { total_tokens: 20 }, duration_ms: 900 } },
];

const getConnection = vi.fn(() => ok({ connection, runs, total: 2 }));
const pruneConnection = vi.fn(() => ok({ removed_runs: 7, removed_sessions: 7 }));
const rotateConnectionToken = vi.fn(() => ok({ connection, token: 'ahc_brand-new' }));
const updateConnection = vi.fn(() => ok({ connection }));
const deleteConnection = vi.fn(() => ok({ deleted: true }));
const answerConnectionRun = vi.fn(() => ok({ status: 'answered' }));

vi.mock('../../api', () => ({
  getConnection: (...a) => getConnection(...a),
  rotateConnectionToken: (...a) => rotateConnectionToken(...a),
  updateConnection: (...a) => updateConnection(...a),
  deleteConnection: (...a) => deleteConnection(...a),
  pruneConnection: (...a) => pruneConnection(...a),
  answerConnectionRun: (...a) => answerConnectionRun(...a),
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'team-a' }),
}));

import ConnectionDetail from '../ConnectionDetail';

const show = () => render(
  <I18nProvider>
    <MemoryRouter initialEntries={['/connections/billing-graph']}>
      <Routes>
        <Route path="/connections/:connectionId" element={<ConnectionDetail />} />
      </Routes>
    </MemoryRouter>
  </I18nProvider>,
);

beforeEach(() => {
  getConnection.mockClear();
  rotateConnectionToken.mockClear();
  updateConnection.mockClear();
});

const waitingRun = {
  run_id: 'ing-9', status: 'awaiting_input', started_at: '2026-09-20T11:00:00Z',
  graph_path: ['plan', 'approve'],
  pending_question: { question: 'Ship the release?', choices: ['yes', 'no'], node: 'approve' },
  process: {},
};

describe('ConnectionDetail — a run waiting on a person', () => {
  it('puts the question above everything else on the page', async () => {
    // It is the only thing here that is waiting on the person reading it.
    getConnection.mockImplementation(() => ok({
      connection, runs: [waitingRun, ...runs], total: 3,
    }));
    show();

    await waitFor(() => expect(screen.getByText('Ship the release?')).toBeInTheDocument());
    expect(screen.getByText(/waiting for an answer/i)).toBeInTheDocument();
    // The node it stopped in is the first thing someone looks for.
    expect(screen.getAllByText('approve').length).toBeGreaterThan(0);
  });

  it('offers the choices the graph itself gave, as buttons', async () => {
    getConnection.mockImplementation(() => ok({ connection, runs: [waitingRun], total: 1 }));
    show();
    await waitFor(() => expect(screen.getByText('Ship the release?')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'yes' }));

    await waitFor(() => expect(answerConnectionRun).toHaveBeenCalledWith(
      'billing-graph', 'ing-9', { value: 'yes' }, 'team-a'));
  });

  it('takes a typed answer when the graph offered no choices', async () => {
    getConnection.mockImplementation(() => ok({
      connection,
      runs: [{ ...waitingRun, pending_question: { question: 'How many?', node: 'ask' } }],
      total: 1,
    }));
    show();
    await waitFor(() => expect(screen.getByText('How many?')).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText(/your answer/i), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: /^answer$/i }));

    await waitFor(() => expect(answerConnectionRun).toHaveBeenCalledWith(
      'billing-graph', 'ing-9', { value: '12' }, 'team-a'));
  });

  it('says the answer is stored, not that the graph has resumed', async () => {
    // Nothing here can push it: the graph picks the answer up when it next asks.
    getConnection.mockImplementation(() => ok({ connection, runs: [waitingRun], total: 1 }));
    show();

    await waitFor(() => expect(screen.getByText(/picks it up the next time/i)).toBeInTheDocument());
  });

  it('lights the node the run stopped in on the reported graph', async () => {
    getConnection.mockImplementation(() => ok({
      connection: {
        ...connection,
        topology: {
          framework: 'langgraph',
          nodes: [{ id: 'plan', label: 'plan', kind: 'node' },
                  { id: 'approve', label: 'approve', kind: 'node' }],
          edges: [{ source: 'plan', target: 'approve' }],
        },
      },
      runs: [waitingRun], total: 1,
    }));
    const { container } = show();
    await waitFor(() => expect(screen.getByText('Ship the release?')).toBeInTheDocument());

    const paused = [...container.querySelectorAll('rect')].filter(
      (r) => (r.getAttribute('class') || '').includes('amber'));
    expect(paused).toHaveLength(1);
  });
});

describe('ConnectionDetail', () => {
  it('draws the graph the connection reported', async () => {
    const { container } = show();
    await waitFor(() => expect(screen.getByText('Billing graph')).toBeInTheDocument());

    expect(screen.getByText('triage')).toBeInTheDocument();
    // A conditional edge is dashed: "may or may not be taken" is the one thing
    // a static picture of a graph has to convey.
    expect(container.querySelectorAll('path[stroke-dasharray]').length).toBe(1);
  });

  it('shows each run with the branch it took', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Billing graph')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^runs$/i }));

    expect(screen.getByText('triage → pricing → answer')).toBeInTheDocument();
    expect(screen.getByText('150')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  it('says which runs were imported rather than watched', async () => {
    // An OTLP run is written once its trace has finished, so it never appears
    // live. Unmarked, that reads as a connection that is not working.
    getConnection.mockImplementation(() => ok({
      connection,
      runs: [
        { ...runs[0], run_id: 'ing-otel', metadata: { otel: { imported: true, root_reported: true } } },
        { ...runs[1], run_id: 'ing-partial', metadata: { otel: { imported: true, root_reported: false } } },
        runs[0],
      ],
      total: 3,
    }));
    show();
    await waitFor(() => expect(screen.getByText('Billing graph')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^runs$/i }));

    expect(screen.getByText('imported')).toBeInTheDocument();
    expect(screen.getByText('imported').title).toMatch(/no live view/i);
    // A trace whose root was lost is flagged harder: that record is incomplete.
    expect(screen.getByText('partial').title).toMatch(/root span never arrived/i);
    // The reported run beside them carries no mark at all.
    expect(screen.getAllByText('imported')).toHaveLength(1);
  });

  it('cannot show the stored token, and says so instead of pretending', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Billing graph')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^setup$/i }));

    expect(screen.getByText(/ends in bcF4/i)).toBeInTheDocument();
    // The snippet still shows the placeholder, which is the point: what must
    // never appear is a real token, and the hub does not have one to show.
    expect(document.body.textContent).toContain('ahc_…');
    expect(screen.queryByText(/ahc_[A-Za-z0-9_-]{8,}/)).toBeNull();
  });

  it('shows a rotated token once, and only after rotating', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Billing graph')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^setup$/i }));
    fireEvent.click(screen.getByRole('button', { name: /rotate token/i }));

    await waitFor(() => expect(screen.getByText('ahc_brand-new')).toBeInTheDocument());
    // The workspace goes with it: a connection belonging to another one answers
    // 404, which is what stops this page acting on somebody else's.
    expect(rotateConnectionToken).toHaveBeenCalledWith('billing-graph', 'team-a');
  });

  it('disabling is offered as the reversible half of revoking', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Billing graph')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^setup$/i }));
    fireEvent.click(screen.getByRole('button', { name: /^disable$/i }));

    await waitFor(() => expect(updateConnection).toHaveBeenCalledWith(
      'billing-graph', { disabled: true }, 'team-a'));
  });

  it('shows what the connection keeps, and where that number comes from', async () => {
    // A retention limit nobody can see is one people discover by missing data.
    show();
    await waitFor(() => expect(screen.getByText('Billing graph')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^setup$/i }));

    expect(screen.getByText(/hub default \(2000\)/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue('2000')).toBeInTheDocument();
  });

  it('trims on demand and says what went', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Billing graph')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^setup$/i }));
    fireEvent.click(screen.getByRole('button', { name: /trim now/i }));

    await waitFor(() => expect(screen.getByText(/removed 7/i)).toBeInTheDocument());
    expect(pruneConnection).toHaveBeenCalledWith('billing-graph', 'team-a');
  });

  it('sets a limit for this connection alone', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Billing graph')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^setup$/i }));
    fireEvent.change(screen.getByDisplayValue('2000'), { target: { value: '500' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(updateConnection).toHaveBeenCalledWith(
      'billing-graph', { retention_runs: 500 }, 'team-a'));
  });

  it('asks for the connection as the workspace it is being viewed from', async () => {
    show();

    await waitFor(() => expect(getConnection).toHaveBeenCalledWith('billing-graph', 'team-a'));
  });

  it('explains a missing graph as something the client has not sent', async () => {
    getConnection.mockImplementation(() => ok({
      connection: { ...connection, topology: {} }, runs: [], total: 0,
    }));
    show();

    await waitFor(() => expect(screen.getByText(/has not reported its shape/i)).toBeInTheDocument());
  });
});
