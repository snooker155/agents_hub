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

const GMAIL = { id: 'gmail', label: 'Gmail', domains: ['gmail.com'], auth: 'app_password', help_url: 'https://g/help',
  watcher: { host: 'imap.gmail.com', port: 993, ssl: true }, channel: { imap_host: 'imap.gmail.com' } };
const KINDS = { kinds: [
  { kind: 'imap', fields: [
    { name: 'use_google', type: 'bool', required: false, default: false },
    { name: 'host', type: 'str', required: true, optional_when: { use_google: true } },
    { name: 'port', type: 'int', required: false, default: 993 },
    { name: 'username', type: 'str', required: true, optional_when: { use_google: true } },
    { name: 'password_secret', type: 'secret', required: true, optional_when: { use_google: true }, hidden_when: { use_google: true } },
    { name: 'ssl', type: 'bool', required: false, default: true },
  ], presets: [GMAIL] },
  { kind: 'http', fields: [{ name: 'url', type: 'str', required: true }, { name: 'json_path', type: 'str', required: false, default: '' }] },
], interval: { min: 15, max: 21600, default: 120 },
google: { connected: true, gmail: true, account_email: 'anna@gmail.com' } };

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

  it("carries the selected workspace when reading a kind's fields (Google readiness)", async () => {
    renderIt();
    await waitFor(() => expect(api.getWatcherKinds).toHaveBeenCalledWith('default'));
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

  it('fills the IMAP host from a provider preset or a typed address', async () => {
    renderIt();
    await waitFor(() => expect(screen.getByText('watchers.add')).toBeInTheDocument());
    fireEvent.click(screen.getByText('watchers.add'));
    // Picking Gmail fills host, port and TLS and shows the app password rule.
    fireEvent.change(screen.getByTestId('mail-preset'), { target: { value: 'gmail' } });
    expect(screen.getByLabelText('watchers.form.fields.host')).toHaveValue('imap.gmail.com');
    expect(screen.getByLabelText('watchers.form.fields.port')).toHaveValue(993);
    expect(screen.getByTestId('mail-preset-hint')).toHaveTextContent('mailPresets.auth.app_password');
    expect(screen.getByText('mailPresets.help').closest('a')).toHaveAttribute('href', 'https://g/help');
    // A hand-typed host turns the pick list back to custom.
    fireEvent.change(screen.getByLabelText('watchers.form.fields.host'), { target: { value: 'mail.corp.local' } });
    expect(screen.getByTestId('mail-preset')).toHaveValue('');
    // With no host yet, a known address domain picks the preset on its own.
    fireEvent.change(screen.getByLabelText('watchers.form.fields.host'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('watchers.form.fields.username'), { target: { value: 'anna@gmail.com' } });
    expect(screen.getByLabelText('watchers.form.fields.host')).toHaveValue('imap.gmail.com');
    expect(screen.getByTestId('mail-preset')).toHaveValue('gmail');
  });

  it('signs in with the connected Google account instead of a password secret', async () => {
    renderIt();
    await waitFor(() => expect(screen.getByText('watchers.add')).toBeInTheDocument());
    fireEvent.click(screen.getByText('watchers.add'));
    fireEvent.change(screen.getByLabelText('watchers.form.name'), { target: { value: 'gmail' } });
    expect(screen.getByLabelText('watchers.form.fields.password_secret')).toBeInTheDocument();
    // Gmail's preset offers the Google sign in; taking it hides the secret.
    fireEvent.change(screen.getByTestId('mail-preset'), { target: { value: 'gmail' } });
    fireEvent.click(screen.getByTestId('mail-preset-use-google'));
    expect(screen.getByLabelText('watchers.form.fields.use_google')).toBeChecked();
    expect(screen.queryByLabelText('watchers.form.fields.password_secret')).toBeNull();
    expect(screen.getByTestId('gmail-note')).toHaveTextContent('mailPresets.google.ready {"email":"anna@gmail.com"}');
    fireEvent.click(screen.getByText('watchers.form.save'));
    await waitFor(() => expect(api.createWatcher).toHaveBeenCalledTimes(1));
    expect(api.createWatcher.mock.calls[0][0].config).toMatchObject({ use_google: true, host: 'imap.gmail.com' });
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
