import { describe, it, expect, vi } from 'vitest';
import { publish, subscribe } from '../viewBus';

// The bus is module-level state shared by every view on the page, so each test
// uses its own channel name rather than trying to reset it.
let n = 0;
const chan = (name) => `test:${name}:${n++}`;

describe('viewBus', () => {
  it('delivers a published payload to a subscriber', () => {
    const c = chan('basic');
    const seen = vi.fn();
    subscribe(c, seen);
    publish(c, { t: 1 });
    expect(seen).toHaveBeenCalledWith({ t: 1 });
  });

  it('delivers to every subscriber of the channel', () => {
    const c = chan('fanout');
    const a = vi.fn();
    const b = vi.fn();
    subscribe(c, a);
    subscribe(c, b);
    publish(c, 'x');
    expect(a).toHaveBeenCalledWith('x');
    expect(b).toHaveBeenCalledWith('x');
  });

  it('replays the last value to a late subscriber', () => {
    // A linked view mounted after the timebase was published still needs it.
    const c = chan('late');
    publish(c, { t: 7 });
    const late = vi.fn();
    subscribe(c, late);
    expect(late).toHaveBeenCalledWith({ t: 7 });
  });

  it('replays only the most recent value', () => {
    const c = chan('latest');
    publish(c, 1);
    publish(c, 2);
    const late = vi.fn();
    subscribe(c, late);
    expect(late).toHaveBeenCalledTimes(1);
    expect(late).toHaveBeenCalledWith(2);
  });

  it('does not replay to a subscriber of a channel nobody published to', () => {
    const quiet = vi.fn();
    subscribe(chan('quiet'), quiet);
    expect(quiet).not.toHaveBeenCalled();
  });

  it('keeps channels isolated', () => {
    const a = vi.fn();
    subscribe(chan('one'), a);
    publish(chan('two'), 'nope');
    expect(a).not.toHaveBeenCalled();
  });

  it('stops delivering after unsubscribe', () => {
    const c = chan('unsub');
    const seen = vi.fn();
    const off = subscribe(c, seen);
    publish(c, 1);
    off();
    publish(c, 2);
    expect(seen).toHaveBeenCalledTimes(1);
    expect(seen).toHaveBeenCalledWith(1);
  });

  it('still reaches the other subscribers when one throws', () => {
    const c = chan('throwing');
    const after = vi.fn();
    subscribe(c, () => { throw new Error('renderer blew up'); });
    subscribe(c, after);
    expect(() => publish(c, 'x')).not.toThrow();
    expect(after).toHaveBeenCalledWith('x');
  });

  it('survives a late subscriber that throws on the replayed value', () => {
    const c = chan('throwing-late');
    publish(c, 'x');
    expect(() => subscribe(c, () => { throw new Error('nope'); })).not.toThrow();
  });
});
