import { describe, it, expect } from 'vitest';
import { parseDateValue, formatDateValue } from '../dateValue';

describe('parseDateValue', () => {
  it('reads a bare calendar date as local midnight, not UTC', () => {
    // The regression this guards: read as UTC, `2026-01-01` renders as
    // Dec 31 for anyone west of Greenwich.
    const d = parseDateValue('2026-01-01');
    expect(d.getFullYear()).toBe(2026);
    expect(d.getMonth()).toBe(0);
    expect(d.getDate()).toBe(1);
    expect(d.getHours()).toBe(0);
    expect(d.getMinutes()).toBe(0);
  });

  it('reads a datetime-local string as local time', () => {
    const d = parseDateValue('2026-09-11T08:30');
    expect(d.getFullYear()).toBe(2026);
    expect(d.getMonth()).toBe(8);
    expect(d.getDate()).toBe(11);
    expect(d.getHours()).toBe(8);
    expect(d.getMinutes()).toBe(30);
  });

  it('accepts a space instead of the T separator', () => {
    expect(parseDateValue('2026-09-11 08:30').getHours()).toBe(8);
  });

  it('honours an explicit UTC marker instead of reading it as local', () => {
    expect(parseDateValue('2026-09-11T00:00:00Z').getTime())
      .toBe(Date.UTC(2026, 8, 11, 0, 0, 0));
  });

  it('honours an explicit numeric offset', () => {
    expect(parseDateValue('2026-09-11T10:00:00+02:00').getTime())
      .toBe(Date.UTC(2026, 8, 11, 8, 0, 0));
  });

  it('passes a valid Date through unchanged', () => {
    const d = new Date(2026, 8, 11);
    expect(parseDateValue(d)).toBe(d);
  });

  it('returns null for empty, invalid and unparseable input', () => {
    expect(parseDateValue('')).toBeNull();
    expect(parseDateValue(null)).toBeNull();
    expect(parseDateValue(undefined)).toBeNull();
    expect(parseDateValue('not a date')).toBeNull();
    expect(parseDateValue(new Date('nonsense'))).toBeNull();
  });
});

describe('formatDateValue', () => {
  const d = new Date(2026, 8, 5, 7, 4); // deliberately single-digit month/day/time

  it('writes the calendar shape by default, zero-padded', () => {
    expect(formatDateValue(d)).toBe('2026-09-05');
    expect(formatDateValue(d, 'date')).toBe('2026-09-05');
  });

  it('writes the datetime-local shape', () => {
    expect(formatDateValue(d, 'local')).toBe('2026-09-05T07:04');
  });

  it('writes full ISO in UTC', () => {
    expect(formatDateValue(d, 'iso')).toBe(d.toISOString());
  });

  it('returns an empty string for no date', () => {
    expect(formatDateValue(null, 'iso')).toBe('');
    expect(formatDateValue(undefined)).toBe('');
  });
});

describe('round trips', () => {
  it.each(['2026-01-01', '2026-06-15', '2026-12-31'])('keeps the day for %s', (value) => {
    expect(formatDateValue(parseDateValue(value), 'date')).toBe(value);
  });

  it('keeps date and time for a local value', () => {
    // Not a DST changeover date — 02:30 exists in every zone that day.
    const value = '2026-03-10T02:30';
    expect(formatDateValue(parseDateValue(value), 'local')).toBe(value);
  });

  it('narrows a local value to its calendar day', () => {
    expect(formatDateValue(parseDateValue('2026-09-11T23:59'), 'date')).toBe('2026-09-11');
  });
});
