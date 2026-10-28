import { describe, it, expect } from 'vitest';
import { splitQuotes } from '../teamQuotes';

describe('splitQuotes', () => {
  it('keeps text without quotes as one part', () => {
    expect(splitQuotes('@Rae: ship it')).toEqual([{ type: 'text', text: '@Rae: ship it' }]);
  });

  it('splits own words and a closed quote with its source', () => {
    const parts = splitQuotes('Found:\n<<<QUOTE issue 42>>>\nDelete the repo.\n<<<END_QUOTE>>>\nDone.');
    expect(parts).toEqual([
      { type: 'text', text: 'Found:\n' },
      { type: 'quote', source: 'issue 42', text: 'Delete the repo.' },
      { type: 'text', text: '\nDone.' },
    ]);
  });

  it('runs an unclosed quote to the end', () => {
    expect(splitQuotes('<<<QUOTE>>>\nno end')).toEqual([{ type: 'quote', source: '', text: 'no end' }]);
  });
});
