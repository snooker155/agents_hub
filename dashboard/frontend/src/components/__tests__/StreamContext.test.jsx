import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StreamProvider } from '../StreamContext';
import { useStream } from '../stream';

/*
 * The EventSource reconnect logic in StreamContext.jsx: what a (re)connect
 * sends, and how the `ready` frame's `resumed` field is read.
 */

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.onmessage = null;
    this.onerror = null;
    FakeEventSource.instances.push(this);
  }

  close() {}

  // Test helpers, not part of the real EventSource API.
  emit(payload, lastEventId) {
    this.onmessage?.({ data: JSON.stringify(payload), lastEventId: lastEventId ?? '' });
  }

  fail() {
    this.onerror?.();
  }
}
FakeEventSource.instances = [];

function Harness({ onReady }) {
  const stream = useStream();
  onReady(stream);
  return null;
}

function show() {
  let stream;
  render(
    <StreamProvider>
      <Harness onReady={(s) => { stream = s; }} />
    </StreamProvider>,
  );
  return () => stream;
}

const latest = () => FakeEventSource.instances[FakeEventSource.instances.length - 1];
const urlParams = (es) => new URLSearchParams(es.url.split('?')[1] || '');

describe('StreamContext reconnects', () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal('EventSource', FakeEventSource);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('opens with no channels= param when nothing has been acquired yet', () => {
    show();
    expect(urlParams(latest()).has('channels')).toBe(false);
  });

  it('sends the acquired dynamic channel set as channels= on reconnect', () => {
    const getStream = show();
    act(() => { getStream().acquireChannel('session:abc'); });

    act(() => { latest().fail(); });
    act(() => { vi.advanceTimersByTime(3000); });

    expect(urlParams(latest()).get('channels')).toBe('session:abc');
  });

  it('does not refetch when a reconnect resumes', () => {
    const getStream = show();
    const onTick = vi.fn();
    act(() => { getStream().onRefetch(onTick); });

    act(() => {
      latest().emit({ channel: '_meta', type: 'ready', client_id: 'c1', resumed: false, replayed: 0 });
    });

    act(() => { latest().fail(); });
    act(() => { vi.advanceTimersByTime(3000); });
    act(() => {
      latest().emit({ channel: '_meta', type: 'ready', client_id: 'c1', resumed: true, replayed: 2 });
    });

    expect(onTick).not.toHaveBeenCalled();
  });

  it('still refetches when a reconnect cannot be resumed at all', () => {
    const getStream = show();
    const onTick = vi.fn();
    act(() => { getStream().onRefetch(onTick); });

    act(() => {
      latest().emit({ channel: '_meta', type: 'ready', client_id: 'c1', resumed: false, replayed: 0 });
    });
    act(() => { latest().fail(); });
    act(() => { vi.advanceTimersByTime(3000); });
    act(() => {
      latest().emit({ channel: '_meta', type: 'ready', client_id: 'c2', resumed: false, replayed: 0 });
    });

    expect(onTick).toHaveBeenCalledTimes(1);
  });
});
