import { describe, expect, it } from 'vitest';
import {
  findWake, isEndCommand, isStopCommand, normalizeSpeech, wakePhrases,
} from '../voiceCommands';
import { VadSegmenter } from '../vad';
import { DEFAULT_PREFS, PREFS_KEY, earPhase, earTuning, readPrefs } from '../assistantState';

const defaults = wakePhrases('');

describe('a spoken stop', () => {
  it.each([
    'Стоп', 'стоп!', 'Стоп, стоп, стоп', 'Хватит.', 'Остановись, пожалуйста', 'замолчи',
    'Stop.', 'stop it', 'OK stop', 'enough', 'be quiet please',
    'Stopp!', 'Hör auf', 'genug', 'Ассистент, стоп', 'Assistant, stop', 'ok assistant stop',
  ])('"%s" stops', (said) => {
    expect(isStopCommand(said, defaults)).toBe(true);
  });

  it.each([
    'stop the nightly job', 'остановись на втором файле и покажи его', 'не останавливайся',
    'what does stop mean', '', 'yes', 'нет',
  ])('"%s" is not a stop', (said) => {
    expect(isStopCommand(said, defaults)).toBe(false);
  });
});

describe('the end of a conversation', () => {
  it('is a short goodbye, in any language', () => {
    for (const said of ['Пока!', 'до свидания', 'Конец разговора', 'Goodbye.', 'stop listening', 'Tschüss']) {
      expect(isEndCommand(said)).toBe(true);
    }
    expect(isEndCommand('пока не запускай задачу')).toBe(false);
    expect(isEndCommand('stop')).toBe(false);
  });
});

describe('the wake phrase', () => {
  it('opens the utterance and keeps what follows in the words heard', () => {
    expect(findWake('Ассистент, покажи задачи на сегодня.', defaults)).toEqual({ rest: 'покажи задачи на сегодня.' });
    expect(findWake('Hey Assistant what failed today?', defaults)).toEqual({ rest: 'what failed today?' });
    expect(findWake('Assistent: was kostet das?', defaults)).toEqual({ rest: 'was kostet das?' });
    expect(findWake('ассистент', defaults)).toEqual({ rest: '' });
  });

  it('forgives a recogniser slip in a long word', () => {
    expect(findWake('ассистенты покажи', defaults)).toEqual({ rest: 'покажи' });
    expect(findWake('Assistance, open the costs', defaults)).toEqual({ rest: 'open the costs' });
  });

  it('is not a word in the middle of a sentence', () => {
    expect(findWake('I asked the assistant yesterday', defaults)).toBeNull();
    expect(findWake('покажи задачи', defaults)).toBeNull();
    expect(findWake('', defaults)).toBeNull();
  });

  it('can be the person\'s own, of several words', () => {
    const own = wakePhrases('Привет, Хаб');
    expect(own).toEqual(['привет хаб']);
    expect(findWake('Привет хаб, что нового?', own)).toEqual({ rest: 'что нового?' });
    expect(findWake('Ассистент, что нового?', own)).toBeNull();
  });

  it('is compared without case, ё or punctuation', () => {
    expect(normalizeSpeech('  Ещё, РАЗ!  ')).toBe('еще раз');
  });
});

describe('the voice activity segmenter', () => {
  const feed = (vad, levels, step = 50, from = 0) => levels.map((level, i) => vad.push(level, from + i * step));

  it('starts after a short run above the floor and ends after the silence', () => {
    const vad = new VadSegmenter({ silenceMs: 300, startMs: 100 });
    const quiet = Array(10).fill(0.01);
    const voice = Array(10).fill(0.4);
    const events = feed(vad, [...quiet, ...voice, ...Array(10).fill(0.01)]).filter(Boolean);
    expect(events).toEqual(['start', 'end']);
  });

  it('takes a click for nothing', () => {
    const vad = new VadSegmenter({ startMs: 120 });
    expect(feed(vad, [0.01, 0.01, 0.9, 0.01, 0.01, 0.01]).filter(Boolean)).toEqual([]);
  });

  it('follows a noisy room and asks more of a voice while the assistant speaks', () => {
    const vad = new VadSegmenter();
    feed(vad, Array(100).fill(0.05));
    expect(vad.threshold).toBeGreaterThan(0.12);
    const before = vad.threshold;
    vad.sensitivity = 2;
    expect(vad.threshold).toBeCloseTo(before * 2);
  });
});

describe('what the open microphone is for', () => {
  it('only listens for a stop while a turn runs or the voice speaks, in every mode', () => {
    for (const listen of ['hold', 'conversation', 'wake']) {
      expect(earPhase({ busy: true, listen, conversing: true, awake: true })).toBe('monitor');
      expect(earPhase({ speaking: true, listen })).toBe('monitor');
    }
  });

  it('takes a question in a conversation or once called, else waits for the name', () => {
    expect(earPhase({ listen: 'conversation', conversing: true })).toBe('command');
    expect(earPhase({ listen: 'conversation', conversing: false })).toBe('off');
    expect(earPhase({ listen: 'wake', awake: false })).toBe('wake');
    expect(earPhase({ listen: 'wake', awake: true })).toBe('command');
    expect(earPhase({ listen: 'hold' })).toBe('off');
  });

  it('throws long stretches away while it works, and cuts a long question', () => {
    expect(earTuning('monitor', { speaking: true })).toMatchObject({ overflow: 'drop', maxSeconds: 3, sensitivity: 2 });
    expect(earTuning('wake')).toMatchObject({ overflow: 'drop', maxSeconds: 10 });
    expect(earTuning('command', { maxSeconds: 120 })).toMatchObject({ overflow: 'cut', maxSeconds: 60 });
  });

  it('remembers the mode, and an unknown one falls back to holding', () => {
    const store = new Map();
    const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
    expect(readPrefs(storage).listen).toBe(DEFAULT_PREFS.listen);
    storage.setItem(PREFS_KEY, JSON.stringify({ listen: 'wake', wakePhrase: 'Хаб' }));
    expect(readPrefs(storage)).toMatchObject({ listen: 'wake', wakePhrase: 'Хаб', voiceStop: true });
    storage.setItem(PREFS_KEY, JSON.stringify({ listen: 'always' }));
    expect(readPrefs(storage).listen).toBe('hold');
  });
});
