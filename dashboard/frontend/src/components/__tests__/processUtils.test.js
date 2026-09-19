import { describe, it, expect } from 'vitest';
import { shortText, preview, fmtDurationMs, SKILL_TOOL } from '../processUtils';

describe('shortText', () => {
  it('leaves short text alone', () => {
    expect(shortText('hello')).toBe('hello');
  });

  it('truncates past the default max', () => {
    const out = shortText('a'.repeat(200));
    expect(out).toBe(`${'a'.repeat(180)}...`);
  });

  it('honours a custom max', () => {
    expect(shortText('abcdef', 3)).toBe('abc...');
  });

  it('keeps newlines — it is not the one-line form', () => {
    expect(shortText('a\nb')).toBe('a\nb');
  });

  it('renders empty input as an empty string', () => {
    expect(shortText(null)).toBe('');
    expect(shortText(undefined)).toBe('');
    expect(shortText(0)).toBe('');
  });
});

describe('preview', () => {
  it('collapses every run of whitespace to a single space', () => {
    expect(preview('  a \n\t b   c ')).toBe('a b c');
  });

  it('truncates with an ellipsis, not three dots', () => {
    expect(preview('abcdef', 3)).toBe('abc…');
  });

  it('does not truncate at exactly the max', () => {
    expect(preview('abc', 3)).toBe('abc');
  });

  it('renders empty input as an empty string', () => {
    expect(preview(null)).toBe('');
  });
});

describe('fmtDurationMs', () => {
  it('renders sub-second durations in milliseconds', () => {
    expect(fmtDurationMs(1)).toBe('1ms');
    expect(fmtDurationMs(999)).toBe('999ms');
  });

  it('renders a second or more with two decimals', () => {
    expect(fmtDurationMs(1000)).toBe('1.00s');
    expect(fmtDurationMs(1500)).toBe('1.50s');
    expect(fmtDurationMs(63210)).toBe('63.21s');
  });

  it('renders nothing-at-all as 0ms', () => {
    expect(fmtDurationMs(0)).toBe('0ms');
    expect(fmtDurationMs(null)).toBe('0ms');
    expect(fmtDurationMs(undefined)).toBe('0ms');
  });
});

it('names the skill tool the process view special-cases', () => {
  expect(SKILL_TOOL).toBe('get_skill');
});
