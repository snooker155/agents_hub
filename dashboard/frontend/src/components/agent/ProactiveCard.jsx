import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, Loader, Pause, Play, X, Zap } from 'lucide-react';
import {
  INTERVAL_MINUTES, NOTIFY_CHANNELS, TRIGGER_FILTERS, TRIGGER_KINDS, collapseTicks, getAgentProactive,
  pauseAgentProactive, resumeAgentProactive, updateAgentProactive, wakeAgentProactive,
} from '../../api/proactive';
import { useI18n } from '../../i18n';
import { getWatchers } from '../../api/watchers';
import { useWorkspace } from '../workspace';

const inputCls = 'border border-gray-200 rounded-lg px-2 py-1 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none';
const smallBtn = 'inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed';

const OUTCOME_CLS = {
  acted: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  blocked: 'bg-amber-50 text-amber-800 border-amber-200',
  error: 'bg-red-50 text-red-700 border-red-200',
  quiet: 'bg-gray-50 text-gray-600 border-gray-200',
  running: 'bg-indigo-50 text-indigo-700 border-indigo-200',
};

const fmt = (iso) => {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
};

const numOrNull = (v) => (v === '' || v === null || v === undefined ? null : Number(v));

// A trigger as the form edits it: its kind and the one filter the kind
// understands, as text (a list filter is comma separated).
const triggerToRow = (trig) => {
  const field = TRIGGER_FILTERS[trig.kind];
  const raw = field ? trig[field] : '';
  return { kind: trig.kind, value: Array.isArray(raw) ? raw.join(', ') : (raw || '') };
};
const rowToTrigger = (row) => {
  const field = TRIGGER_FILTERS[row.kind];
  const text = (row.value || '').trim();
  if (!field || !text) return { kind: row.kind };
  const listField = field === 'statuses' || field === 'from';
  return { kind: row.kind, [field]: listField ? text.split(',').map((x) => x.trim()).filter(Boolean) : text };
};

/**
 * The agent's pulse (proactive/, docs/proactive.md): the profile that makes it
 * wake up on its own (schedule, quiet hours, a daily budget and a tick limit,
 * the brief, where an acted tick is delivered), the state of the job the
 * profile owns (next tick, paused), today's usage against the budget, and the
 * tick feed with its outcomes. Quiet ticks fold into one row.
 */
export default function ProactiveCard({ agentId, onSaved }) {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  // The workspace's watchers (docs/watchers.md), offered as the filter of a
  // `watch` trigger instead of a free-text id.
  const [watchers, setWatchers] = useState([]);
  const [body, setBody] = useState(null);
  const [form, setForm] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [acting, setActing] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const tRef = useRef(t);
  tRef.current = t;

  const applyLoaded = useCallback((data) => {
    setBody(data);
    const p = data?.profile || {};
    setForm({
      enabled: !!p.enabled,
      mode: p.cron ? 'cron' : 'interval',
      interval_minutes: p.interval_minutes || 60,
      cron: p.cron || '',
      timezone: p.timezone || 'UTC',
      quiet_from: p.quiet_hours?.from || '',
      quiet_to: p.quiet_hours?.to || '',
      daily_budget_usd: p.daily_budget_usd ?? 0,
      max_runs_per_day: p.max_runs_per_day ?? 0,
      tick_budget_usd: p.tick_budget_usd ?? '',
      brief: p.brief || '',
      notify: Array.isArray(p.notify) ? [...p.notify] : ['dashboard'],
      triggers: Array.isArray(p.triggers) ? p.triggers.map(triggerToRow) : [],
    });
    setDirty(false);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { data } = await getAgentProactive(agentId);
      applyLoaded(data);
    } catch (e) {
      setError(e?.response?.data?.detail || tRef.current('proactive.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [agentId, applyLoaded]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const ws = selectedWorkspace || body?.profile?.workspace || undefined;
    getWatchers(ws).then(({ data }) => setWatchers(Array.isArray(data) ? data : [])).catch(() => setWatchers([]));
  }, [selectedWorkspace, body?.profile?.workspace]);

  const set = (key, value) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setMessage('');
    setDirty(true);
  };

  const toggleChannel = (ch) => {
    set('notify', form.notify.includes(ch) ? form.notify.filter((c) => c !== ch) : [...form.notify, ch]);
  };

  const addTrigger = () => set('triggers', [...form.triggers, { kind: 'file', value: '' }]);
  const changeTrigger = (index, patch) => set('triggers', form.triggers.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  const removeTrigger = (index) => set('triggers', form.triggers.filter((_, i) => i !== index));

  const save = async () => {
    setSaving(true);
    setMessage('');
    setError('');
    try {
      const { data } = await updateAgentProactive(agentId, {
        enabled: form.enabled,
        interval_minutes: Number(form.interval_minutes),
        cron: form.mode === 'cron' ? form.cron.trim() : '',
        timezone: form.timezone.trim() || 'UTC',
        quiet_hours: { from: form.quiet_from.trim(), to: form.quiet_to.trim() },
        daily_budget_usd: Number(form.daily_budget_usd) || 0,
        max_runs_per_day: Number(form.max_runs_per_day) || 0,
        tick_budget_usd: numOrNull(form.tick_budget_usd),
        brief: form.brief,
        notify: form.notify,
        triggers: form.triggers.map(rowToTrigger),
      });
      applyLoaded(data);
      setMessage(t('proactive.saved'));
      onSaved?.();
    } catch (e) {
      setError(e?.response?.data?.detail || t('proactive.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const act = async (name, fn) => {
    setActing(name);
    setError('');
    setMessage('');
    try {
      const { data } = await fn(agentId);
      applyLoaded(data);
      if (name === 'wake') {
        const r = data?.result || {};
        setMessage(r.task_id ? t('proactive.wokeStarted') : t('proactive.wokeSkipped', { outcome: r.skipped || r.outcome || '—' }));
      }
    } catch (e) {
      setError(e?.response?.data?.detail || t('proactive.actionFailed'));
    } finally {
      setActing('');
    }
  };

  const job = body?.job;
  const usage = body?.usage;
  const paused = job?.status === 'paused';
  const live = !!job && job.status !== 'cancelled';
  const ticks = collapseTicks(body?.ticks || []);

  return (
    <div className="bg-white p-6 shadow-md rounded-lg mt-6" data-testid="proactive-card">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-indigo-600" />
          <h3 className="text-lg font-bold text-gray-900">{t('proactive.title')}</h3>
        </div>
        <button type="button" onClick={save} disabled={saving || !dirty || loading}
          className={`px-4 py-2 rounded-lg text-sm font-semibold ${
            saving || !dirty || loading ? 'bg-gray-100 text-gray-400 cursor-not-allowed' : 'bg-indigo-600 text-white hover:bg-indigo-700'
          }`}>
          {saving ? t('common.saving') : t('proactive.save')}
        </button>
      </div>
      <p className="text-sm text-gray-600 mb-4">{t('proactive.intro')}</p>

      {loading || !form ? (
        <Loader className="w-4 h-4 animate-spin text-gray-400" />
      ) : (
        <div className="space-y-6">
          {/* State of the pulse */}
          <div className="rounded-lg border border-gray-100 bg-gray-50 px-4 py-3 text-sm">
            {live ? (
              <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
                <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${
                  paused ? 'bg-amber-50 text-amber-800 border-amber-200' : 'bg-emerald-50 text-emerald-700 border-emerald-200'
                }`} data-testid="pulse-status">
                  {paused ? t('proactive.state.paused', { reason: job.paused_reason || 'manual' }) : t('proactive.state.on')}
                </span>
                <span className="text-gray-700">{t('proactive.state.schedule', { schedule: body.schedule })}</span>
                <span className="text-gray-700">{t('proactive.state.nextTick', { when: paused ? '—' : fmt(job.run_at) })}</span>
                {(job.pending_events || []).length > 0 && (
                  <span className="text-amber-700" data-testid="pulse-pending">
                    {t('proactive.state.pending', { count: job.pending_events.length })}
                  </span>
                )}
                {usage && (
                  <span className="text-gray-700" data-testid="pulse-usage">
                    {t('proactive.state.today', {
                      runs: usage.runs,
                      maxRuns: usage.max_runs_per_day || '∞',
                      spent: Number(usage.spent_usd || 0).toFixed(2),
                      budget: usage.daily_budget_usd ? Number(usage.daily_budget_usd).toFixed(2) : '∞',
                    })}
                  </span>
                )}
                <span className="flex items-center gap-2 ml-auto">
                  {paused ? (
                    <button type="button" className={smallBtn} disabled={!!acting}
                      onClick={() => act('resume', resumeAgentProactive)}>
                      <Play className="w-3.5 h-3.5" />{t('proactive.resume')}
                    </button>
                  ) : (
                    <button type="button" className={smallBtn} disabled={!!acting}
                      onClick={() => act('pause', pauseAgentProactive)}>
                      <Pause className="w-3.5 h-3.5" />{t('proactive.pause')}
                    </button>
                  )}
                  <button type="button" className={smallBtn} disabled={!!acting}
                    onClick={() => act('wake', wakeAgentProactive)}>
                    {acting === 'wake' ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
                    {t('proactive.wake')}
                  </button>
                </span>
              </div>
            ) : (
              <span className="text-gray-500">{t('proactive.state.off')}</span>
            )}
          </div>

          {/* Profile */}
          <label className="flex items-center gap-2.5 text-sm text-gray-800 cursor-pointer">
            <input type="checkbox" checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500" />
            <span className="font-medium">{t('proactive.enabled')}</span>
          </label>

          <div>
            <h4 className="text-sm font-semibold text-gray-800 mb-1">{t('proactive.brief.title')}</h4>
            <p className="text-xs text-gray-500 mb-2">{t('proactive.brief.hint')}</p>
            <textarea value={form.brief} onChange={(e) => set('brief', e.target.value)} rows={5}
              aria-label={t('proactive.brief.title')} placeholder={t('proactive.brief.placeholder')}
              className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="flex flex-col gap-1 text-sm text-gray-700">
              <span className="font-medium">{t('proactive.schedule.title')}</span>
              <div className="flex gap-3 text-xs">
                <label className="flex items-center gap-1"><input type="radio" name="pulse-mode" checked={form.mode === 'interval'} onChange={() => set('mode', 'interval')} />{t('proactive.schedule.interval')}</label>
                <label className="flex items-center gap-1"><input type="radio" name="pulse-mode" checked={form.mode === 'cron'} onChange={() => set('mode', 'cron')} />{t('proactive.schedule.cron')}</label>
              </div>
              {form.mode === 'interval' ? (
                <select value={form.interval_minutes} onChange={(e) => set('interval_minutes', Number(e.target.value))}
                  aria-label={t('proactive.schedule.interval')} className={inputCls}>
                  {INTERVAL_MINUTES.map((m) => (
                    <option key={m} value={m}>
                      {m < 60 ? t('proactive.schedule.everyMinutes', { n: m })
                        : m === 1440 ? t('proactive.schedule.daily')
                          : t('proactive.schedule.everyHours', { n: m / 60 })}
                    </option>
                  ))}
                </select>
              ) : (
                <input value={form.cron} onChange={(e) => set('cron', e.target.value)} placeholder="*/30 8-20 * * 1-5"
                  aria-label={t('proactive.schedule.cron')} className={`${inputCls} font-mono`} />
              )}
            </div>
            <label className="flex flex-col gap-1 text-sm text-gray-700">
              <span className="font-medium">{t('proactive.timezone')}</span>
              <input value={form.timezone} onChange={(e) => set('timezone', e.target.value)} placeholder="Europe/Berlin"
                aria-label={t('proactive.timezone')} className={inputCls} />
            </label>
            <div className="flex flex-col gap-1 text-sm text-gray-700">
              <span className="font-medium">{t('proactive.quietHours.title')}</span>
              <div className="flex items-center gap-2">
                <input type="time" value={form.quiet_from} onChange={(e) => set('quiet_from', e.target.value)}
                  aria-label={t('proactive.quietHours.from')} className={inputCls} />
                <span className="text-gray-400">→</span>
                <input type="time" value={form.quiet_to} onChange={(e) => set('quiet_to', e.target.value)}
                  aria-label={t('proactive.quietHours.to')} className={inputCls} />
              </div>
              <span className="text-xs text-gray-500">{t('proactive.quietHours.hint')}</span>
            </div>
            <label className="flex flex-col gap-1 text-sm text-gray-700">
              <span className="font-medium">{t('proactive.dailyBudget')}</span>
              <input type="number" min="0" step="0.5" value={form.daily_budget_usd}
                onChange={(e) => set('daily_budget_usd', e.target.value)} aria-label={t('proactive.dailyBudget')} className={inputCls} />
              <span className="text-xs text-gray-500">{t('proactive.zeroMeansNone')}</span>
            </label>
            <label className="flex flex-col gap-1 text-sm text-gray-700">
              <span className="font-medium">{t('proactive.maxRuns')}</span>
              <input type="number" min="0" step="1" value={form.max_runs_per_day}
                onChange={(e) => set('max_runs_per_day', e.target.value)} aria-label={t('proactive.maxRuns')} className={inputCls} />
              <span className="text-xs text-gray-500">{t('proactive.zeroMeansNone')}</span>
            </label>
            <label className="flex flex-col gap-1 text-sm text-gray-700">
              <span className="font-medium">{t('proactive.tickBudget')}</span>
              <input type="number" min="0" step="0.1" value={form.tick_budget_usd}
                onChange={(e) => set('tick_budget_usd', e.target.value)} aria-label={t('proactive.tickBudget')} className={inputCls} />
              <span className="text-xs text-gray-500">{t('proactive.tickBudgetHint')}</span>
            </label>
          </div>

          <div>
            <h4 className="text-sm font-semibold text-gray-800 mb-1">{t('proactive.triggers.title')}</h4>
            <p className="text-xs text-gray-500 mb-2">{t('proactive.triggers.hint')}</p>
            {form.triggers.length === 0 ? (
              <p className="text-sm text-gray-400 italic mb-2">{t('proactive.triggers.empty')}</p>
            ) : (
              <ul className="space-y-1.5 mb-2" data-testid="trigger-list">
                {form.triggers.map((row, index) => (
                  <li key={index} className="flex items-center gap-2 text-sm">
                    <select value={row.kind} aria-label={t('proactive.triggers.kind')}
                      onChange={(e) => changeTrigger(index, { kind: e.target.value, value: '' })} className={inputCls}>
                      {TRIGGER_KINDS.map((k) => <option key={k} value={k}>{t(`proactive.triggers.kinds.${k}`)}</option>)}
                    </select>
                    {row.kind === 'watch' ? (
                      <select value={row.value} onChange={(e) => changeTrigger(index, { value: e.target.value })}
                        aria-label={t('proactive.triggers.filters.watcher_id')} className={`${inputCls} flex-1`}>
                        <option value="">{t('proactive.triggers.filters.watcher_id')}</option>
                        {watchers.map((w) => <option key={w.id} value={w.id}>{w.name} ({w.kind})</option>)}
                      </select>
                    ) : TRIGGER_FILTERS[row.kind] ? (
                      <input value={row.value} onChange={(e) => changeTrigger(index, { value: e.target.value })}
                        aria-label={t(`proactive.triggers.filters.${TRIGGER_FILTERS[row.kind]}`)}
                        placeholder={t(`proactive.triggers.filters.${TRIGGER_FILTERS[row.kind]}`)}
                        className={`${inputCls} flex-1`} />
                    ) : (
                      <span className="flex-1 text-xs text-gray-500">{t('proactive.triggers.noFilter')}</span>
                    )}
                    <button type="button" onClick={() => removeTrigger(index)} aria-label={t('proactive.triggers.remove')}
                      className="p-1 rounded text-gray-500 hover:text-gray-800 hover:bg-gray-100">
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <button type="button" onClick={addTrigger} className={smallBtn}>{t('proactive.triggers.add')}</button>
            {form.triggers.some((r) => ['webhook', 'telegram', 'file', 'watch', 'slack', 'discord', 'teams', 'mail'].includes(r.kind)) && (
              <p className="text-xs text-amber-700 mt-2">{t('proactive.triggers.untrusted')}</p>
            )}
          </div>

          <div>
            <h4 className="text-sm font-semibold text-gray-800 mb-1">{t('proactive.notify.title')}</h4>
            <p className="text-xs text-gray-500 mb-2">{t('proactive.notify.hint')}</p>
            <div className="flex flex-wrap gap-4 text-sm text-gray-700">
              {NOTIFY_CHANNELS.map((ch) => (
                <label key={ch} className="flex items-center gap-1.5 cursor-pointer">
                  <input type="checkbox" checked={form.notify.includes(ch)} onChange={() => toggleChannel(ch)}
                    className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500" />
                  {t(`proactive.notify.${ch}`)}
                </label>
              ))}
            </div>
          </div>

          {error && <p className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>}
          {message && <p className="text-xs text-gray-600">{message}</p>}

          {/* Tick feed */}
          {live && (
            <div>
              <h4 className="text-sm font-semibold text-gray-800 mb-2">{t('proactive.feed.title')}</h4>
              {ticks.length === 0 ? (
                <p className="text-sm text-gray-400 italic">{t('proactive.feed.empty')}</p>
              ) : (
                <ul className="space-y-1.5" data-testid="tick-feed">
                  {ticks.map((tick) => (
                    tick.group ? (
                      <li key={tick.id} className="flex items-center gap-3 text-xs text-gray-500 px-3 py-1.5 rounded-lg bg-gray-50 border border-gray-100">
                        <span className="w-36 shrink-0">{fmt(tick.at)}</span>
                        <span>{t(`proactive.feed.group.${tick.outcome}`, { count: tick.count })}</span>
                      </li>
                    ) : (
                      <li key={tick.id} className="flex items-start gap-3 text-sm px-3 py-2 rounded-lg border border-gray-100">
                        <span className="w-36 shrink-0 text-xs text-gray-500 pt-0.5">{fmt(tick.at)}</span>
                        <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border shrink-0 ${
                          OUTCOME_CLS[tick.outcome || 'running'] || OUTCOME_CLS.quiet
                        }`}>
                          {t(`proactive.outcome.${tick.outcome || 'running'}`)}
                        </span>
                        <span className="flex-1 min-w-0">
                          <span className="block text-gray-800 whitespace-pre-wrap break-words">{tick.summary || tick.error || ''}</span>
                          {tick.next_check && (
                            <span className="block text-xs text-gray-500 mt-0.5">{t('proactive.feed.nextCheck', { text: tick.next_check })}</span>
                          )}
                          <span className="block text-xs text-gray-400 mt-0.5">
                            {tick.cost_usd != null && <span className="mr-3">${Number(tick.cost_usd).toFixed(4)}</span>}
                            {tick.task_id && <a className="text-indigo-600 hover:underline" href={`/tasks/${tick.task_id}`}>{t('proactive.feed.task')}</a>}
                            {tick.trigger === 'manual' && <span className="ml-3">{t('proactive.feed.manual')}</span>}
                          </span>
                        </span>
                      </li>
                    )
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
