import { describe, it, expect } from 'vitest';
import { translate } from '../../i18n/core';
import { describeRecurrence } from '../recurrenceText';

const say = (lang, job) => describeRecurrence(job, (key, vars) => translate(lang, key, vars), lang);
const cron = (expr, lang = 'en') => say(lang, { recurrence: 'cron', cron: expr });

describe('describeRecurrence', () => {
  it('reads the common cron shapes', () => {
    expect(cron('0 9 * * 1-5')).toBe('Weekdays at 09:00 AM');
    expect(cron('30 8 * * *')).toBe('Every day at 08:30 AM');
    expect(cron('0 9 * * 1,3')).toBe('Every Monday and Wednesday at 09:00 AM');
    expect(cron('*/15 * * * *')).toBe('Every 15 minutes');
    expect(cron('0 * * * *')).toBe('Every hour');
    expect(cron('5 */2 * * *')).toBe('Every 2 hours at :05');
    expect(cron('0 9 1 * *')).toBe('Monthly on day 1 at 09:00 AM');
    expect(cron('*/10 * * * 0,6')).toBe('Every 10 minutes, weekends');
  });

  it('speaks the app language', () => {
    expect(cron('0 9 * * 1-5', 'ru')).toBe('По будням в 09:00');
    expect(cron('0 18 * * 5', 'ru')).toBe('По пятницам в 18:00');
    expect(cron('*/5 * * * *', 'ru')).toBe('Каждые 5 минут');
    expect(cron('0 9 * * 1', 'de')).toBe('Montags um 09:00');
  });

  it('leaves a cron it cannot read to the caller', () => {
    expect(cron('0 9 * 1 *')).toBeNull();
    expect(cron('bogus')).toBeNull();
  });

  it('takes hourly, daily and weekly from the first run', () => {
    const runAt = new Date(2026, 9, 26, 7, 45).toISOString(); // a Monday
    expect(say('ru', { recurrence: 'daily', run_at: runAt })).toBe('Каждый день в 07:45');
    expect(say('ru', { recurrence: 'weekly', run_at: runAt })).toBe('По понедельникам в 07:45');
    expect(say('ru', { recurrence: 'hourly', run_at: runAt })).toBe('Каждый час в :45');
    expect(say('en', { recurrence: 'none', run_at: runAt })).toBeNull();
  });
});
