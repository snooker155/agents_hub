import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StreamProvider } from '../StreamContext';
import { useStream } from '../stream';

/*
 * The EventSource reconnect logic in StreamContext.jsx: what a (re)connect
 * sends, and how the `ready` frame's `resumed`/`source` fields are read.
 *
 * With the cross-replica broker bridge (common/broker_bridge.py) on, a
 * reconnect can be resumed from the shared Redis stream instead of the
 * per-process ring buffer — but only for the channels this tab actually
 * wants, and only if the last event id it hands back survives the trip: it
 * is a Redis stream id ("1695400000000-0") once the bridge is involved, not
 * the small integer the browser used to see, so it must not be mangled into
 * a Number() (NaN) on the way through.
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

  it('keeps a Redis stream id as the literal Last-Event-ID string, not Number()', () => {
    show();
    act(() => {
      latest().emit({ channel: '_meta', type: 'ready', client_id: 'c1', resumed: false, replayed: 0 });
    });
    act(() => {
      latest().emit({ channel: 'app', type: 'widgets.changed' }, '1695400000000-0');
    });

    act(() => { latest().fail(); });
    act(() => { vi.advanceTimersByTime(3000); });

    // Number('1695400000000-0') is NaN; the old `Number(e.lastEventId)`
    // coercion would have dropped this silently and sent nothing.
    expect(urlParams(latest()).get('since')).toBe('1695400000000-0');
  });

  it('does not refetch when a reconnect resumes via the stream catch-up path', () => {
    const getStream = show();
    const onTick = vi.fn();
    act(() => { getStream().onRefetch(onTick); });

    act(() => {
      latest().emit({ channel: '_meta', type: 'ready', client_id: 'c1', resumed: false, replayed: 0 });
    });

    // A reconnect that this time resumes from the Redis stream rather than
    // the in-memory ring buffer — `source` differs, `resumed` does not.
    act(() => { latest().fail(); });
    act(() => { vi.advanceTimersByTime(3000); });
    act(() => {
      latest().emit({
        channel: '_meta', type: 'ready', client_id: 'c1', resumed: true, replayed: 2, source: 'stream',
      });
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
