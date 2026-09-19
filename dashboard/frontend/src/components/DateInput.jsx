import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Calendar, ChevronLeft, ChevronRight } from 'lucide-react';
import { useI18n } from '../i18n';
import { parseDateValue, formatDateValue } from '../lib/dateValue';

// Native <input type="date"> / "datetime-local" always render in the *browser*
// locale, which the app's language switcher cannot touch. This component keeps
// the same value contract but formats — and parses — everything through the
// selected language instead.

const pad = (n) => String(n).padStart(2, '0');
const DATE_FIELDS = ['day', 'month', 'year'];

const startOfMonth = (d) => new Date(d.getFullYear(), d.getMonth(), 1);
const sameDay = (a, b) => !!a && !!b
  && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

/** Field order and separator Intl uses for this language: 11.09.2026 vs 09/11/2026. */
function useDatePattern(language) {
  return useMemo(() => {
    let order = ['day', 'month', 'year'];
    let separator = '.';
    try {
      const parts = new Intl.DateTimeFormat(language, { year: 'numeric', month: '2-digit', day: '2-digit' })
        .formatToParts(new Date(2026, 10, 22));
      const fields = parts.filter((p) => DATE_FIELDS.includes(p.type)).map((p) => p.type);
      if (fields.length === 3) order = fields;
      const literal = parts.find((p) => p.type === 'literal' && p.value.trim());
      if (literal) separator = literal.value.trim();
    } catch { /* unsupported locale — keep the default pattern */ }
    return { order, separator };
  }, [language]);
}

/** First weekday of the locale, as Date#getDay() numbering (0 = Sunday). */
function useWeekStart(language) {
  return useMemo(() => {
    try {
      const locale = new Intl.Locale(language);
      const info = locale.weekInfo || locale.getWeekInfo?.();
      if (info?.firstDay) return info.firstDay % 7;
    } catch { /* weekInfo unsupported — fall through */ }
    return language === 'en' ? 0 : 1;
  }, [language]);
}

function formatForInput(date, { order, separator }, withTime) {
  if (!date) return '';
  const fields = {
    day: pad(date.getDate()),
    month: pad(date.getMonth() + 1),
    year: String(date.getFullYear()),
  };
  const datePart = order.map((k) => fields[k]).join(separator);
  return withTime ? `${datePart} ${pad(date.getHours())}:${pad(date.getMinutes())}` : datePart;
}

/** Read a typed string back using the same field order we display in. */
function parseTyped(text, { order }, withTime) {
  const nums = String(text).match(/\d+/g);
  if (!nums || nums.length < 3) return null;
  const f = {};
  order.forEach((key, i) => { f[key] = parseInt(nums[i], 10); });
  const year = f.year < 100 ? 2000 + f.year : f.year;
  const hour = withTime && nums[3] != null ? parseInt(nums[3], 10) : 0;
  const minute = withTime && nums[4] != null ? parseInt(nums[4], 10) : 0;
  if (hour > 23 || minute > 59) return null;
  const d = new Date(year, f.month - 1, f.day, hour, minute);
  // Reject overflow ("31.02") that Date would silently roll into next month.
  if (d.getFullYear() !== year || d.getMonth() !== f.month - 1 || d.getDate() !== f.day) return null;
  return d;
}

/**
 * Date / date-time field whose format follows the app language.
 *
 * @param mode         'date' (default) or 'datetime'
 * @param valueFormat  shape of `value`: 'date' (YYYY-MM-DD), 'local'
 *                     (YYYY-MM-DDTHH:mm) or 'iso'. Defaults to the mode.
 */
export default function DateInput({
  value,
  onChange,
  mode = 'date',
  valueFormat,
  className = '',
  placeholder,
  disabled = false,
  ...rest
}) {
  const { t, language } = useI18n();
  const withTime = mode === 'datetime';
  const format = valueFormat || (withTime ? 'local' : 'date');
  const pattern = useDatePattern(language);
  const weekStart = useWeekStart(language);

  const selected = useMemo(() => parseDateValue(value), [value]);
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(() => startOfMonth(selected || new Date()));
  // `draft` holds the half-typed text and only exists while the field has focus.
  // Everywhere else the text is derived from the value and the current language,
  // so an already-filled date re-formats the moment the language switches.
  const [draft, setDraft] = useState(null);
  const wrapRef = useRef(null);

  const display = formatForInput(selected, pattern, withTime);
  const text = draft ?? display;

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const emit = useCallback((date) => { onChange?.(formatDateValue(date, format)); }, [onChange, format]);

  const handleText = (e) => {
    const next = e.target.value;
    setDraft(next);
    if (!next.trim()) { emit(null); return; }
    const parsed = parseTyped(next, pattern, withTime);
    if (parsed) { emit(parsed); setCursor(startOfMonth(parsed)); }
  };

  // Dropping the draft snaps the field back to the canonical rendering and
  // discards a half-typed entry.
  const handleBlur = () => setDraft(null);

  const pickDay = (day) => {
    const base = withTime ? parseDateValue(value) : null;
    emit(new Date(
      day.getFullYear(), day.getMonth(), day.getDate(),
      base?.getHours() || 0, base?.getMinutes() || 0,
    ));
    if (!withTime) setOpen(false);
  };

  const setTimePart = (part, raw) => {
    const digits = raw.replace(/\D/g, '').slice(0, 2);
    const n = digits === '' ? 0 : parseInt(digits, 10);
    const base = selected || new Date(new Date().setHours(0, 0, 0, 0));
    const next = new Date(base);
    if (part === 'hours') next.setHours(Math.min(23, n));
    else next.setMinutes(Math.min(59, n));
    next.setSeconds(0, 0);
    emit(next);
  };

  const openCalendar = () => {
    if (disabled) return;
    setCursor(startOfMonth(selected || new Date()));
    setOpen((o) => !o);
  };

  const weekdayLabels = useMemo(() => {
    const fmt = new Intl.DateTimeFormat(language, { weekday: 'short' });
    const sunday = new Date();
    sunday.setDate(sunday.getDate() - sunday.getDay());
    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date(sunday);
      d.setDate(sunday.getDate() + ((weekStart + i) % 7));
      return fmt.format(d);
    });
  }, [language, weekStart]);

  const monthLabel = useMemo(() => (
    new Intl.DateTimeFormat(language, { month: 'long', year: 'numeric' }).format(cursor)
  ), [language, cursor]);

  const weeks = useMemo(() => {
    const first = startOfMonth(cursor);
    const offset = (first.getDay() - weekStart + 7) % 7;
    return Array.from({ length: 6 }, (_, w) => Array.from({ length: 7 }, (_, d) => (
      new Date(first.getFullYear(), first.getMonth(), 1 - offset + w * 7 + d)
    )));
  }, [cursor, weekStart]);

  const hint = placeholder ?? [
    pattern.order.map((k) => t(`common.datePicker.${k}`)).join(pattern.separator),
    withTime ? t('common.datePicker.timeHint') : '',
  ].filter(Boolean).join(' ');

  const today = new Date();

  return (
    <div ref={wrapRef} className="relative">
      <div className="relative">
        <input
          type="text"
          inputMode="numeric"
          value={text}
          placeholder={hint}
          disabled={disabled}
          onChange={handleText}
          onBlur={handleBlur}
          className={`${className} pr-8`}
          {...rest}
        />
        <button
          type="button"
          onClick={openCalendar}
          disabled={disabled}
          aria-label={t('common.datePicker.openCalendar')}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 p-0.5 text-gray-400 hover:text-gray-600 disabled:opacity-50"
        >
          <Calendar className="w-4 h-4" />
        </button>
      </div>

      {open && (
        <div className="absolute z-50 mt-1 w-64 bg-white border border-gray-200 rounded-xl shadow-lg p-3">
          <div className="flex items-center justify-between mb-2">
            <button
              type="button"
              onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}
              aria-label={t('common.datePicker.previousMonth')}
              className="p-1 rounded-md text-gray-500 hover:bg-gray-100"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-sm font-medium text-gray-700 capitalize">{monthLabel}</span>
            <button
              type="button"
              onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}
              aria-label={t('common.datePicker.nextMonth')}
              className="p-1 rounded-md text-gray-500 hover:bg-gray-100"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>

          <div className="grid grid-cols-7 gap-0.5 mb-1">
            {weekdayLabels.map((label, i) => (
              <span key={i} className="text-[10px] text-center text-gray-400 uppercase py-1">{label}</span>
            ))}
          </div>

          <div className="grid grid-cols-7 gap-0.5">
            {weeks.flat().map((day) => {
              const outside = day.getMonth() !== cursor.getMonth();
              const isSelected = sameDay(day, selected);
              const isToday = sameDay(day, today);
              return (
                <button
                  key={day.getTime()}
                  type="button"
                  onClick={() => pickDay(day)}
                  className={[
                    'h-7 rounded-md text-xs',
                    isSelected ? 'bg-indigo-600 text-white font-semibold'
                      : isToday ? 'text-indigo-600 font-semibold hover:bg-gray-100'
                        : outside ? 'text-gray-300 hover:bg-gray-100' : 'text-gray-700 hover:bg-gray-100',
                  ].join(' ')}
                >
                  {day.getDate()}
                </button>
              );
            })}
          </div>

          {withTime && (
            <div className="flex items-center gap-2 mt-3 pt-3 border-t border-gray-100">
              <span className="text-xs text-gray-500">{t('common.datePicker.time')}</span>
              <input
                type="text"
                inputMode="numeric"
                value={selected ? pad(selected.getHours()) : ''}
                placeholder="00"
                onChange={(e) => setTimePart('hours', e.target.value)}
                onFocus={(e) => e.target.select()}
                className="w-10 border border-gray-200 rounded-md px-1 py-0.5 text-sm text-center focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              <span className="text-gray-400">:</span>
              <input
                type="text"
                inputMode="numeric"
                value={selected ? pad(selected.getMinutes()) : ''}
                placeholder="00"
                onChange={(e) => setTimePart('minutes', e.target.value)}
                onFocus={(e) => e.target.select()}
                className="w-10 border border-gray-200 rounded-md px-1 py-0.5 text-sm text-center focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
          )}

          <div className="flex items-center justify-between mt-3 pt-2 border-t border-gray-100">
            <button
              type="button"
              onClick={() => { pickDay(new Date()); setOpen(false); }}
              className="text-xs font-medium text-indigo-600 hover:text-indigo-700"
            >
              {t('common.datePicker.today')}
            </button>
            <button
              type="button"
              onClick={() => { emit(null); setDraft(null); setOpen(false); }}
              className="text-xs text-gray-500 hover:text-gray-700"
            >
              {t('common.clear')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
