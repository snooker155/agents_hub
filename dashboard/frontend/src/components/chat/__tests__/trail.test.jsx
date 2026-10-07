import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../api', () => ({
  getView: vi.fn(() => new Promise(() => {})),
  applyViewOps: vi.fn(),
  saveViewSnapshot: vi.fn(),
}));

import { I18nProvider } from '../../../i18n';
import { MessageBubble } from '../MessageBubble';
import { BuildMessage } from '../BuildMessage';
import { compactTimeline, currentActivity, foldDelegationTools, withFinalText } from '../trail';
import { handleAgentEvent } from '../send/handleAgentResponse';

/**
 * A turn's steps: the Chat view lets them pass while the turn is live, a
 * delegated run included, and keeps only the answer; the Build view keeps every
 * step, the text written between tool calls included, and both preview the
 * views a turn made.
 */

const wrap = (node) => render(
  <MemoryRouter>
    <I18nProvider>{node}</I18nProvider>
  </MemoryRouter>,
);

const DELEGATION = {
  type: 'delegation', run_id: 'child', agent_id: 'visualizer', agent_name: 'Visualizer',
  input: 'build a red cube', running: false, ok: true, output: 'Built view v1.',
  timeline: [
    { type: 'reasoning', kind: 'think', content: 'A cube first.' },
    { type: 'tool', tool: 'create_view', input: '{}', output: '{"ok":true,"view_id":"v1"}' },
    { type: 'text', text: 'Scene created, adding the cube.' },
    { type: 'tool', tool: 'mesh_new', input: 'cube', output: 'ok' },
  ],
};

const TURN = {
  id: 'a1', role: 'agent', run_id: 'r1', content: 'Here is your cube.',
  inbound_tokens: 120, outbound_tokens: 40, duration_ms: 2000,
  timeline: [
    { type: 'reasoning', kind: 'think', content: 'The visualizer should build it.' },
    { type: 'text', text: 'I will ask the visualizer.' },
    { type: 'tool', tool: 'run_agent_tool', input: "{'agent_id': 'visualizer'}", output: '{"ok":true}' },
    DELEGATION,
    { type: 'text', text: 'Here is your cube' },
  ],
  entities: [{ kind: 'view', id: 'v1', title: 'Red cube', url: '/views/v1', action: 'created' }],
};

describe('trail helpers', () => {
  it('folds a delegation tool call into the delegated run it opened', () => {
    const folded = foldDelegationTools(TURN.timeline);
    expect(folded.map((e) => e.type)).toEqual(['reasoning', 'text', 'delegation', 'text']);
  });

  it('keeps a delegation call that never opened a run (refused)', () => {
    const refused = [{ type: 'tool', tool: 'run_agent_tool', output: '{"ok":false}' }, { type: 'text', text: 'Could not.' }];
    expect(foldDelegationTools(refused)).toHaveLength(2);
  });

  it('ends the trail with the reply the turn settled on', () => {
    expect(withFinalText(TURN.timeline, 'Final.').at(-1)).toEqual({ type: 'text', text: 'Final.' });
    const endsOnTool = [{ type: 'tool', tool: 'x' }];
    expect(withFinalText(endsOnTool, 'Final.')).toEqual([...endsOnTool, { type: 'text', text: 'Final.' }]);
  });

  it('clips long payloads for storage and drops the running flags', () => {
    const stored = compactTimeline([
      { type: 'tool', tool: 'read_file', output: 'x'.repeat(10000), running: true },
      { ...DELEGATION, running: true, timeline: [{ type: 'tool', tool: 't', input: 'y'.repeat(10000), running: true }] },
    ]);
    expect(stored[0].output.length).toBeLessThan(2100);
    expect(stored[0]).not.toHaveProperty('running');
    expect(stored[1]).not.toHaveProperty('running');
    expect(stored[1].timeline[0].input.length).toBeLessThan(2100);
  });

  it('names what a live turn is doing now', () => {
    expect(currentActivity({ thinking_live: 'hmm' })).toEqual({ kind: 'thinking', text: 'hmm' });
    expect(currentActivity({ timeline: [{ type: 'tool', tool: 'read_file', running: true }] }))
      .toEqual({ kind: 'tool', tool: 'read_file' });
    expect(currentActivity({ timeline: [{ type: 'text', text: 'Looking' }] })).toEqual({ kind: 'text', text: 'Looking' });
  });
});

describe('MessageBubble (Chat view)', () => {
  it('shows the answer only: no steps, no delegated run, no run stats', () => {
    wrap(<MessageBubble msg={TURN} />);
    expect(screen.getByText('Here is your cube.')).toBeInTheDocument();
    expect(screen.queryByTestId('delegation-card')).toBeNull();
    expect(screen.queryByTestId('live-delegation')).toBeNull();
    expect(screen.queryByTestId('live-delegation-done')).toBeNull();
    expect(screen.queryByText('Built view v1.')).toBeNull();
    expect(screen.queryByText('I will ask the visualizer.')).toBeNull();
    expect(screen.queryByText(/The visualizer should build it/)).toBeNull();
    expect(screen.queryByText(/^in:/)).toBeNull();
    expect(screen.queryByText('To eval case')).toBeNull();
  });

  it('previews the views the turn built, with a link to each', () => {
    wrap(<MessageBubble msg={TURN} />);
    expect(screen.getByTestId('message-views')).toBeInTheDocument();
    expect(screen.getAllByRole('link').some((a) => a.getAttribute('href') === '/views/v1')).toBe(true);
  });

  it('while live, shows only the step being written', () => {
    const live = { ...TURN, content: 'I will ask the visualizer.', timeline: [{ type: 'text', text: 'I will ask the visualizer.' }, { type: 'tool', tool: 'read_file', running: true }] };
    wrap(<MessageBubble msg={live} isStreaming />);
    expect(screen.queryByText('I will ask the visualizer.')).toBeNull();
    expect(screen.getByText('Running read_file')).toBeInTheDocument();
  });

  it('while a worker runs, shows only its latest step', () => {
    const running = {
      ...DELEGATION, running: true, ok: null, output: '',
      timeline: [
        { type: 'tool', tool: 'create_view', input: '{}', output: 'ok' },
        { type: 'tool', tool: 'mesh_new', input: 'cube', running: true },
      ],
    };
    const live = { ...TURN, content: '', timeline: [TURN.timeline[2], running] };
    wrap(<MessageBubble msg={live} isStreaming />);
    expect(screen.getByTestId('live-delegation')).toBeInTheDocument();
    expect(screen.getByText('Delegated to Visualizer')).toBeInTheDocument();
    expect(screen.getAllByTestId('live-delegation-tool')).toHaveLength(1);
    expect(screen.getByText('mesh_new')).toBeInTheDocument();
    expect(screen.queryByText('create_view')).toBeNull();
  });

  it('says the worker finished, with its answer, until the turn moves on', () => {
    const live = { ...TURN, content: '', timeline: [TURN.timeline[2], DELEGATION] };
    const { unmount } = wrap(<MessageBubble msg={live} isStreaming />);
    expect(screen.getByTestId('live-delegation-done')).toBeInTheDocument();
    expect(screen.getByText('Visualizer finished')).toBeInTheDocument();
    expect(screen.getByText('Built view v1.')).toBeInTheDocument();
    unmount();
    const next = { ...live, timeline: [...live.timeline, { type: 'tool', tool: 'read_file', running: true }] };
    wrap(<MessageBubble msg={next} isStreaming />);
    expect(screen.queryByTestId('live-delegation-done')).toBeNull();
    expect(screen.getByText('Running read_file')).toBeInTheDocument();
  });
});

describe('BuildMessage (Build view)', () => {
  it('keeps every step, the text between tool calls and the worker steps included', () => {
    wrap(<BuildMessage msg={TURN} />);
    expect(screen.getByText('I will ask the visualizer.')).toBeInTheDocument();
    expect(screen.getByText('Scene created, adding the cube.')).toBeInTheDocument();
    expect(screen.getByText('Here is your cube.')).toBeInTheDocument();
    // The run_agent_tool call is the delegation card, not a card of its own.
    expect(screen.queryByText('run_agent_tool')).toBeNull();
  });
});

describe('delegated run events', () => {
  it('puts the text a worker writes between steps into its card', () => {
    let convs = [{ id: 'c1', messages: [{ id: 'a1', role: 'agent', run_id: 'r1', timeline: [] }] }];
    const setConversations = (fn) => { convs = fn(convs); };
    const ctx = { convId: 'c1', setConversations, assistantId: 'a1', isFlowMode: false, state: {} };
    handleAgentEvent({ type: 'delegation_start', run_id: 'child', parent_run_id: null, agent_id: 'visualizer', input: 'cube' }, ctx);
    handleAgentEvent({ type: 'text', delegation: true, run_id: 'child', content: 'Scene created.' }, ctx);
    handleAgentEvent({ type: 'delegation_end', run_id: 'child', ok: true, output: 'Done: v1' }, ctx);
    const [card] = convs[0].messages[0].timeline;
    expect(card.timeline).toEqual([{ type: 'text', text: 'Scene created.' }]);
    expect(card.output).toBe('Done: v1');
    expect(card.running).toBe(false);
  });

  it('grows the worker in the Process panel step by step', () => {
    let convs = [{ id: 'c1', messages: [{ id: 'a1', role: 'agent', run_id: 'r1', timeline: [] }] }];
    let insights = { message_runs: [{ run_id: 'r1', tools: [{ tool: 'run_agent_tool', input: 'x', output: null, running: true }] }] };
    const ctx = {
      convId: 'c1', assistantId: 'a1', isFlowMode: false, state: {},
      setConversations: (fn) => { convs = fn(convs); },
      setProcessInsights: (fn) => { insights = fn(insights); },
    };
    handleAgentEvent({ type: 'delegation_start', run_id: 'child', parent_run_id: 'r1', agent_id: 'visualizer', agent_name: 'Visualizer', input: 'cube' }, ctx);
    handleAgentEvent({ type: 'tool_start', delegation: true, run_id: 'child', step: 1, tool: 'mesh_new', input: 'cube' }, ctx);
    let live = insights.message_runs[0].tools[0].delegation;
    expect(live.running).toBe(true);
    expect(live.tools).toEqual([{ step: 1, tool: 'mesh_new', input: 'cube', output: null, running: true }]);
    handleAgentEvent({ type: 'tool_end', delegation: true, run_id: 'child', output: 'ok' }, ctx);
    handleAgentEvent({ type: 'delegation_end', run_id: 'child', ok: true, output: 'Done: v1' }, ctx);
    live = insights.message_runs[0].tools[0].delegation;
    expect(live.tools[0]).toMatchObject({ output: 'ok', running: false });
    expect(live).toMatchObject({ running: false, ok: true, output: 'Done: v1' });
  });
});

describe('tool call outcome', () => {
  const turn = () => {
    let convs = [{ id: 'c1', messages: [{ id: 'a1', role: 'agent', run_id: 'r1', timeline: [] }] }];
    let insights = { message_runs: [{ run_id: 'r1', tools: [] }] };
    const ctx = {
      convId: 'c1', assistantId: 'a1', isFlowMode: false, state: {},
      setConversations: (fn) => { convs = fn(convs); },
      setProcessInsights: (fn) => { insights = fn(insights); },
    };
    return { ctx, timeline: () => convs[0].messages[0].timeline, tools: () => insights.message_runs[0].tools };
  };

  it('closes a call that raised as an error instead of leaving it running', () => {
    const { ctx, timeline, tools } = turn();
    handleAgentEvent({ type: 'tool_start', step: 1, tool: 'read_file', input: 'a' }, ctx);
    handleAgentEvent({ type: 'tool_error', tool: 'read_file', error: 'no such file', status: 'error' }, ctx);
    expect(timeline()[0]).toMatchObject({ running: false, error: true, status: 'error', output: 'ERROR: no such file' });
    expect(tools()[0]).toMatchObject({ running: false, status: 'error' });
  });

  it('keeps the status a finished call came back with', () => {
    const { ctx, timeline } = turn();
    handleAgentEvent({ type: 'tool_start', step: 1, tool: 'read_file', input: 'a' }, ctx);
    handleAgentEvent({ type: 'tool_end', step: 1, tool: 'read_file', output: '{"ok": false}', status: 'error' }, ctx);
    expect(timeline()[0]).toMatchObject({ running: false, status: 'error' });
  });
});
