import { beforeAll, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('../../api', () => ({
  getMessageInsights: vi.fn(() => Promise.resolve({ data: { message_runs: [{
    run_id: 'child', agent_id: 'visualizer', input: 'cube', output: 'Built v1',
    tools: [{ step: 1, tool: 'mesh_new', input: 'cube', output: 'ok' }],
  }] } })),
  getEvalSetsForRun: vi.fn(() => new Promise(() => {})),
  createEvalSet: vi.fn(),
  addEvalCase: vi.fn(),
}));

import { getMessageInsights } from '../../api';
import { I18nProvider } from '../../i18n';
import ProcessGraph from '../ProcessGraph';

const RUN = {
  run_id: 'r1', agent_id: 'main-agent', input: 'make a cube', output: 'Here it is',
  status: 'completed',
  tools: [{
    step: 1, tool: 'run_agent_tool', input: "{'agent_id': 'visualizer', 'input': 'cube'}",
    output: '{\n  "ok": true,\n  "run_id": "child",\n  "agent_id": "visualizer",\n  "output": "Built v1", "agent": {"name": "Visual',
  }],
};

const show = (props) => render(<I18nProvider><ProcessGraph messageRuns={[RUN]} {...props} /></I18nProvider>);

describe('ProcessGraph', () => {
  // jsdom has no layout, so no scrolling.
  beforeAll(() => { Element.prototype.scrollIntoView = vi.fn(); });

  it('offers "to eval case" beside the details link when the host gives a workspace', () => {
    show({ evalCaseWorkspace: 'ws' });
    expect(screen.getByText('To eval case')).toBeInTheDocument();
    expect(screen.getByText('View Full Details').closest('a')).toHaveAttribute('href', '/messages/r1');
  });

  it('leaves "to eval case" out elsewhere', () => {
    show({});
    expect(screen.queryByText('To eval case')).toBeNull();
  });

  it('opens a delegated run with its own steps in a modal, even from a truncated log line', async () => {
    show({});
    // The card shows the result; its steps wait for the modal.
    expect(screen.getByTestId('delegated-run')).toHaveTextContent('Built v1');
    expect(screen.queryByText('Tool: mesh_new')).toBeNull();
    fireEvent.click(screen.getByText('Delegated to visualizer'));
    await waitFor(() => expect(getMessageInsights).toHaveBeenCalledWith('child'));
    const modal = screen.getByTestId('process-step-modal');
    expect(await within(modal).findByText('Tool: mesh_new')).toBeInTheDocument();
    expect(within(modal).getByTestId('delegated-run-modal-link')).toHaveAttribute('href', '/messages/child');
  });

  it('shows the call itself when the delegated run has no steps stored', async () => {
    getMessageInsights.mockResolvedValueOnce({ data: { message_runs: [] } });
    show({});
    fireEvent.click(screen.getByText('Delegated to visualizer'));
    const modal = screen.getByTestId('process-step-modal');
    expect(await within(modal).findByTestId('tool-call-status')).toBeInTheDocument();
    expect(within(modal).getByText(/Built v1/)).toBeInTheDocument();
  });

  it('closes only the top modal on Escape', async () => {
    show({});
    fireEvent.click(screen.getByText('Delegated to visualizer'));
    const modal = screen.getByTestId('process-step-modal');
    fireEvent.click(await within(modal).findByText('Tool: mesh_new'));
    expect(screen.getAllByTestId('process-step-modal')).toHaveLength(2);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getAllByTestId('process-step-modal')).toHaveLength(1);
  });

  it('grows a live delegated run step by step, without fetching it', () => {
    getMessageInsights.mockClear();
    const live = {
      ...RUN, status: 'running', output: '',
      tools: [{
        step: 1, tool: 'run_agent_tool', input: 'cube', output: null, running: true,
        delegation: {
          run_id: 'child2', agent_id: 'visualizer', agent_name: 'Visualizer', input: 'cube', running: true,
          tools: [{ step: 1, tool: 'mesh_new', input: 'cube', output: null, running: true }], reasoning: [],
        },
      }],
    };
    const { rerender } = render(<I18nProvider><ProcessGraph messageRuns={[live]} /></I18nProvider>);
    // The card names the step the worker is on; the modal draws them all.
    expect(screen.getByTestId('delegated-run')).toHaveTextContent('mesh_new');
    fireEvent.click(screen.getByText('Delegated to Visualizer'));
    expect(screen.getByText('Tool: mesh_new')).toBeInTheDocument();
    const d = live.tools[0].delegation;
    const next = { ...live, tools: [{ ...live.tools[0], delegation: { ...d, tools: [...d.tools, { step: 2, tool: 'set_color', input: 'red', running: true }] } }] };
    rerender(<I18nProvider><ProcessGraph messageRuns={[next]} /></I18nProvider>);
    expect(screen.getByText('Tool: set_color')).toBeInTheDocument();
    expect(getMessageInsights).not.toHaveBeenCalled();
  });

  it('links the delegated run to its page and names the agent that ran, not the role', () => {
    const run = { ...RUN, tools: [{ ...RUN.tools[0], input: "{'agent_id': '@coder', 'input': 'cube'}" }] };
    render(<I18nProvider><ProcessGraph messageRuns={[run]} /></I18nProvider>);
    expect(screen.getByText('Delegated to visualizer')).toBeInTheDocument();
    expect(screen.getByTestId('delegated-run-link')).toHaveAttribute('href', '/messages/child');
  });

  it('opens the stored run once a live delegation is over', async () => {
    getMessageInsights.mockClear();
    const done = {
      ...RUN,
      tools: [{
        ...RUN.tools[0],
        delegation: { run_id: 'child', agent_id: 'visualizer', input: 'cube', running: false, ok: true, tools: [] },
      }],
    };
    render(<I18nProvider><ProcessGraph messageRuns={[done]} /></I18nProvider>);
    fireEvent.click(screen.getByText('Delegated to visualizer'));
    await waitFor(() => expect(getMessageInsights).toHaveBeenCalledWith('child'));
    expect(await screen.findByText('Tool: mesh_new')).toBeInTheDocument();
  });

  it('says why a tool call failed ahead of its input', () => {
    const failed = {
      ...RUN,
      tools: [{
        step: 1, tool: 'run_shell', input: '{"command": "ls"}', status: 'error',
        output: '{"ok": false, "error": "run_shell needs the user\'s approval", "code": "approval_required"}',
      }],
    };
    render(<I18nProvider><ProcessGraph messageRuns={[failed]} /></I18nProvider>);
    expect(screen.getByTestId('tool-error')).toHaveTextContent("Error: run_shell needs the user's approval");
  });
});
