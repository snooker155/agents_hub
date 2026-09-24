import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// The hook takes frames from the hub's WebSocket relay when it can, and
// polls /frame when the socket will not open. Both paths end in the same
// `frame` shape, so the components above it never know which one they got.

const ok = (data) => Promise.resolve({ data });
const getBrowserFrame = vi.fn(() => ok({ url: 'https://example.com/polled', title: 'Polled', width: 1280, height: 800, image: 'data:image/jpeg;base64,BBBB' }));
const sendBrowserInput = vi.fn(() => ok({}));

vi.mock('../../../api/browser', () => ({
  getBrowserFrame: (...a) => getBrowserFrame(...a),
  sendBrowserInput: (...a) => sendBrowserInput(...a),
  browserStreamUrl: (id) => `ws://test/${id}`,
}));

import { useBrowserSession } from '../useBrowserSession';

class FakeSocket {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.closed = false;
    FakeSocket.instances.push(this);
  }
  close() { this.closed = true; }
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.clearAllMocks();
  vi.stubGlobal('WebSocket', FakeSocket);
});

afterEach(() => vi.unstubAllGlobals());

describe('useBrowserSession', () => {
  it('shows frames the socket pushes and sends input without polling', async () => {
    const { result, unmount } = renderHook(() => useBrowserSession('S1'));
    expect(FakeSocket.instances[0].url).toBe('ws://test/S1');
    act(() => {
      FakeSocket.instances[0].onmessage({ data: JSON.stringify({
        type: 'frame', seq: 1, url: 'https://example.com/live', title: 'Live', width: 1280, height: 800,
        image: 'data:image/jpeg;base64,AAAA', controlled_by: '',
      }) });
    });
    expect(result.current.frame.url).toBe('https://example.com/live');
    expect(result.current.live).toBe('ws');
    act(() => {
      FakeSocket.instances[0].onmessage({ data: JSON.stringify({ type: 'keepalive', seq: 1, controlled_by: 'ann' }) });
    });
    expect(result.current.frame.controlled_by).toBe('ann');
    await act(async () => { await result.current.send({ kind: 'click', x: 1, y: 2 }); });
    expect(sendBrowserInput).toHaveBeenCalledWith('S1', { kind: 'click', x: 1, y: 2 });
    expect(getBrowserFrame).not.toHaveBeenCalled();
    unmount();
    expect(FakeSocket.instances[0].closed).toBe(true);
  });

  it('falls back to polling when the socket closes before any frame', async () => {
    const { result } = renderHook(() => useBrowserSession('S2'));
    act(() => { FakeSocket.instances[0].onclose({ code: 1006 }); });
    await waitFor(() => expect(getBrowserFrame).toHaveBeenCalledWith('S2'));
    await waitFor(() => expect(result.current.frame?.url).toBe('https://example.com/polled'));
    expect(result.current.live).toBe('poll');
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it('polls when asked to', async () => {
    renderHook(() => useBrowserSession('S3', { transport: 'poll' }));
    await waitFor(() => expect(getBrowserFrame).toHaveBeenCalledWith('S3'));
    expect(FakeSocket.instances).toHaveLength(0);
  });
});
