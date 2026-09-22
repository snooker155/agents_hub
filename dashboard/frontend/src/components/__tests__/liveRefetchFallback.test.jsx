import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render } from '@testing-library/react';
import { StreamContext, useLiveRefetch } from '../stream';

// A stream that never delivers anything: the point of the fallback is what
// happens when the channel a page follows has gone quiet.
const silentStream = { connected: true, on: () => () => {}, acquireChannel: () => () => {}, onRefetch: () => () => {} };

function Harness({ onTick, fallbackMs, enabled = true }) {
  useLiveRefetch(onTick, { type: 'team_runs.changed', enabled, fallbackMs });
  return null;
}

const show = (props) => render(
  <StreamContext.Provider value={silentStream}><Harness {...props} /></StreamContext.Provider>,
);

describe('useLiveRefetch fallbackMs', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('does not fetch on mount: the caller already loaded its data', () => {
    const onTick = vi.fn();
    show({ onTick, fallbackMs: 30000 });
    expect(onTick).not.toHaveBeenCalled();
  });

  it('refetches on the slow floor when no event ever arrives', () => {
    const onTick = vi.fn();
    show({ onTick, fallbackMs: 30000 });
    act(() => { vi.advanceTimersByTime(30000); });
    expect(onTick).toHaveBeenCalledTimes(1);
    act(() => { vi.advanceTimersByTime(60000); });
    expect(onTick).toHaveBeenCalledTimes(3);
  });

  it('stays silent without a fallback, which is the default', () => {
    const onTick = vi.fn();
    show({ onTick });
    act(() => { vi.advanceTimersByTime(120000); });
    expect(onTick).not.toHaveBeenCalled();
  });

  it('stops while the subscription is disabled', () => {
    const onTick = vi.fn();
    show({ onTick, fallbackMs: 30000, enabled: false });
    act(() => { vi.advanceTimersByTime(120000); });
    expect(onTick).not.toHaveBeenCalled();
  });
});
