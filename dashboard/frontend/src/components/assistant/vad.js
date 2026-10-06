/**
 * Where speech starts and ends in a stream of levels: the hands-free modes'
 * ear when the workspace's transcription model listens (useHandsFree.js).
 * Pure, so it is tested with made-up levels (__tests__/vad.test.js).
 *
 * The threshold follows the room: a slow average of the level while nobody
 * speaks is the noise floor, and speech is a level a few times above it.
 * `sensitivity` above 1 asks for a louder voice: while the assistant itself
 * speaks, so its own voice from the speakers is less often taken for the
 * person's.
 *
 *   const vad = new VadSegmenter({ silenceMs: 900 });
 *   vad.push(level, now)   // 'start' | 'end' | null
 */
export const VAD_DEFAULTS = {
  //: Never below this, however quiet the room (levels as wav.js levelOf gives them).
  minThreshold: 0.07,
  //: Speech is this many times the noise floor.
  floorFactor: 3,
  //: Above the threshold this long before it counts as speech, not a click.
  startMs: 120,
  //: Below it this long and the utterance is over.
  silenceMs: 900,
  //: How fast the floor follows the room while nobody speaks.
  floorRate: 0.05,
  //: How fast it drops when the room gets quieter than it thought.
  floorFallRate: 0.3,
};

export class VadSegmenter {
  constructor(options = {}) {
    this.options = { ...VAD_DEFAULTS, ...options };
    this.sensitivity = 1;
    this.floor = null;
    this.inSpeech = false;
    this.aboveSince = null;
    this.belowSince = null;
    this.startedAt = null;
  }

  get threshold() {
    const { minThreshold, floorFactor } = this.options;
    return Math.max(minThreshold, (this.floor ?? 0) * floorFactor) * this.sensitivity;
  }

  set(options) {
    Object.assign(this.options, options);
  }

  /** Forget the utterance in progress (the phase changed, the mic paused). */
  reset() {
    this.inSpeech = false;
    this.aboveSince = null;
    this.belowSince = null;
    this.startedAt = null;
  }

  /**
   * The floor after one quiet level. The first one is capped where the
   * threshold is still the minimum: a person who speaks the moment the
   * microphone opens is not the room, and taking them for it would set the
   * threshold above their own voice. It rises slowly and falls fast.
   */
  nextFloor(level) {
    const { minThreshold, floorFactor, floorRate, floorFallRate } = this.options;
    if (this.floor === null) return Math.min(level, minThreshold / floorFactor);
    const rate = level < this.floor ? floorFallRate : floorRate;
    return this.floor + (level - this.floor) * rate;
  }

  push(level, now) {
    const { startMs, silenceMs } = this.options;
    const threshold = this.threshold;
    if (!this.inSpeech) {
      if (level >= threshold) {
        if (this.aboveSince === null) this.aboveSince = now;
        if (now - this.aboveSince >= startMs) {
          this.inSpeech = true;
          this.startedAt = this.aboveSince;
          this.belowSince = null;
          return 'start';
        }
      } else {
        this.aboveSince = null;
        this.floor = this.nextFloor(level);
      }
      return null;
    }
    // A little below the start threshold still counts as the same voice.
    if (level >= threshold * 0.7) {
      this.belowSince = null;
      return null;
    }
    if (this.belowSince === null) this.belowSince = now;
    if (now - this.belowSince >= silenceMs) {
      this.reset();
      return 'end';
    }
    return null;
  }
}

export default VadSegmenter;
