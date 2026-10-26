import { describe, it, expect } from 'vitest';
import { parseReasoningContent, tickerText, tidyThought } from '../reasoningText';

describe('tidyThought', () => {
  it('drops blank lines at the start and whitespace at the end', () => {
    expect(tidyThought('\n\n**Searching**\n\nnext\n\n')).toBe('**Searching**\n\nnext');
    expect(tidyThought('  \r\n \n  indented')).toBe('  indented');
    expect(tidyThought(null)).toBe('');
  });

  it('applies to a JSON think payload too', () => {
    expect(parseReasoningContent('{"thought": "\\n\\nПлан"}')).toBe('План');
  });
});

describe('tickerText', () => {
  it('folds blank lines between paragraphs so no shown line is empty', () => {
    expect(tickerText('\n**Plan**\n\nfirst\n \n\nsecond\r\n\r\nthird\n')).toBe('**Plan**\nfirst\nsecond\nthird');
  });
});
