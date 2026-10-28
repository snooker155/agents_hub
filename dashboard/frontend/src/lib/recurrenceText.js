// A job's repeat in words: "Weekdays at 09:00" instead of "0 9 * * 1-5".
// Hourly / daily / weekly step from the job's first run, so their time and
// weekday come from `run_at`; a cron expression is read field by field. A
// cron this does not cover (a month field, an hour step with an offset…)
// gives null and the caller shows the expression itself.

const DAY_NAMES = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };

const capFirst = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
const lowerFirst = (s) => (s ? s.charAt(0).toLowerCase() + s.slice(1) : s);
const pad = (n) => String(n).padStart(2, '0');

function formatTime(hour, minute, language) {
  try {
    return new Intl.DateTimeFormat(language || undefined, { hour: '2-digit', minute: '2-digit' })
      .format(new Date(2000, 0, 1, hour, minute));
  } catch {
    return `${pad(hour)}:${pad(minute)}`;
  }
}

/** One cron field as a sorted list of numbers, or null for "*" or a step. */
function expand(field, min, max, names) {
  if (field === '*' || field.includes('/')) return null;
  const out = new Set();
  for (const part of field.split(',')) {
    const [a, b] = part.split('-').map((x) => {
      const key = x.toLowerCase();
      if (names && key in names) return names[key];
      return /^\d+$/.test(x) ? parseInt(x, 10) : NaN;
    });
    const end = b === undefined ? a : b;
    if (Number.isNaN(a) || Number.isNaN(end) || a < min || end > max || a > end) return undefined;
    for (let i = a; i <= end; i += 1) out.add(i);
  }
  return [...out].sort((x, y) => x - y);
}

/** "*\/N" → N, anything else → null. */
function step(field) {
  const m = /^\*\/(\d+)$/.exec(field);
  return m ? parseInt(m[1], 10) : null;
}

function joinList(items, t) {
  if (items.length <= 1) return items.join('');
  return `${items.slice(0, -1).join(', ')} ${t('components.recurrence.and')} ${items[items.length - 1]}`;
}

function dayPhrase(days, t) {
  const key = days.join(',');
  if (key === '1,2,3,4,5') return t('components.recurrence.weekdays');
  if (key === '0,6') return t('components.recurrence.weekends');
  const names = days.map((d) => t(`components.recurrence.days.${d}`));
  return capFirst(t('components.recurrence.onDays', { days: joinList(names, t) }));
}

/** The day part of a cron: null for every day, a phrase, or undefined when unreadable. */
function cronDays(dom, month, dow, t) {
  if (month !== '*') return undefined;
  if (dom === '*' && dow === '*') return null;
  if (dom !== '*' && dow !== '*') return undefined;
  if (dow !== '*') {
    const days = expand(dow, 0, 7, DAY_NAMES);
    if (!days) return undefined;
    return dayPhrase([...new Set(days.map((d) => d % 7))].sort((a, b) => a - b), t);
  }
  const dates = expand(dom, 1, 31);
  if (!dates) return undefined;
  return t('components.recurrence.monthly', { days: dates.join(', ') });
}

function describeCron(expr, t, language) {
  const fields = String(expr || '').trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [min, hour, dom, month, dow] = fields;
  const day = cronDays(dom, month, dow, t);
  if (day === undefined) return null;
  const withDay = (phrase) => (day ? `${phrase}, ${lowerFirst(day)}` : phrase);

  // Several times a day: every N minutes, every hour, every N hours.
  const minuteStep = step(min);
  if (min === '*' && hour === '*') return withDay(t('components.recurrence.everyMinute'));
  if (minuteStep && hour === '*') return withDay(t('components.recurrence.everyMinutes', { count: minuteStep }));
  const minutes = expand(min, 0, 59);
  if (!minutes || minutes.length !== 1) return null;
  const minute = minutes[0];
  if (hour === '*') {
    return withDay(minute === 0 ? t('components.recurrence.everyHour')
      : t('components.recurrence.everyHourAt', { minute: pad(minute) }));
  }
  const hourStep = step(hour);
  if (hourStep) {
    return withDay(minute === 0 ? t('components.recurrence.everyHours', { count: hourStep })
      : t('components.recurrence.everyHoursAt', { count: hourStep, minute: pad(minute) }));
  }
  const range = /^(\d+)-(\d+)$/.exec(hour);
  if (range && Number(range[1]) < Number(range[2]) && Number(range[2]) <= 23) {
    return withDay(t('components.recurrence.hourlyBetween', {
      from: formatTime(Number(range[1]), minute, language),
      to: formatTime(Number(range[2]), minute, language),
    }));
  }
  // At given times of the day.
  const hours = expand(hour, 0, 23);
  if (!hours) return null;
  const time = joinList(hours.map((h) => formatTime(h, minute, language)), t);
  return t('components.recurrence.at', { day: day || t('components.recurrence.everyDay'), time });
}

/**
 * The repeat of a scheduled job in words, or null when it does not repeat or
 * its cron is beyond what this reads. `job`: { recurrence, cron, run_at }.
 */
export function describeRecurrence(job, t, language) {
  const recurrence = job?.recurrence || 'none';
  if (recurrence === 'none') return null;
  if (recurrence === 'cron') return describeCron(job.cron, t, language);
  const start = job.run_at ? new Date(job.run_at) : null;
  const valid = start && !Number.isNaN(start.getTime());
  if (recurrence === 'hourly') {
    if (!valid || start.getMinutes() === 0) return t('components.recurrence.everyHour');
    return t('components.recurrence.everyHourAt', { minute: pad(start.getMinutes()) });
  }
  if (!valid) {
    return recurrence === 'daily' ? t('components.recurrence.everyDay')
      : recurrence === 'weekly' ? t('components.recurrence.everyWeek') : null;
  }
  const time = formatTime(start.getHours(), start.getMinutes(), language);
  const day = recurrence === 'weekly' ? dayPhrase([start.getDay()], t)
    : recurrence === 'daily' ? t('components.recurrence.everyDay') : null;
  return day ? t('components.recurrence.at', { day, time }) : null;
}

export const RECURRENCE_OPTIONS = ['none', 'hourly', 'daily', 'weekly', 'cron'];
