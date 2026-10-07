/**
 * The assistant's voice, in order: sentences go in as the answer streams,
 * each is synthesised as soon as it arrives (so the next one is ready when
 * the current one ends), and they are played one after another.
 *
 * `cancel()` drops everything, the clip playing included: a new question, a
 * press of the talk button or Stop must silence the old answer at once, and a
 * clip that finishes synthesising after that must never start playing.
 *
 * The queue knows nothing about HTTP or audio elements: `synthesize(item,
 * signal)` turns an item into a clip (or null to skip it), `play(clip,
 * signal)` plays one and resolves when it ends, `release(clip)` frees it.
 * useSpeaker.js binds them to /api/assistant/speak and an <audio> element;
 * the tests bind them to fakes (__tests__/speechQueue.test.js).
 */
export class SpeechQueue {
  constructor({ synthesize, play, release = null, onState = null, onError = null }) {
    this.synthesize = synthesize;
    this.play = play;
    this.release = release;
    this.onState = onState;
    this.onError = onError;
    this.entries = [];
    this.generation = 0;
    this.playing = false;
    this.controller = new AbortController();
  }

  /** Whether anything is playing or waiting to. */
  get busy() {
    return this.playing || this.entries.length > 0;
  }

  /** Add one thing to say; its clip is requested right away. */
  enqueue(item) {
    const { signal } = this.controller;
    const ready = Promise.resolve()
      .then(() => this.synthesize(item, signal))
      .catch((err) => {
        if (!signal.aborted && this.onError) this.onError(err, item);
        return null;
      });
    this.entries.push({ item, ready });
    this._pump();
  }

  async _pump() {
    if (this.playing) return;
    const generation = this.generation;
    this.playing = true;
    if (this.onState) this.onState(true);
    while (this.entries.length && generation === this.generation) {
      const { ready } = this.entries.shift();
      const clip = await ready;
      if (generation !== this.generation) {
        if (clip && this.release) this.release(clip);
        return;
      }
      if (!clip) continue;
      try {
        await this.play(clip, this.controller.signal);
      } catch (err) {
        if (!this.controller.signal.aborted && this.onError) this.onError(err, clip);
      } finally {
        if (this.release) this.release(clip);
      }
    }
    if (generation === this.generation) {
      this.playing = false;
      if (this.onState) this.onState(false);
    }
  }

  /** Silence: stop the clip playing and forget everything queued. */
  cancel() {
    const wasBusy = this.busy;
    this.generation += 1;
    this.controller.abort();
    this.controller = new AbortController();
    const dropped = this.entries;
    this.entries = [];
    for (const { ready } of dropped) {
      ready.then((clip) => { if (clip && this.release) this.release(clip); });
    }
    this.playing = false;
    if (wasBusy && this.onState) this.onState(false);
  }
}

export default SpeechQueue;
