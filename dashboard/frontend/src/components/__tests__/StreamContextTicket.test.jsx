import { act, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/*
 * The credential StreamContext puts on /api/stream: a one-time ticket minted
 * before each (re)connect, never the session token itself, and the legacy
 * `token=` form only when the backend cannot mint (an older backend's 404).
 */

const mintAuthTicket = vi.fn();
vi.mock('../../api', () => ({
  API_ORIGIN: '',
  getAuthToken: () => 'sess-token',
  mintAuthTicket: (...a) => mintAuthTicket(...a),
}));

import { StreamProvider } from '../StreamContext';

class FakeEventSource {
  constructor(url) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  close() {}

  fail() { this.onerror?.(); }
}
FakeEventSource.instances = [];

const latest = () => FakeEventSource.instances[FakeEventSource.instances.length - 1];
const urlParams = (es) => new URLSearchParams(es.url.split('?')[1] || '');

describe('StreamContext credentials', () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    mintAuthTicket.mockReset();
    vi.stubGlobal('EventSource', FakeEventSource);
  });

  afterEach(() => vi.unstubAllGlobals());

  it('connects with a fresh ticket each time and never the token', async () => {
    mintAuthTicket.mockResolvedValueOnce('T1').mockResolvedValueOnce('T2');
    render(<StreamProvider><div /></StreamProvider>);
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(urlParams(latest()).get('ticket')).toBe('T1');
    expect(urlParams(latest()).has('token')).toBe(false);

    vi.useFakeTimers();
    act(() => { latest().fail(); });
    await act(async () => { vi.advanceTimersByTime(3000); });
    vi.useRealTimers();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
    expect(urlParams(latest()).get('ticket')).toBe('T2');
  });

  it('falls back to the token when the backend cannot mint a ticket', async () => {
    mintAuthTicket.mockRejectedValue(Object.assign(new Error('nope'), { response: { status: 404 } }));
    render(<StreamProvider><div /></StreamProvider>);
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(urlParams(latest()).get('token')).toBe('sess-token');
    expect(urlParams(latest()).has('ticket')).toBe(false);
  });
});
