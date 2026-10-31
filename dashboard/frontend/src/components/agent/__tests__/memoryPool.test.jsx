import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryPoolDetails } from '../memoryPool';
import { I18nProvider } from '../../../i18n';

// The bottom of an agent's Memory tab: every kind of memory its primary pool
// holds, read only, one tab each. It used to list `pool.files`, a field the
// pool route never sends, so it always said the pool had no files.
vi.mock('../../../api', () => ({
  listMemoryEpisodes: () => Promise.resolve({ data: { episodes: [
    { id: 'e1', kind: 'decision', outcome: 'success', summary: 'Picked Postgres', occurred_at: '2026-10-30T10:00:00Z' },
  ] } }),
  getMemoryGraphStats: () => Promise.resolve({ data: {
    node_count: 3, edge_count: 2, types: { person: 2, team: 1 }, relations: { member_of: 2 },
  } }),
}));

const pool = {
  id: 'pool-1', name: 'HR knowledge', description: 'Policies',
  blocks: [{ name: 'persona', value: 'You are careful.', limit_chars: 500, description: '' }],
  structured_data: { leave: { days: 30, carry_over: true } },
  notes: [
    { id: 'n1', title: 'Onboarding', content: 'Laptop on day one' },
    { id: 'n2', title: 'journal:2026-10-30', content: 'Asked about leave' },
  ],
  rag_files: [{ filename: 'handbook.md', workspace: 'default', status: 'indexed', chunks: 4 }],
};

const show = () => render(<I18nProvider><MemoryPoolDetails pool={pool} /></I18nProvider>);

describe('MemoryPoolDetails', () => {
  it('shows every kind of memory the pool holds, one tab each', async () => {
    show();
    expect(screen.getByText('You are careful.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /Structured/ }));
    expect(screen.getByText('leave')).toBeInTheDocument();
    expect(screen.getByText('carry_over')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /Notes/ }));
    expect(screen.getByText('Onboarding')).toBeInTheDocument();
    expect(screen.queryByText('Asked about leave')).toBeNull();

    fireEvent.click(screen.getByRole('tab', { name: /Journal/ }));
    expect(screen.getByText('2026-10-30')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /Episodes/ }));
    expect(await screen.findByText('Picked Postgres')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /Graph/ }));
    await waitFor(() => expect(screen.getByText('3 nodes, 2 edges.')).toBeInTheDocument());
    expect(screen.getByText('person · 2')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /Files/ }));
    expect(screen.getByText('handbook.md')).toBeInTheDocument();
    expect(screen.getByText('indexed')).toBeInTheDocument();
  });
});
