import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { PoolsTab } from '../PoolsTab';
import { I18nProvider } from '../../../i18n';

const POOL = {
  id: 'pool-1', name: 'Team pool', description: '', workspace: 'default',
  blocks: [{ name: 'persona', value: 'a persona', limit_chars: 2000, description: '', read_only: false }],
  notes: [{ id: 'note-1', title: 'runbook', content: 'restart the worker', created_at: '2026-09-24T09:00:00Z' }],
  structured_data: {},
};

vi.mock('../../../api', () => ({
  getSharedMemory: () => Promise.resolve({ data: POOL }),
  createSharedMemory: () => Promise.resolve({ data: POOL }),
  deleteSharedMemory: () => Promise.resolve({ data: {} }),
  addMemoryNote: () => Promise.resolve({ data: POOL }),
  updateMemoryNote: () => Promise.resolve({ data: POOL }),
  deleteMemoryNote: () => Promise.resolve({ data: POOL }),
  upsertMemoryBlock: () => Promise.resolve({ data: POOL }),
  deleteMemoryBlock: () => Promise.resolve({ data: POOL }),
  upsertMemoryStructuredSlot: () => Promise.resolve({ data: POOL }),
  deleteMemoryStructuredSlot: () => Promise.resolve({ data: POOL }),
  getMemoryEpisodesStats: () => Promise.resolve({ data: { total: 0 } }),
  getMemoryGraphStats: () => Promise.resolve({ data: { node_count: 0 } }),
}));

vi.mock('../../../api/memoryVersions', () => ({
  listMemoryVersions: () => Promise.resolve({ data: { versions: [] } }),
  restoreMemoryVersion: () => Promise.resolve({ data: {} }),
  redactMemoryVersion: () => Promise.resolve({ data: {} }),
}));

const show = () => render(
  <I18nProvider>
    <PoolsTab memories={[POOL]} onRefresh={() => {}} workspaceFilter="default" onPoolSelected={() => {}} />
  </I18nProvider>,
);

describe('PoolsTab: version history entry points', () => {
  it('opens the whole pool history from the header button', async () => {
    show();
    fireEvent.click(screen.getByText('Team pool'));
    await waitFor(() => expect(screen.getByText('Pool history')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Pool history'));
    await waitFor(() => expect(screen.getByText('No history yet.')).toBeInTheDocument());
  });

  it('opens a block\'s own history filtered to that block', async () => {
    show();
    fireEvent.click(screen.getByText('Team pool'));
    await waitFor(() => expect(screen.getByText('Pool history')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Blocks'));
    await waitFor(() => expect(screen.getByText('persona')).toBeInTheDocument());

    const blockCard = screen.getByText('persona').closest('div.bg-white');
    const historyButton = within(blockCard).getAllByRole('button')[0];
    fireEvent.click(historyButton);

    await waitFor(() => expect(screen.getByText('History: persona')).toBeInTheDocument());
  });

  it('opens a note\'s own history filtered to that note', async () => {
    show();
    fireEvent.click(screen.getByText('Team pool'));
    await waitFor(() => expect(screen.getByText('Pool history')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Notes'));
    await waitFor(() => expect(screen.getByText('runbook')).toBeInTheDocument());

    const noteRow = screen.getByText('runbook').closest('div.p-3');
    const historyButton = within(noteRow).getAllByRole('button')[0];
    fireEvent.click(historyButton);

    await waitFor(() => expect(screen.getByText('History: runbook')).toBeInTheDocument());
  });
});
