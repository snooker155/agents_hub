import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { StreamContext } from '../../stream';
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
  testTelegramToken: vi.fn(),
  deleteTelegramBinding: vi.fn(),
}));

import {
  getTelegramConfig, getTelegramStatus, getTelegramBindings,
} from '../../../api';

describe('TelegramConnector: live status instead of polling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getTelegramConfig.mockResolvedValue({ data: config });
    getTelegramStatus.mockResolvedValue({ data: status });
    getTelegramBindings.mockResolvedValue({ data: [] });
  });

  const show = () => {
    const { stream, emit } = makeStream();
    const utils = render(
      <StreamContext.Provider value={stream}>
        <I18nProvider><TelegramConnector /></I18nProvider>
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
});
