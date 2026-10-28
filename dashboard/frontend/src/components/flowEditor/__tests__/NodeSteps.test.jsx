import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nProvider } from '../../../i18n';
import NodeSteps from '../NodeSteps.jsx';
import { trailStats } from '../stepStats';
import * as api from '../../../api';

vi.mock('../../../api', () => ({ getRunTrail: vi.fn() }));

const show = (log) => render(
  <I18nProvider><NodeSteps log={log} /></I18nProvider>,
);

describe('NodeSteps: a flow node\'s steps folded inside its log line', () => {
  it('counts tools, failures and thoughts, leaving think/plan out of the calls', () => {
    expect(trailStats([
      { type: 'tool', tool: 'think' },
      { type: 'tool', tool: 'read', status: 'ok' },
      { type: 'tool', tool: 'write', status: 'error' },
      { type: 'reasoning', content: 'x' },
      { type: 'artifact', path: 'a' },
    ])).toEqual({ tools: 2, errors: 1, thoughts: 1, files: 1 });
  });

  it('is folded by default with the counts on its header, and opens to the live steps', () => {
    show({
      type: 'agent_start', run_id: 'r1',
      turn: { timeline: [
        { type: 'reasoning', kind: 'think', content: 'plan the search' },
        { type: 'tool', tool: 'web_search', input: 'q', output: 'res', status: 'ok' },
      ] },
    });
    expect(screen.queryByTestId('node-steps-body')).toBeNull();
    expect(screen.getByTitle('1 tool call')).toBeInTheDocument();
    expect(screen.getByTitle('1 thought')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { expanded: false }));
    expect(screen.getByTestId('node-steps-body')).toBeInTheDocument();
    expect(screen.getByText('Searching the web: q')).toBeInTheDocument();
    expect(api.getRunTrail).not.toHaveBeenCalled();
  });

  it('fetches a finished node\'s steps from its run only when opened', async () => {
    api.getRunTrail.mockResolvedValue({ data: {
      timeline: [
        { type: 'tool', tool: 'read_file', input: 'a', output: 'b', status: 'ok' },
        { type: 'text', text: 'the answer' },
      ],
      stats: { tools: 1, errors: 0, thoughts: 0 },
    } });
    show({ type: 'agent_finish', run_id: 'r2', stats: { tools: 1, errors: 0, thoughts: 0 }, output: 'the answer' });
    expect(api.getRunTrail).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { expanded: false }));
    await waitFor(() => expect(screen.getByText('Reading a file: a')).toBeInTheDocument());
    expect(api.getRunTrail).toHaveBeenCalledWith('r2');
    // The answer is the node's Output, not one of its steps.
    expect(screen.queryByText('the answer')).toBeNull();
  });

  it('shows nothing for a node that answered without a step', () => {
    const { container } = show({ type: 'agent_finish', run_id: 'r3', stats: { tools: 0, errors: 0, thoughts: 0 } });
    expect(container.querySelector('[data-testid="node-steps"]')).toBeNull();
  });
});
