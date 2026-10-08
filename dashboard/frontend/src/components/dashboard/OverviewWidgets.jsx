import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity, AlertCircle, CheckCircle2, ChevronRight, CircleStop, Cpu, DollarSign, Globe, PauseCircle,
  Server, TrendingUp, XCircle,
} from 'lucide-react';
import { useI18n } from '../../i18n';
import {
  fmtAgo, fmtBytes, fmtInt, fmtMs, fmtTokens, fmtUsd, runHref,
} from './format';

/*
 * The Dashboard's load and spend widgets, fed by one call
 * (GET /api/stats/overview, common/dashboard_overview.py). Each widget gets
 * its own section of that answer and shows the section's `error` instead of
 * numbers when the backend could not compute it.
 */

const dayLabel = (iso, language) => {
  const d = new Date(`${iso}T12:00:00`);
  return d.toLocaleDateString(language, { day: 'numeric', month: 'short' });
};

/* ── Building blocks ───────────────────────────────────────── */

export const Panel = ({ icon: Icon, iconColor, title, to, linkLabel, badge, children, testId }) => (
  <div className="relative flex-1 bg-white p-5 rounded-xl shadow-sm border border-gray-100 flex flex-col min-w-0" data-testid={testId}>
    <div className="flex justify-between items-center gap-2 mb-4">
      <h3 className="font-bold text-gray-700 flex items-center text-sm min-w-0">
        <Icon className={`w-4 h-4 mr-2 shrink-0 ${iconColor}`} />
        <span className="truncate">{title}</span>
        {badge}
      </h3>
      {to && (
        <Link to={to} className="text-xs text-indigo-600 hover:underline flex items-center shrink-0">
          {linkLabel} <ChevronRight className="w-3 h-3 ml-0.5" />
        </Link>
      )}
    </div>
    {children}
  </div>
);

const Tile = ({ label, value, hint, tone = 'text-gray-800' }) => (
  <div className="rounded-lg bg-gray-50 px-3 py-2 min-w-0 flex flex-col justify-between">
    <div className="text-[11px] uppercase tracking-wide text-gray-400 leading-tight break-words">{label}</div>
    <div className={`text-lg font-semibold tabular-nums truncate ${tone}`}>{value}</div>
    {hint && <div className="text-[11px] text-gray-400 truncate">{hint}</div>}
  </div>
);

const SectionError = ({ message }) => {
  const { t } = useI18n();
  return (
    <p className="flex items-start gap-1.5 text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">
      <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
      <span>{t('dashboard.overview.unavailable')}: {message}</span>
    </p>
  );
};

/** One labelled share bar: a list row with the value on the right. */
const ShareRow = ({ label, value, share, mono }) => (
  <div className="py-1">
    <div className="flex items-center justify-between gap-2 text-xs">
      <span className={`truncate text-gray-600 ${mono ? 'font-mono' : ''}`} title={label}>{label}</span>
      <span className="shrink-0 tabular-nums font-medium text-gray-700">{value}</span>
    </div>
    <div className="mt-1 h-1 rounded-full bg-gray-100">
      <div className="h-1 rounded-full bg-indigo-400" style={{ width: `${Math.max(2, Math.min(100, share * 100))}%` }} />
    </div>
  </div>
);

/**
 * Bars per day. `series` are stacked bottom-up in the given order; one series
 * draws a plain bar. Hovering a day names it and its values above the chart,
 * so no bar carries a number of its own.
 */
const DayBars = ({ days, series, format = fmtInt, height = 72, testId }) => {
  const { language } = useI18n();
  const [hover, setHover] = useState(null);
  const totals = days.map((d) => series.reduce((n, s) => n + (Number(d[s.key]) || 0), 0));
  const max = Math.max(...totals, 0);
  const shown = hover === null ? null : days[hover];

  return (
    <div data-testid={testId}>
      <div className="flex items-center justify-between gap-2 text-[11px] text-gray-400 h-4 mb-1">
        {shown ? (
          <>
            <span className="text-gray-600 font-medium">{dayLabel(shown.date, language)}</span>
            <span className="flex items-center gap-2 tabular-nums">
              {series.map((s) => (
                <span key={s.key} className="flex items-center gap-1">
                  <span className={`w-2 h-2 rounded-sm ${s.color}`} />
                  {series.length > 1 && <span>{s.label}</span>}
                  <span className="text-gray-700 font-medium">{format(shown[s.key])}</span>
                </span>
              ))}
            </span>
          </>
        ) : series.length > 1 ? (
          <span className="flex items-center gap-3">
            {series.map((s) => (
              <span key={s.key} className="flex items-center gap-1">
                <span className={`w-2 h-2 rounded-sm ${s.color}`} />{s.label}
              </span>
            ))}
          </span>
        ) : <span />}
      </div>
      <div className="flex items-end gap-0.5 border-b border-gray-100" style={{ height }} onMouseLeave={() => setHover(null)}>
        {days.map((d, i) => (
          <div
            key={d.date}
            className={`flex-1 h-full flex flex-col justify-end rounded-t ${hover === i ? 'bg-gray-50' : ''}`}
            onMouseEnter={() => setHover(i)}
          >
            {max > 0 && totals[i] > 0 && (
              <div className="flex flex-col-reverse gap-[2px] overflow-hidden rounded-t" style={{ height: `${Math.max(3, (totals[i] / max) * 100)}%` }}>
                {series.map((s) => {
                  const v = Number(d[s.key]) || 0;
                  if (!v) return null;
                  // Shares of the day's total: flex-grow below 1 (a few cents)
                  // would leave most of the column empty.
                  return <div key={s.key} className={s.color} style={{ flexGrow: v / totals[i], minHeight: 2 }} />;
                })}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="flex justify-between text-[10px] text-gray-400 mt-1">
        <span>{days.length ? dayLabel(days[0].date, language) : ''}</span>
        <span>{days.length ? dayLabel(days[days.length - 1].date, language) : ''}</span>
      </div>
    </div>
  );
};

/* ── Widgets ───────────────────────────────────────────────── */

export const RunsWidget = ({ runs }) => {
  const { t } = useI18n();
  const last = runs?.last_24h || {};
  const channels = Object.entries(last.channels || {}).slice(0, 5);
  return (
    <Panel
      icon={TrendingUp} iconColor="text-emerald-500" testId="dash-runs"
      title={t('dashboard.overview.runs.heading', { days: runs?.days || 14 })}
      to="/sessions" linkLabel={t('dashboard.overview.runs.link')}
    >
      {runs?.error ? <SectionError message={runs.error} /> : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
            <Tile label={t('dashboard.overview.runs.last24h')} value={fmtInt(last.total)} />
            <Tile
              label={t('dashboard.overview.runs.successRate')}
              value={last.success_rate === null || last.success_rate === undefined ? '—' : `${last.success_rate}%`}
              hint={last.failed ? t('dashboard.overview.runs.failedCount', { count: last.failed }) : undefined}
              tone={last.failed ? 'text-amber-700' : 'text-gray-800'}
            />
            <Tile label={t('dashboard.overview.runs.avgDuration')} value={fmtMs(last.avg_duration_ms)} />
            <Tile label={t('dashboard.overview.runs.p95Duration')} value={fmtMs(last.p95_duration_ms)} />
          </div>
          <DayBars
            testId="dash-runs-chart"
            height={96}
            days={runs?.per_day || []}
            series={[
              { key: 'completed', label: t('dashboard.overview.runs.completed'), color: 'bg-emerald-500' },
              { key: 'failed', label: t('dashboard.overview.runs.failed'), color: 'bg-red-400' },
              { key: 'other', label: t('dashboard.overview.runs.other'), color: 'bg-gray-300' },
            ]}
          />
          {channels.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-3">
              <span className="text-[11px] text-gray-400 mr-1 self-center">{t('dashboard.overview.runs.channels')}</span>
              {channels.map(([name, n]) => (
                <span key={name} className="inline-flex items-center gap-1 rounded-full border border-gray-200 px-2 py-0.5 text-[11px] text-gray-600">
                  {t(`dashboard.overview.channels.${name}`, { defaultValue: name })}
                  <span className="font-semibold text-gray-800 tabular-nums">{fmtInt(n)}</span>
                </span>
              ))}
            </div>
          )}
        </>
      )}
    </Panel>
  );
};

export const CostsWidget = ({ costs }) => {
  const { t } = useI18n();
  const top = costs?.top_agents || [];
  const topMax = Math.max(...top.map((a) => a.cost), 0);
  const budget = costs?.budget;
  const cap = budget ? (budget.hard_limit_usd || budget.soft_limit_usd) : 0;
  const used = cap ? Math.min(1, (budget.spend || 0) / cap) : 0;
  return (
    <Panel
      icon={DollarSign} iconColor="text-amber-500" testId="dash-costs"
      title={t('dashboard.overview.costs.heading')}
      to="/costs" linkLabel={t('dashboard.overview.costs.link')}
    >
      {costs?.error ? <SectionError message={costs.error} /> : (
        <>
          <div className="grid grid-cols-3 gap-2 mb-4">
            <Tile label={t('dashboard.overview.costs.today')} value={fmtUsd(costs?.today)} />
            <Tile label={t('dashboard.overview.costs.week')} value={fmtUsd(costs?.last_7d)} />
            <Tile
              label={t('dashboard.overview.costs.month')} value={fmtUsd(costs?.last_30d)}
              hint={t('dashboard.overview.costs.tokens', { tokens: fmtTokens(costs?.tokens_30d) })}
            />
          </div>
          {budget && (
            <div className="mb-4" data-testid="dash-budget">
              <div className="flex justify-between text-xs text-gray-500 mb-1">
                <span>{t('dashboard.overview.costs.budget', { period: t(`dashboard.overview.costs.periods.${budget.period}`, { defaultValue: budget.period }) })}</span>
                <span className="tabular-nums">{fmtUsd(budget.spend)} / {fmtUsd(cap)}</span>
              </div>
              <div className="w-full bg-gray-100 rounded-full h-2">
                <div
                  className={`h-2 rounded-full ${budget.hard_exceeded ? 'bg-red-500' : budget.soft_exceeded ? 'bg-amber-400' : 'bg-indigo-400'}`}
                  style={{ width: `${Math.max(2, used * 100)}%` }}
                />
              </div>
            </div>
          )}
          <DayBars
            testId="dash-costs-chart"
            days={costs?.per_day || []}
            series={[{ key: 'cost', label: t('dashboard.overview.costs.perDay'), color: 'bg-amber-400' }]}
            format={fmtUsd}
            height={56}
          />
          {top.length > 0 && (
            <div className="mt-3">
              <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">{t('dashboard.overview.costs.topAgents')}</div>
              {top.slice(0, 4).map((a) => (
                <ShareRow key={a.key} label={a.key} value={fmtUsd(a.cost)} share={topMax ? a.cost / topMax : 0} />
              ))}
            </div>
          )}
        </>
      )}
    </Panel>
  );
};

const RUNTIME_SOURCES = ['hub', 'endpoint', 'voice', 'direct'];

export const LocalModelsWidget = ({ local }) => {
  const { t } = useI18n();
  const usage = local?.usage;
  const totals = usage?.totals || {};
  const mem = local?.memory || {};
  const ramUsed = mem.ram_total_bytes ? 1 - (mem.ram_available_bytes || 0) / mem.ram_total_bytes : null;
  const top = usage?.top_models || [];
  const topMax = Math.max(...top.map((m) => m.requests), 0);

  let state;
  if (!local || local.error) state = local?.error ? 'error' : 'unknown';
  else if (!local.configured) state = 'off';
  else if (local.ok) state = 'running';
  else state = local.state || 'stopped';
  const stateTone = {
    running: 'bg-green-50 text-green-700 border-green-100',
    error: 'bg-red-50 text-red-700 border-red-100',
    failed: 'bg-red-50 text-red-700 border-red-100',
  }[state] || 'bg-gray-50 text-gray-500 border-gray-200';

  return (
    <Panel
      icon={Cpu} iconColor="text-violet-500" testId="dash-local-models"
      title={t('dashboard.overview.local.heading')}
      to="/models?tab=local" linkLabel={t('dashboard.overview.local.link')}
      badge={(
        <span className={`ml-2 text-[11px] px-2 py-0.5 rounded-full border font-medium ${stateTone}`}>
          {t(`dashboard.overview.local.states.${state}`, { defaultValue: state })}
        </span>
      )}
    >
      {!local?.configured ? (
        <p className="text-sm text-gray-400 italic">{t('dashboard.overview.local.notConfigured')}</p>
      ) : !local.ok ? (
        <p className="text-sm text-gray-400">
          {local.error || (local.stopped_by_user ? t('dashboard.overview.local.stoppedByUser') : t('dashboard.overview.local.notRunning'))}
        </p>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2 mb-3">
            <Tile label={t('dashboard.overview.local.loaded')} value={`${local.loaded.length}/${local.models}`} />
            <Tile
              label={t('dashboard.overview.local.calls')} value={fmtInt(totals.requests)}
              hint={totals.errors ? t('dashboard.overview.local.errors', { count: totals.errors }) : undefined}
              tone={totals.errors ? 'text-amber-700' : 'text-gray-800'}
            />
            <Tile label={t('dashboard.overview.local.avgTime')} value={totals.requests ? fmtMs(totals.duration_ms / totals.requests) : '—'} />
          </div>
          {ramUsed !== null && (
            <div className="mb-3">
              <div className="flex justify-between text-xs text-gray-500 mb-1">
                <span>{t('dashboard.overview.local.ram')}</span>
                <span className="tabular-nums">
                  {fmtBytes(mem.ram_total_bytes - (mem.ram_available_bytes || 0))} / {fmtBytes(mem.ram_total_bytes)}
                </span>
              </div>
              <div className="w-full bg-gray-100 rounded-full h-2">
                <div className={`h-2 rounded-full ${ramUsed > 0.9 ? 'bg-red-500' : ramUsed > 0.75 ? 'bg-amber-400' : 'bg-violet-400'}`} style={{ width: `${Math.max(2, ramUsed * 100)}%` }} />
              </div>
            </div>
          )}
          {local.usage_outdated && (
            <p className="text-xs text-amber-700 mb-2">{t('dashboard.overview.local.restartNeeded')}</p>
          )}
          {usage && Object.keys(usage.by_source || {}).length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {RUNTIME_SOURCES.filter((s) => usage.by_source[s]).map((s) => (
                <span key={s} className="inline-flex items-center gap-1 rounded-full border border-gray-200 px-2 py-0.5 text-[11px] text-gray-600">
                  {t(`localModels.runtimeUsage.sources.${s}`, { defaultValue: s })}
                  <span className="font-semibold text-gray-800 tabular-nums">{fmtInt(usage.by_source[s])}</span>
                </span>
              ))}
            </div>
          )}
          {top.length > 0 ? (
            <div>
              {top.slice(0, 4).map((m) => (
                <ShareRow
                  key={m.model} mono label={m.model} share={topMax ? m.requests / topMax : 0}
                  value={[
                    fmtInt(m.requests),
                    m.tokens_per_second ? `${m.tokens_per_second} tok/s` : (m.avg_ms ? fmtMs(m.avg_ms) : null),
                  ].filter(Boolean).join(' · ')}
                />
              ))}
            </div>
          ) : local.loaded.length > 0 ? (
            <div className="space-y-1">
              {local.loaded.map((m) => (
                <div key={m.name} className="flex justify-between text-xs py-0.5">
                  <span className="font-mono text-gray-600 truncate">{m.name}</span>
                  <span className="text-gray-400 shrink-0">{m.kind}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-gray-400 italic">{t('dashboard.overview.local.nothingLoaded')}</p>
          )}
          {usage?.since && (
            <p className="text-[11px] text-gray-400 mt-2">{t('dashboard.overview.local.since', { date: new Date(usage.since).toLocaleString() })}</p>
          )}
        </>
      )}
    </Panel>
  );
};

export const EndpointWidget = ({ endpoint, days }) => {
  const { t } = useI18n();
  const day = endpoint?.last_24h || {};
  const win = endpoint?.window || {};
  const top = endpoint?.top_models || [];
  const topMax = Math.max(...top.map((m) => m.requests), 0);
  return (
    <Panel
      icon={Globe} iconColor="text-sky-500" testId="dash-endpoint"
      title={t('dashboard.overview.endpoint.heading')}
      to="/models?tab=endpoint" linkLabel={t('dashboard.overview.endpoint.link')}
    >
      {endpoint?.error ? <SectionError message={endpoint.error} /> : (
        <>
          <div className="grid grid-cols-3 gap-2 mb-4">
            <Tile
              label={t('dashboard.overview.endpoint.calls24h')} value={fmtInt(day.requests)}
              hint={day.errors ? t('dashboard.overview.endpoint.errors', { count: day.errors }) : undefined}
              tone={day.errors ? 'text-amber-700' : 'text-gray-800'}
            />
            <Tile label={t('dashboard.overview.endpoint.tokens24h')} value={fmtTokens(day.total_tokens)} />
            <Tile
              label={t('dashboard.overview.endpoint.callsWindow', { days })} value={fmtInt(win.requests)}
              hint={win.cost ? fmtUsd(win.cost) : undefined}
            />
          </div>
          {win.requests > 0 ? (
            <>
              <DayBars
                testId="dash-endpoint-chart"
                days={endpoint?.per_day || []}
                series={[{ key: 'requests', label: t('dashboard.overview.endpoint.calls'), color: 'bg-sky-400' }]}
                height={56}
              />
              {top.length > 0 && (
                <div className="mt-3">
                  {top.slice(0, 4).map((m) => (
                    <ShareRow key={m.key} mono label={m.key} value={fmtInt(m.requests)} share={topMax ? m.requests / topMax : 0} />
                  ))}
                </div>
              )}
            </>
          ) : (
            <p className="text-xs text-gray-400 italic">{t('dashboard.overview.endpoint.empty', { days })}</p>
          )}
        </>
      )}
    </Panel>
  );
};

export const ServicesWidget = ({ services }) => {
  const { t } = useI18n();
  const totals = services?.totals || {};
  const items = services?.items || [];
  return (
    <Panel
      icon={Server} iconColor="text-teal-500" testId="dash-services"
      title={t('dashboard.overview.services.heading')}
      to="/services" linkLabel={t('dashboard.overview.services.link')}
    >
      {services?.error ? <SectionError message={services.error} /> : (
        <>
          <div className="grid grid-cols-3 gap-2 mb-4">
            <Tile
              label={t('dashboard.overview.services.services')} value={fmtInt(totals.services)}
              hint={totals.paused ? t('dashboard.overview.services.paused', { count: totals.paused }) : undefined}
            />
            <Tile label={t('dashboard.overview.services.replicas')} value={fmtInt(totals.replicas_live)} />
            <Tile
              label={t('dashboard.overview.services.turns24h')} value={fmtInt(totals.turns_24h)}
              hint={totals.failed_24h ? t('dashboard.overview.services.failed', { count: totals.failed_24h }) : undefined}
              tone={totals.failed_24h ? 'text-amber-700' : 'text-gray-800'}
            />
          </div>
          {items.length > 0 ? (
            <div className="space-y-1.5">
              {items.map((s) => (
                <Link
                  key={s.service_id} to={`/services/${encodeURIComponent(s.service_id)}`}
                  className="flex items-center justify-between gap-2 py-1.5 px-2.5 rounded-lg bg-gray-50 hover:bg-gray-100 transition-colors"
                >
                  <span className="flex items-center gap-2 min-w-0">
                    {s.paused
                      ? <PauseCircle className="w-3.5 h-3.5 text-gray-400 shrink-0" />
                      : <span className={`w-2 h-2 rounded-full shrink-0 ${s.replicas_live > 0 ? 'bg-green-400' : 'bg-gray-300'}`} />}
                    <span className="text-sm text-gray-700 truncate">{s.name}</span>
                    {s.workspace && <span className="text-[11px] text-gray-400 truncate">{s.workspace}</span>}
                  </span>
                  <span className="flex items-center gap-3 text-xs text-gray-500 shrink-0 tabular-nums">
                    <span title={t('dashboard.overview.services.replicasHint')}>
                      {s.replicas_live}{s.replicas_max ? `/${s.replicas_max}` : ''}
                    </span>
                    <span title={t('dashboard.overview.services.turnsHint')}>
                      {t('dashboard.overview.services.turns', { count: s.turns_24h })}
                    </span>
                  </span>
                </Link>
              ))}
            </div>
          ) : (
            <p className="text-xs text-gray-400 italic">{t('dashboard.overview.services.empty')}</p>
          )}
        </>
      )}
    </Panel>
  );
};

const ENDED_ICON = {
  completed: <CheckCircle2 className="w-3.5 h-3.5 text-green-500 shrink-0" />,
  failed: <XCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />,
  stopped: <CircleStop className="w-3.5 h-3.5 text-gray-400 shrink-0" />,
};

const RunRow = ({ item, children }) => {
  const href = runHref(item);
  const cls = 'flex items-center gap-2 py-1.5 px-2.5 rounded-lg bg-gray-50 min-w-0';
  return href
    ? <Link to={href} className={`${cls} hover:bg-gray-100 transition-colors`}>{children}</Link>
    : <div className={cls}>{children}</div>;
};

/**
 * What runs right now, of every kind: agent runs (a chat turn, a task, a
 * service's turn), flows, loops, teams and scenarios, plus the launch queue.
 * With nothing running it shows the last runs that ended instead. Records
 * still marked active but silent for long are counted apart: their process
 * most likely died without closing them. `generatedAt` is the overview's
 * own time, what elapsed times are counted to.
 */
export const LiveWidget = ({ live, generatedAt }) => {
  const { t, language } = useI18n();
  const items = live?.items || [];
  const byKind = Object.entries(live?.by_kind || {});
  const queue = live?.queue || {};
  const kindLabel = (k) => t(`dashboard.overview.live.kinds.${k}`, { defaultValue: k });
  // The server's clock at answer time: elapsed and ago times stay as of the
  // data, and rendering stays pure.
  const now = generatedAt ? new Date(generatedAt).getTime() : 0;
  return (
    <Panel
      icon={Activity} iconColor="text-orange-500" testId="dash-live"
      title={t('dashboard.overview.live.heading')}
      to="/messages" linkLabel={t('dashboard.overview.live.link')}
      badge={live?.total > 0 && (
        <span className="ml-2 px-1.5 py-0.5 text-xs bg-orange-100 text-orange-700 rounded-full font-semibold">{live.total}</span>
      )}
    >
      {live?.error ? <SectionError message={live.error} /> : (
        <>
          {(byKind.length > 0 || queue.queued > 0) && (
            <div className="flex flex-wrap gap-1.5 mb-3">
              {byKind.map(([k, n]) => (
                <span key={k} className="inline-flex items-center gap-1 rounded-full border border-gray-200 px-2 py-0.5 text-[11px] text-gray-600">
                  {kindLabel(k)}<span className="font-semibold text-gray-800 tabular-nums">{n}</span>
                </span>
              ))}
              {queue.queued > 0 && (
                <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] text-amber-700">
                  {t('dashboard.overview.live.queued', { count: queue.queued, wait: fmtMs((queue.oldest_queued_seconds || 0) * 1000) })}
                </span>
              )}
            </div>
          )}

          {items.length > 0 ? (
            <div className="space-y-1.5">
              {items.map((it) => (
                <RunRow key={`${it.kind}:${it.run_id}`} item={it}>
                  <span className={`w-2 h-2 rounded-full shrink-0 ${it.status === 'awaiting_input' ? 'bg-amber-400' : 'bg-orange-400 animate-pulse'}`} />
                  <span className="text-sm font-medium text-gray-700 truncate">{it.name}</span>
                  <span className="text-[11px] text-gray-400 shrink-0">{kindLabel(it.kind)}</span>
                  {it.title && <span className="text-xs text-gray-400 truncate min-w-0">{it.title}</span>}
                  <span className="ml-auto text-xs text-gray-400 shrink-0 tabular-nums">
                    {it.status === 'awaiting_input'
                      ? t('dashboard.overview.live.awaiting')
                      : it.started_at ? fmtMs(now - new Date(it.started_at).getTime()) : ''}
                  </span>
                </RunRow>
              ))}
            </div>
          ) : (
            <>
              <p className="text-sm text-gray-400 mb-3">{t('dashboard.overview.live.nothing')}</p>
              {(live?.recent || []).length > 0 && (
                <div data-testid="dash-live-recent">
                  <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1.5">{t('dashboard.overview.live.recent')}</div>
                  <div className="space-y-1.5">
                    {live.recent.map((it) => (
                      <RunRow key={`${it.kind}:${it.run_id}`} item={it}>
                        {ENDED_ICON[it.status] || ENDED_ICON.stopped}
                        <span className="text-sm text-gray-700 truncate shrink-0 max-w-[40%]">{it.name}</span>
                        {it.title && <span className="text-xs text-gray-400 truncate min-w-0">{it.title}</span>}
                        <span className="ml-auto text-xs text-gray-400 shrink-0 tabular-nums">
                          {[it.duration_ms ? fmtMs(it.duration_ms) : null, fmtAgo(it.finished_at, language, now)].filter(Boolean).join(' · ')}
                        </span>
                      </RunRow>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

          {live?.stale_total > 0 && (
            <p
              className="mt-3 flex items-start gap-1.5 text-xs text-amber-700 bg-amber-50 rounded-lg px-3 py-2"
              title={(live.stale || []).map((s) => `${s.name} (${kindLabel(s.kind)}), ${fmtAgo(s.started_at, language, now)}`).join('\n')}
              data-testid="dash-live-stale"
            >
              <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              <span>{t('dashboard.overview.live.stale', { count: live.stale_total })}</span>
            </p>
          )}
        </>
      )}
    </Panel>
  );
};
