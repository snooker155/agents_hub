import { beforeAll, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import { FlowLog } from '../FlowLog';
import * as api from '../../../api';

vi.mock('../../../api', () => ({ getRunTrail: vi.fn() }));

beforeAll(() => {
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  Element.prototype.scrollIntoView = Element.prototype.scrollIntoView || (() => {});
});

const show = (logs) => render(
  <MemoryRouter><I18nProvider><FlowLog logs={logs} nodeStatuses={[]} /></I18nProvider></MemoryRouter>,
);

describe('FlowLog: a node\'s row, its input and the files in its output', () => {
  it('shows a running node\'s input from the live line', () => {
    show([
      { type: 'flow_start', timestamp: '2026-10-22T10:00:00Z', content: 'Flow started' },
      { type: 'agent_start', node_id: 'n1', agent_name: 'Writer', timestamp: '2026-10-22T10:00:01Z',
        content: 'Running Writer', input: 'Write the report', turn: { timeline: [] } },
    ]);
    expect(screen.getByText('Write the report')).toBeInTheDocument();
  });

  it('links the files a finished node saved on its line, without the diff', () => {
    show([
      { type: 'agent_start', node_id: 'n1', agent_name: 'Writer', timestamp: '2026-10-22T10:00:01Z', input: 'go' },
      { type: 'agent_finish', node_id: 'n1', agent_name: 'Writer', timestamp: '2026-10-22T10:00:09Z',
        run_id: 'r1', stats: { tools: 0, errors: 0, thoughts: 0 }, output: 'done',
        files: [{ op: 'add', path: 'out/report.md', url: '/artifacts?file=file_1' }] },
    ]);
    const link = screen.getByText('report.md').closest('a');
    expect(link).toHaveAttribute('href', '/artifacts?file=file_1');
    // Inside the Output card, not a block of its own.
    const card = link.closest('[data-testid="process-node"]');
    expect(card).toHaveTextContent('done');
    expect(card).not.toHaveTextContent('go');
    expect(api.getRunTrail).not.toHaveBeenCalled();
  });

  it('shows the expand mark of the hovered card alone', () => {
    show([
      { type: 'agent_finish', node_id: 'n1', agent_name: 'Writer', timestamp: '2026-10-22T10:00:09Z',
        input: 'go', output: 'done' },
    ]);
    const cards = screen.getAllByTestId('process-node');
    expect(cards).toHaveLength(2);
    // The row is a `group` too; the mark answers to its own card's group only.
    for (const card of cards) {
      expect(card.className).toContain('group/step');
      expect(card.querySelector('svg.opacity-0').getAttribute('class')).toContain('group-hover/step:opacity-100');
    }
  });

  it('takes the files of a line saved without them from the node\'s run', async () => {
    api.getRunTrail.mockResolvedValue({ data: {
      timeline: [], stats: { tools: 0, errors: 0, thoughts: 0 },
      files: [{ op: 'add', path: 'notes.md', additions: 3, deletions: 0, url: '/artifacts?file=file_2' }],
    } });
    show([
      { type: 'agent_finish', node_id: 'n2', agent_name: 'Writer', timestamp: '2026-10-22T10:00:09Z',
        run_id: 'r2', stats: { tools: 0, errors: 0, thoughts: 0 }, output: 'done' },
    ]);
    await waitFor(() => expect(screen.getByText('notes.md').closest('a')).toHaveAttribute('href', '/artifacts?file=file_2'));
    expect(screen.queryByText(/\+3/)).toBeNull();
  });

  it('shows the flow\'s own starting state, its request among it, before a run', () => {
    render(
      <MemoryRouter><I18nProvider>
        <FlowLog logs={[]} initialState={{ user_request: 'Weekly digest' }} nodeStatuses={[]} />
      </I18nProvider></MemoryRouter>,
    );
    expect(screen.getByText('Weekly digest')).toBeInTheDocument();
  });

  it('lets a run\'s snapshot replace the starting state', () => {
    render(
      <MemoryRouter><I18nProvider>
        <FlowLog
          logs={[{ type: 'flow_start', timestamp: '2026-10-22T10:00:00Z', state: { user_request: 'Today only' } }]}
          initialState={{ user_request: 'Weekly digest' }}
          nodeStatuses={[]}
        />
      </I18nProvider></MemoryRouter>,
    );
    expect(screen.getByText('Today only')).toBeInTheDocument();
    expect(screen.queryByText('Weekly digest')).toBeNull();
  });
});
