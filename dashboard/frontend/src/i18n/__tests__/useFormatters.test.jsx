import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useFormatters } from '../core';
import I18nProvider from '../I18nProvider';

const formatters = () => renderHook(() => useFormatters(), { wrapper: I18nProvider }).result.current;

describe('useFormatters', () => {
  it('formats a date in the active locale', () => {
    const { formatDate } = formatters();
    const out = formatDate('2026-09-11T08:30:00Z', { timeZone: 'UTC', dateStyle: 'medium' });
    // jsdom reports en-US, the default language.
    expect(out).toBe(new Date('2026-09-11T08:30:00Z')
      .toLocaleString('en', { timeZone: 'UTC', dateStyle: 'medium' }));
  });

  it('accepts a Date as well as a string', () => {
    const { formatDate } = formatters();
    const d = new Date('2026-09-11T08:30:00Z');
    expect(formatDate(d, { timeZone: 'UTC' })).toBe(formatDate(d.toISOString(), { timeZone: 'UTC' }));
  });

  it('shows an unparseable date as given instead of "Invalid Date"', () => {
    expect(formatters().formatDate('whenever')).toBe('whenever');
  });

  it('renders a missing date as an empty string', () => {
    const { formatDate } = formatters();
    expect(formatDate(null)).toBe('');
    expect(formatDate('')).toBe('');
  });

  it('groups numbers in the active locale', () => {
    const { formatNumber } = formatters();
    expect(formatNumber(1234567)).toBe((1234567).toLocaleString('en'));
    expect(formatNumber('1234.5', { maximumFractionDigits: 1 }))
      .toBe((1234.5).toLocaleString('en', { maximumFractionDigits: 1 }));
  });

  it('keeps zero rather than blanking it', () => {
    expect(formatters().formatNumber(0)).toBe('0');
  });

  it('renders a missing number as an empty string and a non-number as itself', () => {
    const { formatNumber } = formatters();
    expect(formatNumber(null)).toBe('');
    expect(formatNumber('')).toBe('');
    expect(formatNumber('n/a')).toBe('n/a');
  });
});
