import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useEffect } from 'react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n';

const ok = (data) => Promise.resolve({ data });

const loop = {
  loop_id: 'lp1', name: 'Polish the post', description: 'the launch post', flow_id: 'f1',
  flow_name: 'Writer flow', flow_exists: true, max_iterations: 3, min_iterations: 1,
  target_score: 80, patience: 2, max_wall_seconds: 3600, evaluator_mode: 'final_agent',
  exit_criterion: 'reads well', rubric: '', resolved_evaluator: { mode: 'final_agent', agent_id: 'editor' },
};
const runs = [
  { loop_run_id: 'r2', loop_id: 'lp1', status: 'completed', goal: 'second goal', iterations_done: 2,
    final_score: 85, best_score: 85, started_at: '2026-10-23T10:00:00Z', stop_reason: 'target_score', result: 'draft two' },
  { loop_run_id: 'r1', loop_id: 'lp1', status: 'failed', goal: 'first goal', iterations_done: 1,
    final_score: 40, best_score: 40, started_at: '2026-10-22T10:00:00Z', position: { iterations_done: 1 } },
];
const iterationsOf = {
  r2: [
    { iteration: 1, flow_run_id: 'fr1', status: 'completed', score: 60, verdict: 'continue',
      reason: 'The intro is too long.', feedback: 'Cut the intro to two lines.', output: 'draft one',
      duration_ms: 1200, cost: 0.01, evaluator_agent: 'editor' },
    { iteration: 2, flow_run_id: 'fr2', status: 'completed', score: 85, verdict: 'stop',
      reason: 'Tight and clear now.', feedback: '', output: 'draft two', duration_ms: 900, cost: 0.01 },
  ],
  r1: [
    { iteration: 1, flow_run_id: 'fr0', status: 'completed', score: 40, verdict: 'continue',
      reason: 'Off topic.', feedback: 'Stay on the launch.', output: 'x', duration_ms: 500, cost: 0 },
  ],
};

vi.mock('../../api', async (importOriginal) => ({
  ...(await importOriginal()),
  getLoop: vi.fn(() => ok(loop)),
  startLoop: vi.fn(),
  estimateLoop: vi.fn(() => ok({ llm_calls_upper_bound: 9, agent_nodes: 2, max_iterations: 3, note_key: 'estimateNote', note: '' })),
  getLoopRuns: vi.fn(() => ok({ runs })),
  getLoopRun: vi.fn((id) => ok({ ...runs.find((r) => r.loop_run_id === id), iterations: iterationsOf[id] })),
  getLoopRunLog: vi.fn((id) => ok({
    iterations: iterationsOf[id].map((it) => ({
      iteration: it.iteration,
      events: [
        { type: 'flow_start', timestamp: '2026-10-23T10:00:00Z', run_group: it.flow_run_id },
        { type: 'agent_start', node_id: 'n1', agent_name: 'Writer', timestamp: '2026-10-23T10:00:01Z', run_group: it.flow_run_id },
        { type: 'agent_finish', node_id: 'n1', agent_name: 'Writer', output: `wrote ${it.output}`, timestamp: '2026-10-23T10:00:05Z', run_group: it.flow_run_id },
      ],
    })),
  })),
  getFlow: vi.fn(() => ok({ id: 'f1', nodes: [{ id: 'n1', data: { label: 'Writer', agent_id: 'writer' } }], edges: [] })),
  listFlows: vi.fn(() => ok({ flows: [{ id: 'f1', name: 'Writer flow' }] })),
  getAgents: vi.fn(() => ok([])),
  getRunTrail: vi.fn(() => ok(null)),
}));
vi.mock('../../components/workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: 'ws' }) }));
const stream = { on: () => () => {}, onRefetch: () => () => {} };
vi.mock('../../components/stream', () => ({
  useStream: () => stream, useChannel: () => {}, useLiveRefetch: () => {},
}));
const toast = { error: vi.fn(), success: vi.fn() };
vi.mock('../../components/toast', () => ({ useToast: () => toast, errorDetail: () => '' }));
vi.mock('../../components/flow/FlowCanvas', () => ({
  default: ({ nodes }) => <div data-testid="canvas">{nodes.map((n) => n.data.label).join(',')}</div>,
}));
vi.mock('../../components/loops/LoopCreatorChat', () => ({
  default: ({ loopId, onClose }) => (
    <div data-testid="loop-creator-chat" data-loop={loopId}>
      <button type="button" onClick={onClose}>close chat</button>
    </div>
  ),
}));

import LoopDetail from '../LoopDetail';
import Loops from '../Loops';

const seen = {};
function Where() {
  const here = useLocation();
  useEffect(() => { seen.location = here; });
  return null;
}

const show = (path) => render(
  <MemoryRouter initialEntries={[path]}>
    <I18nProvider>
      <Routes>
        <Route path="/loops" element={<Loops />} />
        <Route path="/loops/:loopId" element={<LoopDetail />} />
        <Route path="/loops/:loopId/runs/:runId" element={<LoopDetail />} />
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

describe('LoopDetail: a loop on a page of its own', () => {
  it('draws the flow and shows the run as a chat: the goal, then each pass with its verdict', async () => {
    show('/loops/lp1');
    const canvas = await screen.findByTestId('canvas');
    await waitFor(() => expect(canvas).toHaveTextContent('Writer'));
    const chat = await screen.findByTestId('loop-run-chat');
    // What the run was asked for is the person's message.
    expect(within(chat).getByText('second goal')).toBeInTheDocument();
    const first = await within(chat).findByTestId('loop-evaluation-1');
    expect(first).toHaveTextContent('60/100');
    expect(first).toHaveTextContent('draft one');
    expect(first).toHaveTextContent('The intro is too long.');
    expect(first).toHaveTextContent('Cut the intro to two lines.');
    expect(within(chat).getByTestId('loop-evaluation-2')).toHaveTextContent('Tight and clear now.');
    // The outcome closes the run, whole.
    expect(within(chat).getByTestId('loop-run-outcome')).toHaveTextContent('draft two');
  });

  it('opens a pass\'s whole output in a modal, not in place', async () => {
    show('/loops/lp1');
    fireEvent.click(await screen.findByTestId('loop-output-open-1'));
    const modal = screen.getByTestId('loop-output-modal');
    expect(modal).toHaveTextContent('draft one');
    fireEvent.click(within(modal).getByRole('button', { name: 'Close' }));
    expect(screen.queryByTestId('loop-output-modal')).not.toBeInTheDocument();
  });

  it('keeps the scores and each pass\'s work log in the right column', async () => {
    show('/loops/lp1');
    const column = await screen.findByTestId('loop-pass-logs');
    expect(await within(column).findByTestId('loop-trajectory')).toHaveTextContent('85');
    // The last pass opens with its log, an earlier one on a click.
    const last = await within(column).findByTestId('loop-iteration-2');
    await waitFor(() => expect(last).toHaveTextContent('wrote draft two'));
    const first = within(column).getByTestId('loop-iteration-1');
    expect(first).not.toHaveTextContent('wrote draft one');
    fireEvent.click(within(first).getByText('Iteration 1'));
    expect(first).toHaveTextContent('wrote draft one');
  });

  it('starts a run from the box at the foot of the run chat, with no task of its own', async () => {
    const api = await import('../../api');
    api.startLoop.mockResolvedValue({ data: { loop_run_id: 'r3', status: 'pending' } });
    show('/loops/lp1');
    await screen.findByTestId('loop-evaluation-2');
    fireEvent.change(screen.getByPlaceholderText(/request the flow works on/i), { target: { value: 'third goal' } });
    fireEvent.click(screen.getByTestId('loop-run-button'));
    await waitFor(() => expect(api.startLoop).toHaveBeenCalledWith('lp1', {
      goal: 'third goal', workspace: 'ws', attachments: [], references: [],
    }));
    await waitFor(() => expect(seen.location.pathname).toBe('/loops/lp1/runs/r3'));
  });

  it('lists the runs on the left and opens another one at its own address', async () => {
    show('/loops/lp1');
    const history = await screen.findByTestId('loop-run-history');
    expect(within(history).getByText('second goal')).toBeInTheDocument();
    fireEvent.click(within(history).getByText('first goal'));
    await waitFor(() => expect(seen.location.pathname).toBe('/loops/lp1/runs/r1'));
    await waitFor(() => expect(screen.getByTestId('loop-evaluation-1')).toHaveTextContent('Off topic.'));
    expect(screen.queryByTestId('loop-evaluation-2')).not.toBeInTheDocument();
    // A failed run with a finished pass can be picked up again.
    expect(within(history).getByText('Resume')).toBeInTheDocument();
  });

  it('empties the chat for a new run from the button over the history', async () => {
    show('/loops/lp1/runs/r2');
    await screen.findByTestId('loop-evaluation-2');
    fireEvent.click(screen.getByTestId('loop-new-run'));
    await waitFor(() => expect(seen.location.pathname).toBe('/loops/lp1'));
    expect(screen.queryByTestId('loop-evaluation-1')).not.toBeInTheDocument();
    expect(within(screen.getByTestId('loop-run-chat')).getByText(/press Run/)).toBeInTheDocument();
  });

  it('opens the run chat at its end when the Run tab comes back', async () => {
    const height = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight');
    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', { configurable: true, get: () => 900 });
    try {
      show('/loops/lp1');
      await screen.findByTestId('loop-evaluation-2');
      fireEvent.click(screen.getByRole('button', { name: /setup/i }));
      const scroller = screen.getByTestId('loop-run-chat').querySelector('.overflow-y-auto');
      scroller.scrollTop = 0;
      fireEvent.click(screen.getAllByRole('button', { name: /^run$/i })[0]); // the tab, before the box's round button
      expect(scroller.scrollTop).toBe(900);
    } finally {
      if (height) Object.defineProperty(HTMLElement.prototype, 'scrollHeight', height);
      else delete HTMLElement.prototype.scrollHeight;
    }
  });

  it('has no estimate button in the box', async () => {
    show('/loops/lp1');
    await screen.findByTestId('loop-evaluation-2');
    expect(screen.queryByText('Estimate')).not.toBeInTheDocument();
  });

  it('folds the flow graph away and back from the header', async () => {
    show('/loops/lp1');
    await screen.findByTestId('loop-flow-graph');
    fireEvent.click(screen.getByTestId('loop-graph-toggle'));
    expect(screen.queryByTestId('loop-flow-graph')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('loop-graph-toggle'));
    expect(screen.getByTestId('loop-flow-graph')).toBeInTheDocument();
  });

  it('has an Artifacts tab, as the flow page does', async () => {
    show('/loops/lp1');
    await screen.findByTestId('loop-evaluation-2');
    fireEvent.click(screen.getByRole('button', { name: /artifacts/i }));
    expect(await screen.findByTestId('flow-artifacts')).toBeInTheDocument();
    expect(screen.queryByTestId('loop-flow-graph')).not.toBeInTheDocument();
  });

  it('opens the Loop Creator beside the page, not as a tab', async () => {
    show('/loops/lp1');
    await screen.findByTestId('loop-evaluation-2');
    expect(screen.queryByRole('button', { name: /^chat$/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('loop-creator-toggle'));
    expect(screen.getByTestId('loop-creator-chat')).toHaveAttribute('data-loop', 'lp1');
    fireEvent.click(screen.getByText('close chat'));
    expect(screen.queryByTestId('loop-creator-chat')).not.toBeInTheDocument();
  });

  it('sends an older ?loop=&run= link on to the run\'s own address', async () => {
    show('/loops?loop=lp1&run=r1');
    await waitFor(() => expect(seen.location.pathname).toBe('/loops/lp1/runs/r1'));
  });
});
