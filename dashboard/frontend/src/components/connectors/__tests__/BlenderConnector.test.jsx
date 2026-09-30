import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { StreamContext } from '../../stream';
import { I18nProvider } from '../../../i18n';
import BlenderConnector from '../BlenderConnector';

// A fake app-channel stream: `emit` fires whatever handlers BlenderConnector
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

const config = {
  enabled: true, binary_path: '/usr/bin/blender', discovered_binary: '/usr/bin/blender',
  max_daemons: 2, idle_timeout_s: 300, command_timeout_s: 30,
  availability: { available: true, version: '4.0' },
};

vi.mock('../../../api', () => ({
  getBlenderConfig: vi.fn(),
  updateBlenderConfig: vi.fn(),
  testBlenderBinary: vi.fn(),
  getBlenderDaemons: vi.fn(),
  stopBlenderDaemon: vi.fn(),
  stopAllBlenderDaemons: vi.fn(),
}));

import { getBlenderConfig, getBlenderDaemons } from '../../../api';

describe('BlenderConnector: live engine list instead of polling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getBlenderConfig.mockResolvedValue({ data: config });
    getBlenderDaemons.mockResolvedValue({ data: { daemons: [], running: 0, max_daemons: 2 } });
  });

  const show = () => {
    const { stream, emit } = makeStream();
    const utils = render(
      <StreamContext.Provider value={stream}>
        <I18nProvider><BlenderConnector /></I18nProvider>
      </StreamContext.Provider>,
    );
    return { ...utils, emit };
  };

  it('fetches config and the daemon list once on mount', async () => {
    show();
    await waitFor(() => expect(getBlenderConfig).toHaveBeenCalledTimes(1));
    expect(getBlenderDaemons).toHaveBeenCalledTimes(1);
  });

  it('refetches the daemon list when the app channel says blender_daemons.changed', async () => {
    const { emit } = show();
    await waitFor(() => expect(getBlenderDaemons).toHaveBeenCalledTimes(1));

    getBlenderDaemons.mockResolvedValue({
      data: { daemons: [{ key: 'scene-1', pid: 123, objects: [], commands: 4, uptime_s: 30, rss_bytes: 1e7 }], running: 1, max_daemons: 2 },
    });
    act(() => { emit('app', { type: 'blender_daemons.changed' }); });
    await waitFor(() => expect(getBlenderDaemons).toHaveBeenCalledTimes(2));
    await screen.findByText('scene-1');
  });

  it('ignores unrelated app-channel events', async () => {
    const { emit } = show();
    await waitFor(() => expect(getBlenderDaemons).toHaveBeenCalledTimes(1));
    act(() => { emit('app', { type: 'agents.changed' }); });
    await new Promise((r) => setTimeout(r, 10));
    expect(getBlenderDaemons).toHaveBeenCalledTimes(1);
  });
});
