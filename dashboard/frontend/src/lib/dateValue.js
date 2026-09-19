/**
 * Conversions between the string shapes our date fields store and Date objects.
 *
 * Shapes ("valueFormat"):
 *   'date'  — YYYY-MM-DD            (what <input type="date"> used to hand back)
 *   'local' — YYYY-MM-DDTHH:mm      (what <input type="datetime-local"> used to)
 *   'iso'   — full ISO string in UTC
 */

const pad = (n) => String(n).padStart(2, '0');

/** `2026-09-11`, `2026-09-11T08:30` or a full ISO string → a local Date. */
export function parseDateValue(value) {
  if (!value) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  const s = String(value).trim();
  // A bare calendar value carries no zone — read it as local time, not UTC,
  // so a date does not slip a day in western timezones.
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
  if (m && !/([zZ]|[+-]\d{2}:?\d{2})$/.test(s)) {
    return new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0));
  }
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Date → the string shape the caller stores. */
export function formatDateValue(date, valueFormat) {
  if (!date) return '';
  if (valueFormat === 'iso') return date.toISOString();
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  return valueFormat === 'local' ? `${day}T${pad(date.getHours())}:${pad(date.getMinutes())}` : day;
}
