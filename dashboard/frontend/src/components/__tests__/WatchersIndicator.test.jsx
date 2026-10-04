import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getWatchersSummary: vi.fn() }));
vi.mock('../../api/watchers', () => api);
vi.mock('../workspace', () => ({ useWorkspace: () => ({ workspaceFilter: 'default' }) }));
vi.mock('../stream', () => ({ useLiveRefetch: () => {} }));
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k) }),
}));

import WatchersIndicator from '../WatchersIndicator';

const watcher = (overrides = {}) => ({
  id: 'w1', name: 'inbox', kind: 'imap', active: true, paused_reason: null, last_error: null,
  last_checked_at: '2026-10-02T09:00:00Z', last_event: 'new mail from a@x: Spec',
  listeners: [{ agent_id: 'mailbot', name: 'Mailbot' }], ...overrides,
});

const renderIt = () => render(<MemoryRouter><WatchersIndicator /></MemoryRouter>);

describe('WatchersIndicator', () => {
  beforeEach(() => { api.getWatchersSummary.mockReset(); });

  it('stays hidden while the workspace has no watchers', async () => {
    api.getWatchersSummary.mockResolvedValue({ data: { active: 0, paused: 0, errors: 0, total: 0, watchers: [] } });
    renderIt();
    await waitFor(() => expect(api.getWatchersSummary).toHaveBeenCalledWith('default'));
    expect(screen.queryByTestId('watchers-indicator')).toBeNull();
  });

  it('shows the active count and lists the watchers with their agents on click', async () => {
    api.getWatchersSummary.mockResolvedValue({ data: {
      active: 1, paused: 1, errors: 1, total: 2,
      watchers: [watcher(), watcher({ id: 'w2', name: 'status', kind: 'http', active: false, paused_reason: 'errors', last_error: 'HTTP 500', listeners: [] })],
    } });
    renderIt();
    await waitFor(() => expect(screen.getByTestId('watchers-count')).toHaveTextContent('1'));
    fireEvent.click(screen.getByTestId('watchers-count'));
    expect(screen.getByText('inbox')).toBeInTheDocument();
    expect(screen.getByText(/watchers.indicator.wakes .*Mailbot/)).toBeInTheDocument();
    expect(screen.getByText('HTTP 500')).toBeInTheDocument();
    expect(screen.getByText('watchers.indicator.nobodyListens')).toBeInTheDocument();
    expect(screen.getByText('watchers.indicator.manage').closest('a')).toHaveAttribute('href', '/watchers');
  });
});
