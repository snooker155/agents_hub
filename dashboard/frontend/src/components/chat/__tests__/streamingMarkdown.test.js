import { describe, it, expect } from 'vitest';
import { closeOpenMarkdown } from '../streamingMarkdown';

describe('closeOpenMarkdown', () => {
  it('leaves finished text alone', () => {
    const text = 'A **bold** word, `code` and a [link](https://x.y).\n\n- one\n- two';
    expect(closeOpenMarkdown(text)).toBe(text);
  });

  it('closes bold, strikethrough and italic still being written', () => {
    expect(closeOpenMarkdown('This is **very imp')).toBe('This is **very imp**');
    expect(closeOpenMarkdown('gone ~~old')).toBe('gone ~~old~~');
    expect(closeOpenMarkdown('an *aside')).toBe('an *aside*');
  });

  it('closes the innermost marker first', () => {
    expect(closeOpenMarkdown('**bold *and it')).toBe('**bold *and it***');
  });

  it('drops a marker with nothing after it yet', () => {
    expect(closeOpenMarkdown('Hello\n**')).toBe('Hello\n');
    expect(closeOpenMarkdown('see `')).toBe('see ');
  });

  it('closes an inline code span, and ignores markers inside it', () => {
    expect(closeOpenMarkdown('run `a **b')).toBe('run `a **b`');
  });

  it('shows a link being typed as its text', () => {
    expect(closeOpenMarkdown('see [the docs](https://exa')).toBe('see the docs');
  });

  it('does not read a list bullet as emphasis', () => {
    expect(closeOpenMarkdown('* one\n* two')).toBe('* one\n* two');
  });

  it('does not turn the line above a lone dash into a heading', () => {
    expect(closeOpenMarkdown('Steps:\n-')).toBe('Steps:');
  });

  it('leaves an open code fence to render as a code block', () => {
    const text = 'Here:\n\n```python\nx = **2';
    expect(closeOpenMarkdown(text)).toBe(text);
  });

  it('only looks at the paragraph being written', () => {
    expect(closeOpenMarkdown('a * b\n\nnow **this')).toBe('a * b\n\nnow **this**');
  });
});
