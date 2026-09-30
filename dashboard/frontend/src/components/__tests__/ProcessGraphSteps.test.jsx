import { beforeAll, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

vi.mock('../../api', () => ({
  getMessageInsights: vi.fn(),
  getEvalSetsForRun: vi.fn(() => new Promise(() => {})),
  createEvalSet: vi.fn(),
  addEvalCase: vi.fn(),
}));

import { I18nProvider } from '../../i18n';
import ProcessGraph from '../ProcessGraph';

const RUN = {
  run_id: 'r1', agent_id: 'main-agent', input: 'find the task', output: 'Found it',
  inbound_tokens: 120, outbound_tokens: 30, total_tokens: 150, duration_ms: 1200, status: 'completed',
  reasoning: [{ step: 1, content: 'I should search the tasks first' }],
  tools: [{
    step: 2, tool: 'list_tasks', input: "{'status': 'open', 'limit': 5, 'verbose': True}",
    output: '{"ok": true, "tasks": [{"key": "DEMO-1", "title": "Test task"}], "meta": "{\\"page\\": 1}"}',
  }],
};

const show = () => render(<I18nProvider><ProcessGraph messageRuns={[RUN]} /></I18nProvider>);

describe('ProcessGraph steps', () => {
  beforeAll(() => { Element.prototype.scrollIntoView = vi.fn(); });

  it('lays the run statistics out in two rows', () => {
    show();
    const tokens = screen.getByTestId('run-stats-tokens');
    const run = screen.getByTestId('run-stats-run');
    expect(tokens).toHaveTextContent(/in: 120.*out: 30.*total: 150/);
    expect(run).toHaveTextContent(/tools: 1.*duration/);
    expect(run).not.toHaveTextContent('total');
  });

  it('shows every step as a card clamped to four lines', () => {
    show();
    const previews = screen.getAllByTestId('process-node-preview');
    // input, thought, tool, output
    expect(previews).toHaveLength(4);
    for (const p of previews) expect(p.className).toContain('max-h-16');
  });

  it('opens the full step in a modal on click, and closes it with Escape', () => {
    show();
    const thought = screen.getAllByTestId('process-node').find((n) => n.textContent.includes('Thought'));
    fireEvent.click(thought);
    const modal = screen.getByTestId('process-step-modal');
    expect(within(modal).getByText('I should search the tasks first')).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByTestId('process-step-modal')).toBeNull();
  });

  it('parses a tool call into a tree, the Python-literal input and nested JSON output included', () => {
    show();
    const tool = screen.getAllByTestId('process-node').find((n) => n.textContent.includes('Tool: list_tasks'));
    fireEvent.click(tool);
    const modal = screen.getByTestId('process-step-modal');
    // Input: {'status': 'open', ..., 'verbose': True} as key/value rows.
    expect(within(modal).getByText('status:')).toBeInTheDocument();
    expect(within(modal).getByText('open')).toBeInTheDocument();
    expect(within(modal).getAllByText('true')).toHaveLength(2); // verbose: True, ok: true
    // Output: the task inside the array, and the JSON string under "meta" unfolded.
    expect(within(modal).getByText('DEMO-1')).toBeInTheDocument();
    expect(within(modal).getByText('page:')).toBeInTheDocument();
    // The raw text is one click away.
    fireEvent.click(within(modal).getAllByRole('button', { name: 'Raw' })[1]);
    expect(within(modal).getByText(/"tasks": \[/)).toBeInTheDocument();
  });

  it('closes on a click outside the dialog, not inside it', () => {
    show();
    fireEvent.click(screen.getAllByTestId('process-node')[0]);
    const modal = screen.getByTestId('process-step-modal');
    fireEvent.click(within(modal).getByRole('heading'));
    expect(screen.getByTestId('process-step-modal')).toBeInTheDocument();
    fireEvent.click(modal);
    expect(screen.queryByTestId('process-step-modal')).toBeNull();
  });
  it('parses a result the run log cut short, and says it is cut', () => {
    const cut = { ...RUN, reasoning: [], tools: [{ step: 1, tool: 'list_agents_tool', input: '{}',
      output: '{"ok": true, "agents": [{"id": "orchestrator", "description": "Manages the ta... (truncated)' }] };
    render(<I18nProvider><ProcessGraph messageRuns={[cut]} /></I18nProvider>);
    fireEvent.click(screen.getAllByTestId('process-node').find((n) => n.textContent.includes('Tool:')));
    const modal = screen.getByTestId('process-step-modal');
    expect(within(modal).getByTestId('truncated-note')).toBeInTheDocument();
    expect(within(modal).getByText('orchestrator')).toBeInTheDocument();
    expect(within(modal).getByText('Manages the ta')).toBeInTheDocument();
    // An empty input reads as {}, not as a folder with nothing in it.
    expect(within(modal).getByText('{}')).toBeInTheDocument();
  });
  it('keeps duration on the second stats row, its pills unbreakable', () => {
    show();
    const run = screen.getByTestId('run-stats-run');
    expect(run.className).not.toContain('flex-wrap');
    for (const pill of run.children) expect(pill.className).toContain('whitespace-nowrap');
  });

  it('renders a markdown output as markdown, and plain text as it is', () => {
    const md = { ...RUN, output: 'Called **Visualizer Agent**.\n\n- one\n- two' };
    render(<I18nProvider><ProcessGraph messageRuns={[md]} /></I18nProvider>);
    const output = screen.getAllByTestId('process-node').find((n) => n.textContent.startsWith('Output'));
    expect(output.querySelector('strong')).toHaveTextContent('Visualizer Agent');
    expect(output.querySelectorAll('li')).toHaveLength(2);
    // The two-line summary on the run card drops the markers.
    expect(screen.getByText(/Called Visualizer Agent\./)).toBeInTheDocument();
    expect(screen.queryByText(/\*\*Visualizer/)).toBeNull();
  });

  it('shows a plain output as text with its line breaks', () => {
    show();
    const output = screen.getAllByTestId('process-node').find((n) => n.textContent.startsWith('Output'));
    expect(output.querySelector('.chat-md')).toBeNull();
    expect(output).toHaveTextContent('Found it');
  });
});
