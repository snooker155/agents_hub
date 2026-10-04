import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { StreamContext } from '../../stream';
import { WorkspaceContext } from '../../workspace';
import { I18nProvider } from '../../../i18n';
import TelegramConnector from '../TelegramConnector';

// A fake app-channel stream: `emit` fires whatever handlers TelegramConnector
// subscribed on that channel, standing in for a real `X.changed` SSE event.
function makeStream() {
  const handlers = {};
  const stream = {
    connected: true,
    on: (channel, handler) => {
      (handlers[channel] = handlers[channel] || []).push(handler);
      return () => { handlers[channel] = (handlers[channel] || []).filter((h) => h !== handler); };
    },
    acquireChannel: () => () => {},
    onRefetch: () => () => {},
  };
  return { stream, emit: (channel, event) => (handlers[channel] || []).forEach((h) => h(event)) };
}

const config = { enabled: true, has_token: true, bot_username: 'first_bot', running: true };
const status = { running: true, bot_username: 'first_bot', last_poll: null, last_error: null };

vi.mock('../../../api', () => ({
  getTelegramConfig: vi.fn(),
  getTelegramStatus: vi.fn(),
  getTelegramBindings: vi.fn(),
  updateTelegramConfig: vi.fn(),
  deleteTelegramConfig: vi.fn(),
  testTelegramToken: vi.fn(),
  deleteTelegramBinding: vi.fn(),
}));

import {
  getTelegramConfig, getTelegramStatus, getTelegramBindings, deleteTelegramConfig,
} from '../../../api';

describe('TelegramConnector: live status instead of polling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getTelegramConfig.mockResolvedValue({ data: config });
    getTelegramStatus.mockResolvedValue({ data: status });
    getTelegramBindings.mockResolvedValue({ data: [] });
  });

  const show = (workspace = 'default') => {
    const { stream, emit } = makeStream();
    const utils = render(
      <StreamContext.Provider value={stream}>
        <WorkspaceContext.Provider value={{ selectedWorkspace: workspace }}>
          <I18nProvider><TelegramConnector /></I18nProvider>
        </WorkspaceContext.Provider>
      </StreamContext.Provider>,
    );
    return { ...utils, emit };
  };

  it('fetches config, status and bindings once on mount', async () => {
    show();
    await waitFor(() => expect(getTelegramConfig).toHaveBeenCalledTimes(1));
    expect(getTelegramStatus).toHaveBeenCalledTimes(1);
    expect(getTelegramBindings).toHaveBeenCalledTimes(1);
  });

  it('refreshes status when the app channel says telegram.changed', async () => {
    const { emit } = show();
    await waitFor(() => expect(getTelegramStatus).toHaveBeenCalledTimes(1));

    getTelegramStatus.mockResolvedValue({ data: { ...status, bot_username: 'second_bot' } });
    act(() => { emit('app', { type: 'telegram.changed' }); });
    await waitFor(() => expect(getTelegramStatus).toHaveBeenCalledTimes(2));
    await screen.findByText('@second_bot');
  });

  it('ignores unrelated app-channel events', async () => {
    const { emit } = show();
    await waitFor(() => expect(getTelegramStatus).toHaveBeenCalledTimes(1));
    act(() => { emit('app', { type: 'agents.changed' }); });
    // No debounce window to wait out for an event that was never subscribed to.
    await new Promise((r) => setTimeout(r, 10));
    expect(getTelegramStatus).toHaveBeenCalledTimes(1);
  });

  it('carries the selected workspace on every call, falling back to "default"', async () => {
    show('acme');
    await waitFor(() => expect(getTelegramConfig).toHaveBeenCalledWith('acme'));
    expect(getTelegramStatus).toHaveBeenCalledWith('acme');
    expect(getTelegramBindings).toHaveBeenCalledWith('acme');
  });

  it('falls back to the default workspace when none is selected', async () => {
    const { stream } = makeStream();
    render(
      <StreamContext.Provider value={stream}>
        <WorkspaceContext.Provider value={{ selectedWorkspace: '' }}>
          <I18nProvider><TelegramConnector /></I18nProvider>
        </WorkspaceContext.Provider>
      </StreamContext.Provider>,
    );
    await waitFor(() => expect(getTelegramConfig).toHaveBeenCalledWith('default'));
  });

  it('shows a badge, a read only token field and a "Define for this workspace" button when the bot is only the default workspace\'s', async () => {
    getTelegramConfig.mockResolvedValue({ data: { ...config, source: 'default' } });
    show('acme');
    const badge = await screen.findByTestId('connector-source-badge');
    expect(badge.textContent).toMatch(/default workspace/i);
    expect(screen.getByPlaceholderText('Leave empty to keep the existing token')).toBeDisabled();
    expect(screen.queryByText('Save token')).toBeNull();
    const defineButton = screen.getByTestId('connector-define-here');
    act(() => { defineButton.click(); });
    await waitFor(() => expect(screen.getByPlaceholderText('Leave empty to keep the existing token')).not.toBeDisabled());
    expect(screen.getByText('Save token')).toBeInTheDocument();
  });

  it('offers "Remove, use the default\'s" when this workspace has its own bot, and calls DELETE on confirm', async () => {
    getTelegramConfig.mockResolvedValue({ data: { ...config, source: 'here' } });
    deleteTelegramConfig.mockResolvedValue({ data: { ...config, source: 'default' } });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    show('acme');
    const removeButton = await screen.findByTestId('connector-remove-here');
    act(() => { removeButton.click(); });
    await waitFor(() => expect(deleteTelegramConfig).toHaveBeenCalledWith('acme'));
    expect(confirmSpy).toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('does not ask to remove without confirming first', async () => {
    getTelegramConfig.mockResolvedValue({ data: { ...config, source: 'here' } });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    show('acme');
    const removeButton = await screen.findByTestId('connector-remove-here');
    act(() => { removeButton.click(); });
    expect(deleteTelegramConfig).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('shows no define/remove button in the default workspace, only the badge', async () => {
    getTelegramConfig.mockResolvedValue({ data: { ...config, source: 'here' } });
    show('default');
    await screen.findByTestId('connector-source-badge');
    expect(screen.queryByTestId('connector-define-here')).toBeNull();
    expect(screen.queryByTestId('connector-remove-here')).toBeNull();
  });
});
