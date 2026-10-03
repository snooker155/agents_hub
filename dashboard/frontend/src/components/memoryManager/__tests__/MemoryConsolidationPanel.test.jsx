import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryConsolidationPanel } from '../MemoryConsolidationPanel';
import { I18nProvider } from '../../../i18n';

const listMemoryConsolidations = vi.fn();
const startMemoryConsolidation = vi.fn();
const applyMemoryConsolidation = vi.fn(() => Promise.resolve({ data: { ok: true } }));
const discardMemoryConsolidation = vi.fn(() => Promise.resolve({ data: { status: 'discarded' } }));
const getAgents = vi.fn(() => Promise.resolve({ data: [{ id: 'dreamer', name: 'Dreamer' }] }));

vi.mock('../../../api/memoryConsolidation', () => ({
  listMemoryConsolidations: (...args) => listMemoryConsolidations(...args),
  startMemoryConsolidation: (...args) => startMemoryConsolidation(...args),
  applyMemoryConsolidation: (...args) => applyMemoryConsolidation(...args),
  discardMemoryConsolidation: (...args) => discardMemoryConsolidation(...args),
}));

vi.mock('../../../api', () => ({
  getAgents: (...args) => getAgents(...args),
}));

const DONE_ROW = {
  id: 'job-1', memory_id: 'pool-1', new_memory_id: 'pool-2', status: 'done',
  session_limit: 10, session_ids: [], summary: 'Merged the duplicate plan note.',
  diff: {
    blocks: [{ key: 'user', op: 'changed', before: 'old text', after: 'new text' }],
    notes: [{ key: 'plan', op: 'added', after: 'ship it' }],
    slots: [],
  },
  error: null, created_at: '2026-10-03T10:00:00Z', started_at: '2026-10-03T10:00:01Z',
  finished_at: '2026-10-03T10:00:05Z',
};

const show = (props = {}) => render(
  <I18nProvider>
    <MemoryConsolidationPanel poolId="pool-1" onClose={() => {}} onApplied={() => {}} {...props} />
  </I18nProvider>,
);

beforeEach(() => {
  listMemoryConsolidations.mockReset();
  startMemoryConsolidation.mockReset();
  applyMemoryConsolidation.mockClear();
  discardMemoryConsolidation.mockClear();
  getAgents.mockClear();
  listMemoryConsolidations.mockResolvedValue({ data: { consolidations: [DONE_ROW] } });
});

describe('MemoryConsolidationPanel', () => {
  it('shows an empty state when nothing has run yet', async () => {
    listMemoryConsolidations.mockResolvedValue({ data: { consolidations: [] } });
    show();
    await waitFor(() => expect(screen.getByText(/No consolidation has run yet/)).toBeInTheDocument());
  });

  it('lists a finished job and renders its diff', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Merged the duplicate plan note.')).toBeInTheDocument());
    expect(screen.getByText('user')).toBeInTheDocument();
    expect(screen.getByText('plan')).toBeInTheDocument();
    expect(screen.getByText('changed')).toBeInTheDocument();
    expect(screen.getByText('added')).toBeInTheDocument();
  });

  it('starts a new consolidation with the chosen session limit', async () => {
    startMemoryConsolidation.mockResolvedValue({
      data: { id: 'job-2', memory_id: 'pool-1', status: 'queued', diff: null },
    });
    show();
    await waitFor(() => expect(screen.getByText('Merged the duplicate plan note.')).toBeInTheDocument());

    const input = screen.getByDisplayValue('10');
    fireEvent.change(input, { target: { value: '5' } });
    fireEvent.click(screen.getByText('Run now'));

    await waitFor(() => expect(startMemoryConsolidation).toHaveBeenCalledWith('pool-1', { session_limit: 5 }));
  });

  it('switches the binding to the result once an agent is picked', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Merged the duplicate plan note.')).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue('Pick an agent to switch…'), { target: { value: 'dreamer' } });
    fireEvent.click(screen.getByText('Switch binding'));

    await waitFor(() => expect(applyMemoryConsolidation).toHaveBeenCalledWith('job-1', { agent_id: 'dreamer' }));
  });

  it('discards a finished result', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Merged the duplicate plan note.')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Discard'));
    await waitFor(() => expect(discardMemoryConsolidation).toHaveBeenCalledWith('job-1'));
  });

  it('shows the error reason for a failed job', async () => {
    listMemoryConsolidations.mockResolvedValue({
      data: {
        consolidations: [{
          ...DONE_ROW, id: 'job-3', status: 'failed', diff: null, summary: null,
          error: 'the model did not return a usable JSON proposal',
        }],
      },
    });
    show();
    await waitFor(() => expect(screen.getByText(/did not return a usable JSON proposal/)).toBeInTheDocument());
  });
});
