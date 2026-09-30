import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryHistoryPanel } from '../MemoryHistoryPanel';
import { I18nProvider } from '../../../i18n';

const listMemoryVersions = vi.fn();
const restoreMemoryVersion = vi.fn(() => Promise.resolve({ data: { op: 'restore' } }));
const redactMemoryVersion = vi.fn(() => Promise.resolve({ data: { op: 'redact' } }));

vi.mock('../../../api/memoryVersions', () => ({
  listMemoryVersions: (...args) => listMemoryVersions(...args),
  restoreMemoryVersion: (...args) => restoreMemoryVersion(...args),
  redactMemoryVersion: (...args) => redactMemoryVersion(...args),
}));

const ROWS = [
  {
    id: 2, memory_id: 'pool-1', kind: 'block', item_key: 'user', version: 2, op: 'update',
    value: { name: 'user', value: 'second', limit_chars: 2000, description: '', read_only: false },
    actor_kind: 'user', actor_id: 'alice', run_id: null, at: '2026-09-24T10:00:00Z', redacted: false,
  },
  {
    id: 1, memory_id: 'pool-1', kind: 'block', item_key: 'user', version: 1, op: 'create',
    value: { name: 'user', value: 'first', limit_chars: 2000, description: '', read_only: false },
    actor_kind: 'agent', actor_id: 'job_scout', run_id: 'run-9', at: '2026-09-24T09:00:00Z', redacted: false,
  },
];

const show = (props = {}) => render(
  <I18nProvider>
    <MemoryHistoryPanel poolId="pool-1" onClose={() => {}} onChanged={() => {}} {...props} />
  </I18nProvider>,
);

beforeEach(() => {
  listMemoryVersions.mockReset();
  restoreMemoryVersion.mockClear();
  redactMemoryVersion.mockClear();
  listMemoryVersions.mockResolvedValue({ data: { versions: ROWS } });
});

describe('MemoryHistoryPanel', () => {
  it('lists every version with its op and actor', async () => {
    show({ kind: 'block', itemKey: 'user', itemLabel: 'user' });
    await waitFor(() => expect(screen.getByText('Created')).toBeInTheDocument());
    expect(screen.getByText('Updated')).toBeInTheDocument();
    expect(screen.getByText(/alice/)).toBeInTheDocument();
    expect(screen.getByText(/job_scout/)).toBeInTheDocument();
  });

  it('shows an empty state when the item has no history', async () => {
    listMemoryVersions.mockResolvedValue({ data: { versions: [] } });
    show({ kind: 'block', itemKey: 'user' });
    await waitFor(() => expect(screen.getByText(/No history yet/)).toBeInTheDocument());
  });

  it('shows a line diff against the previous version once expanded', async () => {
    show({ kind: 'block', itemKey: 'user', itemLabel: 'user' });
    await waitFor(() => expect(screen.getByText('Updated')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Updated'));
    expect(screen.getByText(/second/)).toBeInTheDocument();
    expect(screen.getByText(/first/)).toBeInTheDocument();
  });

  it('restores a version after confirming', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    show({ kind: 'block', itemKey: 'user', itemLabel: 'user' });
    await waitFor(() => expect(screen.getByText('Created')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Created'));
    fireEvent.click(screen.getByText('Restore'));
    await waitFor(() => expect(restoreMemoryVersion).toHaveBeenCalledWith('pool-1', 1));
  });

  it('does nothing when the restore confirm is declined', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    show({ kind: 'block', itemKey: 'user', itemLabel: 'user' });
    await waitFor(() => expect(screen.getByText('Created')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Created'));
    fireEvent.click(screen.getByText('Restore'));
    expect(restoreMemoryVersion).not.toHaveBeenCalled();
  });

  it('redacts with also_current true when the checkbox is ticked', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    show({ kind: 'block', itemKey: 'user', itemLabel: 'user' });
    await waitFor(() => expect(screen.getByText('Created')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Created'));
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByText('Redact'));
    await waitFor(() => expect(redactMemoryVersion).toHaveBeenCalledWith('pool-1', 1, { also_current: true }));
  });

  it('redacts with also_current false by default', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    show({ kind: 'block', itemKey: 'user', itemLabel: 'user' });
    await waitFor(() => expect(screen.getByText('Created')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Created'));
    fireEvent.click(screen.getByText('Redact'));
    await waitFor(() => expect(redactMemoryVersion).toHaveBeenCalledWith('pool-1', 1, { also_current: false }));
  });

  it('shows the redaction marker instead of a diff, and no redact button, for an already redacted row', async () => {
    listMemoryVersions.mockResolvedValue({
      data: {
        versions: [{
          ...ROWS[0], redacted: true, value: '[redacted]',
        }],
      },
    });
    show({ kind: 'block', itemKey: 'user', itemLabel: 'user' });
    await waitFor(() => expect(screen.getByText('Updated')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Updated'));
    expect(screen.getByText(/redacted/i)).toBeInTheDocument();
    expect(screen.queryByText('Redact')).not.toBeInTheDocument();
  });

  it('shows the item badge in the pool wide view but not in a single item view', async () => {
    show({});
    await waitFor(() => expect(screen.getByText('Created')).toBeInTheDocument());
    expect(screen.getAllByText(/block:user/).length).toBe(2);
  });
});
