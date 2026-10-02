import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getWatchers: vi.fn(), getWatcherKinds: vi.fn(), createWatcher: vi.fn(), updateWatcher: vi.fn(),
  deleteWatcher: vi.fn(), pauseWatcher: vi.fn(), resumeWatcher: vi.fn(), probeWatcher: vi.fn(),
}));
vi.mock('../../api/watchers', () => api);
vi.mock('../../components/workspace', () => ({ useWorkspace: () => ({ workspaceFilter: 'default', selectedWorkspace: 'default' }) }));
vi.mock('../../components/stream', () => ({ useLiveRefetch: () => {} }));
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k) }),
}));

import Watchers from '../Watchers';

const KINDS = { kinds: [
  { kind: 'imap', fields: [
    { name: 'host', type: 'str', required: true }, { name: 'username', type: 'str', required: true },
    { name: 'password_secret', type: 'secret', required: true }, { name: 'ssl', type: 'bool', required: false, default: true },
  ] },
  { kind: 'http', fields: [{ name: 'url', type: 'str', required: true }, { name: 'json_path', type: 'str', required: false, default: '' }] },
], interval: { min: 15, max: 21600, default: 120 } };

const row = (overrides = {}) => ({
  id: 'w1', workspace: 'default', name: 'inbox', kind: 'imap', config: { host: 'imap.x', username: 'me', folder: 'INBOX' },
  interval_seconds: 120, enabled: true, paused_reason: null, last_error: null, last_checked_at: null, last_event: null,
  fired: 3, active: true, listeners: [{ agent_id: 'mailbot', name: 'Mailbot' }], ...overrides,
});

const renderIt = () => render(<MemoryRouter><Watchers /></MemoryRouter>);

describe('Watchers page', () => {
  beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    api.getWatcherKinds.mockResolvedValue({ data: KINDS });
    api.getWatchers.mockResolvedValue({ data: [row()] });
    api.createWatcher.mockResolvedValue({ data: row({ id: 'w2' }) });
    api.pauseWatcher.mockResolvedValue({ data: row({ paused_reason: 'manual' }) });
    api.probeWatcher.mockResolvedValue({ data: { ok: true, dry_run: true, summary: 'no new messages', events: [], would_wake: ['mailbot'] } });
  });

  it('lists the watchers with the agents they wake', async () => {
    renderIt();
    await waitFor(() => expect(screen.getByTestId('watcher-list')).toBeInTheDocument());
    expect(screen.getByText('inbox')).toBeInTheDocument();
    expect(screen.getByText('Mailbot').closest('a')).toHaveAttribute('href', '/agents/mailbot');
    expect(screen.getByText('watchers.state.active')).toBeInTheDocument();
  });

  it('creates a watcher from the form with the kind\'s fields', async () => {
    renderIt();
    await waitFor(() => expect(screen.getByText('watchers.add')).toBeInTheDocument());
    fireEvent.click(screen.getByText('watchers.add'));
    expect(screen.getByTestId('watcher-form')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('watchers.form.name'), { target: { value: 'status' } });
    fireEvent.change(screen.getByLabelText('watchers.form.kind'), { target: { value: 'http' } });
    fireEvent.change(screen.getByLabelText('watchers.form.fields.url'), { target: { value: 'https://example.org/s.json' } });
    fireEvent.change(screen.getByLabelText('watchers.form.fields.json_path'), { target: { value: 'state' } });
    fireEvent.change(screen.getByLabelText('watchers.form.interval'), { target: { value: '60' } });
    fireEvent.click(screen.getByText('watchers.form.save'));
    await waitFor(() => expect(api.createWatcher).toHaveBeenCalledTimes(1));
    expect(api.createWatcher.mock.calls[0][0]).toMatchObject({
      workspace: 'default', name: 'status', kind: 'http', interval_seconds: 60,
      config: { url: 'https://example.org/s.json', json_path: 'state' },
    });
  });

  it('pauses and tests a watcher', async () => {
    renderIt();
    await waitFor(() => expect(screen.getByText('watchers.actions.pause')).toBeInTheDocument());
    fireEvent.click(screen.getByText('watchers.actions.pause'));
    await waitFor(() => expect(api.pauseWatcher).toHaveBeenCalledWith('w1'));
    fireEvent.click(screen.getByText('watchers.actions.probe'));
    await waitFor(() => expect(api.probeWatcher).toHaveBeenCalledWith('w1', true));
    await waitFor(() => expect(screen.getByTestId('probe-result')).toHaveTextContent('no new messages'));
    expect(screen.getByTestId('probe-result')).toHaveTextContent('mailbot');
  });
});
