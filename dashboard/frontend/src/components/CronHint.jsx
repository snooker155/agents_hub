import React, { useEffect, useMemo, useState } from 'react';
import { previewCron } from '../api';
import { useI18n } from '../i18n';

const DEBOUNCE_MS = 350;

/**
 * "Next: Mon 6 Oct 09:00, Tue 7 Oct 09:00, …" under a cron (or hourly/daily/
 * weekly) field, debounced against ``GET /api/plan/cron/preview`` — the same
 * next-fire logic the scheduler runs, so the hint can't say one thing while
 * the job does another. Shows the backend's parse error instead when the
 * expression or timezone is invalid, and renders nothing while the field is
 * empty or the recurrence is 'none'.
 *
 * ``recurrence``: 'cron' | 'hourly' | 'daily' | 'weekly' (default 'cron').
 * ``cron``: the raw expression, used when recurrence === 'cron'.
 * ``start``: the job's first run for hourly/daily/weekly (a Date, ISO string,
 * or the datetime-local input value the job forms already keep in state);
 * the backend steps from it the way the scheduler does.
 */
export default function CronHint({ recurrence = 'cron', cron = '', timezone, start, count = 3, className = '' }) {
  const { t, language } = useI18n();
  const [state, setState] = useState({ loading: false, error: '', times: [] });

  const isCron = recurrence === 'cron';
  // The datetime-local value is browser-local wall time; the backend wants
  // an instant, the same conversion the job forms make on submit.
  const startIso = useMemo(() => {
    if (isCron || !start) return '';
    const d = start instanceof Date ? start : new Date(start);
    return Number.isNaN(d.getTime()) ? '' : d.toISOString();
  }, [isCron, start]);
  // What there is to preview: the expression for cron, the anchor otherwise.
  const subject = recurrence === 'none' ? '' : (isCron ? cron : startIso);

  useEffect(() => {
    // Nothing to preview: the render guard below already hides the hint
    // regardless of whatever state is left over from a previous value, so
    // there is nothing to reset here.
    if (!subject || !subject.trim()) return undefined;
    let cancelled = false;
    // Data-fetch-on-change, not a prop mirrored into state: this flips the
    // hint to "checking…" for the debounce window, then the timeout below
    // resolves it one way or the other.
    setState((prev) => ({ ...prev, loading: true })); // eslint-disable-line react-hooks/set-state-in-effect
    const timer = setTimeout(() => {
      previewCron(isCron ? subject : '', timezone, count, isCron ? undefined : subject, recurrence)
        .then(({ data }) => {
          if (cancelled) return;
          if (!data?.valid) setState({ loading: false, error: data?.error || '', times: [] });
          else setState({ loading: false, error: '', times: data.upcoming_runs_at || [] });
        })
        .catch(() => { if (!cancelled) setState({ loading: false, error: '', times: [] }); });
    }, DEBOUNCE_MS);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [subject, isCron, recurrence, timezone, count]);

  if (!subject || !subject.trim()) return null;

  if (state.error) {
    return <p className={`text-[11px] text-red-500 mt-1 ${className}`} data-testid="cron-hint">{state.error}</p>;
  }
  if (state.times.length === 0) {
    if (!state.loading) return null;
    return <p className={`text-[11px] text-gray-400 mt-1 ${className}`} data-testid="cron-hint">{t('components.cronHint.checking')}</p>;
  }
  const formatted = state.times.map((iso) => {
    try {
      return new Intl.DateTimeFormat(language || undefined, {
        weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
      }).format(new Date(iso));
    } catch {
      return iso;
    }
  }).join(', ');
  return (
    <p className={`text-[11px] text-gray-400 mt-1 ${className}`} data-testid="cron-hint">
      {t('components.cronHint.next', { times: formatted })}
    </p>
  );
}
