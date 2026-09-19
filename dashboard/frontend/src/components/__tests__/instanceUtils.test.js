import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { KIND_ICONS, STATE_STYLES, formatDuration, relativeTime } from '../instanceUtils';

describe('formatDuration', () => {
  it('renders seconds under a minute', () => {
    expect(formatDuration(5000)).toBe('5s');
    expect(formatDuration(59_400)).toBe('59s');
  });

  it('renders minutes and seconds under an hour', () => {
    expect(formatDuration(65_000)).toBe('1m 5s');
    expect(formatDuration(3_599_000)).toBe('59m 59s');
  });

  it('renders hours and minutes beyond that', () => {
    expect(formatDuration(3_725_000)).toBe('1h 2m');
    expect(formatDuration(90_000_000)).toBe('25h 0m');
  });

  it('renders a missing duration as a dash', () => {
    expect(formatDuration(0)).toBe('—');
    expect(formatDuration(null)).toBe('—');
    expect(formatDuration(undefined)).toBe('—');
  });
});

describe('relativeTime', () => {
  // Echo the key and count so the test asserts on the choice, not the wording.
  const t = (key, vars) => (vars?.count == null ? key : `${key}:${vars.count}`);
  const NOW = new Date('2026-09-11T12:00:00Z');

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  const ago = (ms) => relativeTime(new Date(NOW.getTime() - ms).toISOString(), t);

  it('calls anything under a minute "just now"', () => {
    expect(ago(0)).toBe('instances.time.justNow');
    expect(ago(59_000)).toBe('instances.time.justNow');
  });

  it('switches to minutes at a minute, rounding down', () => {
    expect(ago(60_000)).toBe('instances.time.minutes:1');
    expect(ago(119_000)).toBe('instances.time.minutes:1');
    expect(ago(45 * 60_000)).toBe('instances.time.minutes:45');
  });

  it('switches to hours at an hour', () => {
    expect(ago(60 * 60_000)).toBe('instances.time.hours:1');
    expect(ago(23 * 60 * 60_000)).toBe('instances.time.hours:23');
  });

  it('switches to days at a day', () => {
    expect(ago(24 * 60 * 60_000)).toBe('instances.time.days:1');
    expect(ago(9 * 24 * 60 * 60_000)).toBe('instances.time.days:9');
  });

  it('shows a clock-skewed future timestamp as "just now", never as a negative age', () => {
    expect(relativeTime(new Date(NOW.getTime() + 60_000).toISOString(), t))
      .toBe('instances.time.justNow');
  });

  it('renders a missing or unparseable timestamp as a dash', () => {
    expect(relativeTime(null, t)).toBe('—');
    expect(relativeTime('', t)).toBe('—');
    expect(relativeTime('not a date', t)).toBe('—');
  });
});

describe('shared vocabulary', () => {
  it('styles every instance state the backend can report', () => {
    ['starting', 'active', 'standby', 'finished', 'stopped', 'failed'].forEach((state) => {
      expect(STATE_STYLES[state]).toMatchObject({
        dot: expect.any(String),
        badge: expect.any(String),
      });
    });
  });

  it('gives every instance kind an icon', () => {
    ['node', 'container', 'task', 'chat', 'flow_node', 'team_member'].forEach((kind) => {
      expect(KIND_ICONS[kind]).toBeTruthy();
    });
  });
});
