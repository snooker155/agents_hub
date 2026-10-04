import { describe, expect, it } from 'vitest';
import { SentenceStream, firstParagraphSentences, isSpeakable, plainSpeech, sentenceEnd } from '../sentences';

const feed = (chunks) => {
  const s = new SentenceStream({ minChars: 1 });
  const out = [];
  for (const c of chunks) out.push(...s.push(c));
  out.push(...s.end());
  return out;
};

describe('cutting an answer into sentences', () => {
  it('hands out each sentence as soon as the next character shows it ended', () => {
    const s = new SentenceStream({ minChars: 1 });
    expect(s.push('Your task is created')).toEqual([]);
    expect(s.push('.')).toEqual([]);              // "." might still be "3.5"
    expect(s.push(' It runs')).toEqual(['Your task is created.']);
    expect(s.push(' tonight! More')).toEqual(['It runs tonight!']);
    expect(s.end()).toEqual(['More']);
  });

  it('keeps numbers, versions and addresses whole', () => {
    expect(feed(['It costs $3.50 and uses v1.2 of example.com today. Next one.']))
      .toEqual(['It costs $3.50 and uses v1.2 of example.com today.', 'Next one.']);
  });

  it('keeps abbreviations in English, Russian and German whole', () => {
    expect(feed(['Use a tool, e.g. the researcher. Then stop.']))
      .toEqual(['Use a tool, e.g. the researcher.', 'Then stop.']);
    expect(feed(['Это задачи, т. е. работа на потом. Готово.']))
      .toEqual(['Это задачи, т. е. работа на потом.', 'Готово.']);
    expect(feed(['Отчёт за 2025 г. готов. Смотрите ниже.']))
      .toEqual(['Отчёт за 2025 г. готов.', 'Смотрите ниже.']);
    expect(feed(['Ein Agent, z. B. der Forscher. Fertig.']))
      .toEqual(['Ein Agent, z. B. der Forscher.', 'Fertig.']);
    expect(feed(['Dr. Smith agreed. Done.'])).toEqual(['Dr. Smith agreed.', 'Done.']);
  });

  it('speaks only the first paragraph', () => {
    expect(feed(['Two tasks wait. Both are yours.\n\n- one\n- two\n\nMore text.']))
      .toEqual(['Two tasks wait.', 'Both are yours.']);
    expect(firstParagraphSentences('\n\nDone.\n\n| a | b |')).toEqual(['Done.']);
  });

  it('joins a very short sentence to the next one', () => {
    const s = new SentenceStream();
    expect(s.push('Ok. The researcher has started. ')).toEqual(['Ok. The researcher has started.']);
  });

  it('does not end a sentence inside a list number', () => {
    expect(sentenceEnd('1. First step')).toBe(-1);
  });

  it('leaves code and tables to the screen', () => {
    expect(isSpeakable('```python')).toBe(false);
    expect(isSpeakable('| a | b |')).toBe(false);
    expect(isSpeakable('Done.')).toBe(true);
    expect(plainSpeech('**Done.** See [the task](/tasks/1) and `x`.')).toBe('Done. See the task and.');
  });
});
