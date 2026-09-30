import { beforeAll, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

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

  it('opens a delegated run with its own steps, even from a truncated log line', async () => {
    show({});
    fireEvent.click(screen.getByText('Delegated to visualizer'));
    await waitFor(() => expect(getMessageInsights).toHaveBeenCalledWith('child'));
    expect(await screen.findByText('Tool: mesh_new')).toBeInTheDocument();
  });

  it('draws the delegated run without a timeline dot of its own', async () => {
    const { container } = show({});
    fireEvent.click(screen.getByText('Delegated to visualizer'));
    await screen.findByText('Tool: mesh_new');
    // One dot: the parent run's. The delegated run hangs off its step.
    expect(container.querySelectorAll('.rounded-full.bg-indigo-500')).toHaveLength(1);
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
    expect(screen.getByText('Tool: mesh_new')).toBeInTheDocument();
    const d = live.tools[0].delegation;
    const next = { ...live, tools: [{ ...live.tools[0], delegation: { ...d, tools: [...d.tools, { step: 2, tool: 'set_color', input: 'red', running: true }] } }] };
    rerender(<I18nProvider><ProcessGraph messageRuns={[next]} /></I18nProvider>);
    expect(screen.getByText('Tool: set_color')).toBeInTheDocument();
    expect(getMessageInsights).not.toHaveBeenCalled();
  });
});
