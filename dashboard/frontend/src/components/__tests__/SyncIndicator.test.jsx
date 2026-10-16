import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const sync = vi.hoisted(() => ({ getHubSync: vi.fn(), runHubSync: vi.fn(), relativeTime: () => 'now' }));
vi.mock('../../api/hubSync', () => sync);
vi.mock('../../i18n', () => ({ useI18n: () => ({ t: (k) => k, language: 'en' }) }));

import SyncIndicator from '../SyncIndicator';

const on = { enabled: true, encrypted: true, key_unlocked: true, mappings: [], last_result: {}, conflicts: 0 };
const show = () => render(<MemoryRouter><SyncIndicator /></MemoryRouter>);

describe('SyncIndicator', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders nothing while sync is off', async () => {
    sync.getHubSync.mockResolvedValue({ data: { ...on, enabled: false } });
    show();
    await waitFor(() => expect(sync.getHubSync).toHaveBeenCalled());
    expect(screen.queryByTestId('sync-indicator')).toBeNull();
  });

  it('shows the encrypted state in green', async () => {
    sync.getHubSync.mockResolvedValue({ data: on });
    show();
    await waitFor(() => expect(screen.getByTestId('sync-indicator')).toHaveAttribute('data-tone', 'emerald'));
    expect(screen.getByTestId('sync-label')).toHaveTextContent('hubSync.indicator.encrypted');
  });

  it('shows Locked in red when the key is not unlocked', async () => {
    sync.getHubSync.mockResolvedValue({ data: { ...on, key_unlocked: false } });
    show();
    await waitFor(() => expect(screen.getByTestId('sync-indicator')).toHaveAttribute('data-tone', 'red'));
    expect(screen.getByTestId('sync-label')).toHaveTextContent('hubSync.indicator.locked');
  });

  it('turns amber on an error and shows it in the dropdown', async () => {
    sync.getHubSync.mockResolvedValue({ data: { ...on, encrypted: false, last_error: 'boom', conflicts: 2 } });
    show();
    await waitFor(() => expect(screen.getByTestId('sync-indicator')).toHaveAttribute('data-tone', 'amber'));
    fireEvent.click(screen.getByRole('button', { name: /hubSync.indicator.plain|hubSync.indicator.tooltip/ }));
    expect(screen.getByText(/boom/)).toBeInTheDocument();
    expect(screen.getByText('hubSync.indicator.plainNote')).toBeInTheDocument();
  });

  it('turns amber on has_error alone, which is what a non-administrator gets', async () => {
    sync.getHubSync.mockResolvedValue({ data: { ...on, last_error: '', has_error: true } });
    show();
    await waitFor(() => expect(screen.getByTestId('sync-indicator')).toHaveAttribute('data-tone', 'amber'));
    fireEvent.click(screen.getByRole('button', { name: /hubSync.indicator.encrypted|hubSync.indicator.tooltip/ }));
    expect(screen.getByTestId('sync-error-hidden')).toHaveTextContent('hubSync.status.errorHidden');
  });
});
