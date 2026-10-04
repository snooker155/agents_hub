import React from 'react';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ previewCron: vi.fn() }));
vi.mock('../../api', () => api);
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k}:${JSON.stringify(vars)}` : k), language: 'en-US' }),
}));

import CronHint from '../CronHint';

const ok = (data) => Promise.resolve({ data });

describe('CronHint', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    api.previewCron.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it('renders nothing for an empty cron', () => {
    render(<CronHint recurrence="cron" cron="" timezone="UTC" />);
    expect(screen.queryByTestId('cron-hint')).toBeNull();
    expect(api.previewCron).not.toHaveBeenCalled();
  });

  it('renders nothing when the recurrence is none', () => {
    render(<CronHint recurrence="none" cron="0 9 * * *" timezone="UTC" />);
    expect(screen.queryByTestId('cron-hint')).toBeNull();
  });

  it('debounces: a keystroke does not call the API before the debounce window elapses', async () => {
    api.previewCron.mockReturnValue(new Promise(() => {}));
    render(<CronHint recurrence="cron" cron="0 9 * * *" timezone="UTC" />);
    await vi.advanceTimersByTimeAsync(100);
    expect(api.previewCron).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(300);
    expect(api.previewCron).toHaveBeenCalledWith('0 9 * * *', 'UTC', 3, undefined, 'cron');
  });

  it('shows the fire times once the preview resolves', async () => {
    api.previewCron.mockReturnValue(ok({
      valid: true, error: null,
      upcoming_runs_at: ['2026-10-05T09:00:00+00:00', '2026-10-06T09:00:00+00:00'],
      description: 'daily at 09:00',
    }));
    render(<CronHint recurrence="cron" cron="0 9 * * *" timezone="UTC" />);
    await vi.advanceTimersByTimeAsync(400);
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByTestId('cron-hint')).toHaveTextContent('components.cronHint.next');
    expect(screen.getByTestId('cron-hint').textContent).toMatch(/Oct 5/);
  });

  it('shows the backend error for an invalid expression instead of a time list', async () => {
    api.previewCron.mockReturnValue(ok({
      valid: false, error: "Invalid cron expression 'nope': bad", upcoming_runs_at: [], description: null,
    }));
    render(<CronHint recurrence="cron" cron="nope" timezone="UTC" />);
    await vi.advanceTimersByTimeAsync(400);
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByTestId('cron-hint')).toHaveTextContent(/Invalid cron expression/);
    expect(api.previewCron).toHaveBeenCalled();
  });

  it('re-debounces on every change instead of piling up calls', async () => {
    api.previewCron.mockReturnValue(new Promise(() => {}));
    const { rerender } = render(<CronHint recurrence="cron" cron="0 9 * * *" timezone="UTC" />);
    await vi.advanceTimersByTimeAsync(100);
    rerender(<CronHint recurrence="cron" cron="0 10 * * *" timezone="UTC" />);
    await vi.advanceTimersByTimeAsync(100);
    expect(api.previewCron).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(300);
    expect(api.previewCron).toHaveBeenCalledTimes(1);
    expect(api.previewCron).toHaveBeenCalledWith('0 10 * * *', 'UTC', 3, undefined, 'cron');
  });
});

describe('CronHint for hourly, daily and weekly', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    api.previewCron.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it('renders nothing until a start time is set', () => {
    render(<CronHint recurrence="daily" start="" timezone="UTC" />);
    expect(screen.queryByTestId('cron-hint')).toBeNull();
    expect(api.previewCron).not.toHaveBeenCalled();
  });

  it('asks the backend with the recurrence and the start instant, not a derived cron', async () => {
    api.previewCron.mockReturnValue(new Promise(() => {}));
    const start = new Date(Date.UTC(2026, 9, 5, 7, 30));
    render(<CronHint recurrence="weekly" start={start} timezone="Europe/Berlin" />);
    await vi.advanceTimersByTimeAsync(400);
    expect(api.previewCron).toHaveBeenCalledWith('', 'Europe/Berlin', 3, '2026-10-05T07:30:00.000Z', 'weekly');
  });
});
