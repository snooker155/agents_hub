import { describe, expect, it, vi } from 'vitest';
import { SpeechQueue } from '../speechQueue';

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

describe('the speech queue', () => {
  it('plays in order even when a later clip is ready first', async () => {
    const synth = { a: deferred(), b: deferred() };
    const played = [];
    const q = new SpeechQueue({
      synthesize: (item) => synth[item].promise,
      play: async (clip) => { played.push(clip); },
    });
    q.enqueue('a');
    q.enqueue('b');
    synth.b.resolve('clip-b');
    await tick();
    expect(played).toEqual([]);
    synth.a.resolve('clip-a');
    await tick();
    await tick();
    expect(played).toEqual(['clip-a', 'clip-b']);
  });

  it('asks for every clip at once, so the next is ready when one ends', () => {
    const synthesize = vi.fn(() => new Promise(() => {}));
    const q = new SpeechQueue({ synthesize, play: async () => {} });
    q.enqueue('a');
    q.enqueue('b');
    q.enqueue('c');
    return tick().then(() => expect(synthesize).toHaveBeenCalledTimes(3));
  });

  it('cancel stops the clip playing and never plays what was queued', async () => {
    const ends = deferred();
    const played = [];
    const released = [];
    let aborted = false;
    const q = new SpeechQueue({
      synthesize: async (item) => item,
      play: (clip, signal) => {
        played.push(clip);
        signal.addEventListener('abort', () => { aborted = true; ends.resolve(); });
        return ends.promise;
      },
      release: (clip) => released.push(clip),
    });
    q.enqueue('first');
    q.enqueue('second');
    await tick();
    expect(played).toEqual(['first']);
    q.cancel();
    await tick();
    await tick();
    expect(aborted).toBe(true);
    expect(played).toEqual(['first']);
    expect(released).toEqual(expect.arrayContaining(['first', 'second']));
    expect(q.busy).toBe(false);
  });

  it('a clip that arrives after a cancel is dropped, and the queue works again', async () => {
    const late = deferred();
    const played = [];
    const q = new SpeechQueue({
      synthesize: (item) => (item === 'old' ? late.promise : Promise.resolve(item)),
      play: async (clip) => { played.push(clip); },
    });
    q.enqueue('old');
    q.cancel();
    q.enqueue('new');
    late.resolve('old');
    await tick();
    await tick();
    expect(played).toEqual(['new']);
  });

  it('skips an item with no clip and reports a failed one', async () => {
    const onError = vi.fn();
    const played = [];
    const q = new SpeechQueue({
      synthesize: async (item) => {
        if (item === 'boom') throw new Error('no voice');
        return item === 'empty' ? null : item;
      },
      play: async (clip) => { played.push(clip); },
      onError,
    });
    ['empty', 'boom', 'ok'].forEach((i) => q.enqueue(i));
    await tick();
    await tick();
    expect(played).toEqual(['ok']);
    expect(onError).toHaveBeenCalledTimes(1);
  });

  it('says when it starts and stops speaking', async () => {
    const states = [];
    const q = new SpeechQueue({ synthesize: async (i) => i, play: async () => {}, onState: (s) => states.push(s) });
    q.enqueue('a');
    await tick();
    await tick();
    expect(states).toEqual([true, false]);
  });
});
