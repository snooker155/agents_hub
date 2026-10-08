import { describe, expect, it } from 'vitest';
import { VadSegmenter } from '../vad';

describe('speech from the first frame', () => {
  it('is heard when the person speaks the moment the microphone opens', () => {
    const vad = new VadSegmenter();
    // Speaking at once, the first syllable soft: that frame is the voice, not
    // the room, and must not lift the threshold above the words that follow.
    const events = [vad.push(0.06, 0)];
    for (let t = 40; t <= 400; t += 40) events.push(vad.push(0.15, t));
    expect(events).toContain('start');
  });

  it('drops the floor fast when the room turns quiet', () => {
    const vad = new VadSegmenter();
    vad.floor = 0.1;
    vad.push(0.01, 0);
    vad.push(0.01, 40);
    expect(vad.floor).toBeLessThan(0.06);
  });
});
