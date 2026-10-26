import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useEffect } from 'react';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n';

const ok = (data) => Promise.resolve({ data });
const chatRecord = {
  run_group: 'flow-chat-f1-7', kind: 'chat', title: 'write the report', started_at: '2026-10-22T10:00:00Z',
  status: 'completed', conversation_id: 'flow-chat-f1-7',
  events: [
    { type: 'flow_start', timestamp: '2026-10-22T10:00:00Z', run_group: 'flow-chat-f1-7' },
    { type: 'agent_start', node_id: 'n1', input: 'Latest user message: write the report', timestamp: '2026-10-22T10:00:01Z', run_group: 'flow-chat-f1-7' },
    { type: 'agent_finish', node_id: 'n1', agent_name: 'Writer', output: 'Done.', timestamp: '2026-10-22T10:00:05Z', run_group: 'flow-chat-f1-7' },
    { type: 'flow_finish', timestamp: '2026-10-22T10:00:06Z', run_group: 'flow-chat-f1-7' },
  ],
};

// A task run whose log has no last line (a loop pass written before the loop
// logged its end): picked, it must stay picked.
const openTaskRun = {
  run_group: 'fr-open', kind: 'task', title: 'Loop — iteration 1', started_at: '2026-10-22T09:00:00Z',
  status: 'running',
  events: [
    { type: 'flow_start', timestamp: '2026-10-22T09:00:00Z', run_group: 'fr-open', kind: 'task' },
    { type: 'agent_start', node_id: 'n1', agent_name: 'Writer', timestamp: '2026-10-22T09:00:01Z', run_group: 'fr-open', kind: 'task' },
    { type: 'agent_finish', node_id: 'n1', agent_name: 'Writer', output: 'pass output', timestamp: '2026-10-22T09:00:05Z', run_group: 'fr-open', kind: 'task' },
  ],
};

vi.mock('../../api', async (importOriginal) => ({
  ...(await importOriginal()),
  getFlow: vi.fn(() => ok({ id: 'f1', name: 'F', workspace: 'ws', nodes: [], edges: [] })),
  getAgents: vi.fn(() => ok([])),
  getTasks: vi.fn(() => ok([])),
  getFlowLogs: vi.fn(() => ok([...openTaskRun.events, ...chatRecord.events])),
  getFlowRuns: vi.fn(() => ok([chatRecord, openTaskRun])),
  getFlowInstances: vi.fn(() => ok([])),
  resumeFlowRun: vi.fn(() => ok({})),
  getRunTrail: vi.fn(() => ok(null)),
}));
vi.mock('../../components/workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: 'ws', liveUpdates: false }) }));
// Stable, as the app's stream context is: a new `on` per render would re-run
// the page's load effect forever.
const stream = { on: () => () => {}, acquireChannel: () => () => {}, clientId: 'c' };
vi.mock('../../components/stream', () => ({ useStream: () => stream }));
const toast = { error: vi.fn(), success: vi.fn() };
vi.mock('../../components/toast', () => ({ useToast: () => toast, errorDetail: () => '' }));
vi.mock('../../components/flow/FlowCanvas', () => ({ default: () => <div data-testid="canvas" /> }));
// The chat as the page hands it over: which conversation, with what turns.
vi.mock('../../components/flow/FlowChat', () => ({
  default: ({ resumeKey, resumeMessages, onStreamEvent }) => (
    <div data-testid="flow-chat" data-key={resumeKey || ''}>
      {(resumeMessages || []).map((m) => <p key={m.id}>{m.content}</p>)}
      <button type="button" onClick={() => onStreamEvent({ type: 'flow_meta', nodes: [], user_message: 'hi' })}>send</button>
    </div>
  ),
}));

import FlowRun from '../FlowRun';

const seen = {};
function Where() {
  const here = useLocation();
  const navigate = useNavigate();
  useEffect(() => { seen.location = here; seen.go = navigate; });
  return null;
}

const show = (path) => render(
  <MemoryRouter initialEntries={[path]}>
    <I18nProvider>
      <Routes>
        <Route path="/flows/:flowId" element={<FlowRun />} />
        <Route path="/flows/:flowId/runs/:runGroup" element={<FlowRun />} />
      </Routes>
      <Where />
    </I18nProvider>
  </MemoryRouter>,
);

beforeEach(() => {
  try { localStorage.clear(); } catch { /* none */ }
  Element.prototype.scrollIntoView = () => {};
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
});

describe('FlowRun: a conversation has its own address', () => {
  it('opens the conversation the address names, its turns back in the chat', async () => {
    show('/flows/f1/runs/flow-chat-f1-7');
    await waitFor(() => expect(screen.getByTestId('flow-chat')).toHaveAttribute('data-key', 'flow-chat-f1-7'));
    expect(screen.getByText('write the report', { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByText('Done.', { selector: 'p' })).toBeInTheDocument();
    expect(seen.location.pathname).toBe('/flows/f1/runs/flow-chat-f1-7');
  });

  it('keeps a task run picked even when its log has no last line', async () => {
    show('/flows/f1/runs/fr-open');
    expect((await screen.findAllByText('pass output')).length).toBeGreaterThan(0);
    // Still there once the page has settled: the pick was not dropped for a
    // run that only looks live, for an empty new chat.
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getAllByText('pass output').length).toBeGreaterThan(0);
    expect(screen.queryByTestId('flow-chat')).not.toBeInTheDocument();
  });

  it('answers the question a run without a task parked on, from its History record', async () => {
    const api = await import('../../api');
    const parked = {
      run_group: 'fr-wait', kind: 'task', title: 'Digest', started_at: '2026-10-22T08:00:00Z', status: 'running',
      events: [
        { type: 'flow_start', timestamp: '2026-10-22T08:00:00Z', run_group: 'fr-wait', kind: 'task' },
        { type: 'flow_interrupt', node_id: 'ask', question: 'Publish it?', choices: ['yes', 'no'],
          timestamp: '2026-10-22T08:00:02Z', run_group: 'fr-wait', kind: 'task' },
      ],
    };
    api.getFlowRuns.mockImplementation(() => ok([chatRecord, openTaskRun, parked]));
    api.getFlowLogs.mockImplementation(() => ok([...parked.events, ...chatRecord.events]));
    api.getFlowInstances.mockImplementation(() => ok([{ flow_run_id: 'fr-wait', status: 'awaiting_input', checkpoint: { done: [] } }]));
    try {
      show('/flows/f1/runs/fr-wait');
      const box = await screen.findByTestId('flow-run-question');
      expect(box).toHaveTextContent('Publish it?');
      fireEvent.click(screen.getByRole('button', { name: 'yes' }));
      await waitFor(() => expect(api.resumeFlowRun).toHaveBeenCalledWith('fr-wait', 'yes'));
    } finally {
      api.getFlowRuns.mockImplementation(() => ok([chatRecord, openTaskRun]));
      api.getFlowLogs.mockImplementation(() => ok([...openTaskRun.events, ...chatRecord.events]));
      api.getFlowInstances.mockImplementation(() => ok([]));
    }
  });

  it('moves an older ?run= link to the address', async () => {
    show('/flows/f1?run=flow-chat-f1-7');
    await waitFor(() => expect(seen.location.pathname).toBe('/flows/f1/runs/flow-chat-f1-7'));
    expect(seen.location.search).toBe('');
    await waitFor(() => expect(screen.getByTestId('flow-chat')).toHaveAttribute('data-key', 'flow-chat-f1-7'));
  });

  it('a pick in History gives the address, a new chat takes it back', async () => {
    show('/flows/f1');
    await screen.findByTestId('flow-chat');
    expect(screen.getByTestId('flow-chat')).toHaveAttribute('data-key', '');
    fireEvent.click(await screen.findByText('write the report'));
    await waitFor(() => expect(seen.location.pathname).toBe('/flows/f1/runs/flow-chat-f1-7'));
    expect(screen.getByTestId('flow-chat')).toHaveAttribute('data-key', 'flow-chat-f1-7');
    const btn = screen.getByRole('button', { name: /new chat/i });

    await act(async () => { fireEvent.click(btn); });
    await waitFor(() => expect(seen.location.pathname).toBe('/flows/f1'));
    expect(screen.getByTestId('flow-chat')).toHaveAttribute('data-key', '');
  });

  it('a new chat takes its address once its first turn starts', async () => {
    show('/flows/f1');
    await screen.findByTestId('flow-chat');
    fireEvent.click(screen.getByRole('button', { name: 'send' }));
    await waitFor(() => expect(seen.location.pathname).toMatch(/^\/flows\/f1\/runs\/flow-chat-f1-/));
    // Still the same new chat, not a record reopened over it.
    expect(screen.getByTestId('flow-chat')).toHaveAttribute('data-key', '');
  });

  it('Back returns to what was open before a pick', async () => {
    show('/flows/f1');
    fireEvent.click(await screen.findByText('write the report'));
    await waitFor(() => expect(screen.getByTestId('flow-chat')).toHaveAttribute('data-key', 'flow-chat-f1-7'));
    await act(async () => { seen.go(-1); });
    await waitFor(() => expect(seen.location.pathname).toBe('/flows/f1'));
    await waitFor(() => expect(screen.getByTestId('flow-chat')).toHaveAttribute('data-key', ''));
    await act(async () => { seen.go(1); });
    await waitFor(() => expect(screen.getByTestId('flow-chat')).toHaveAttribute('data-key', 'flow-chat-f1-7'));
  });
});
