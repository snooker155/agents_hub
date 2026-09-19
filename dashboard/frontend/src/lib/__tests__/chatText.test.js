import { describe, it, expect } from 'vitest';
import { trimBubbleText } from '../chatText';

describe('trimBubbleText', () => {
  it('drops the trailing newline that rendered as a blank row in the bubble', () => {
    // The regression this guards: under `whitespace-pre-wrap` a trailing "\n"
    // is a rendered empty line, so every reply grew an empty row at the bottom.
    expect(trimBubbleText('Готово, задача создана.\n')).toBe('Готово, задача создана.');
    expect(trimBubbleText('Готово.\n\n\n')).toBe('Готово.');
    expect(trimBubbleText('Готово.   \n  \n')).toBe('Готово.');
  });

  it('keeps a leading newline — indentation the model meant is not ours to reflow', () => {
    expect(trimBubbleText('\n  indented start')).toBe('\n  indented start');
  });

  it('keeps blank lines between paragraphs', () => {
    expect(trimBubbleText('Первый абзац.\n\nВторой абзац.\n'))
      .toBe('Первый абзац.\n\nВторой абзац.');
  });

  it('is a no-op on text that already ends cleanly', () => {
    expect(trimBubbleText('Готово.')).toBe('Готово.');
  });

  it('renders nullish content as the empty string rather than "undefined"', () => {
    expect(trimBubbleText(undefined)).toBe('');
    expect(trimBubbleText(null)).toBe('');
  });
});
