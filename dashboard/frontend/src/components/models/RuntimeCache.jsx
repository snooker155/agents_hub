import { useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Flame, Loader, RefreshCw, RotateCw, Table2, Trash2 } from 'lucide-react';
import {
  applyRuntimeCache, clearRuntimeCache, getRuntimeCache, setRuntimeCacheSettings, warmRuntimeCache,
} from '../../api/localModels';
import { humanBytes } from './jobs';
import {
  RANGES, bucketize, fmtCompact, fmtDuration, fmtPercent, modelStats, overallStats,
} from './cacheStats';
import { Legend, Meter, PartsBar, TokenColumns, TtftLine } from './CacheCharts';
import CacheSettings from './CacheSettings';
import { isAdmin, isMultiUser, useAuth } from '../auth';
import { useToast, errorDetail } from '../toast';
import { useI18n } from '../../i18n';

const POLL_MS = 15000;
// While a model warms up its progress changes by the second.
const BUSY_POLL_MS = 3000;
const btnCls = 'flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-xs font-medium transition-colors disabled:opacity-50';

function Tile({ label, value, sub, testId }) {
  return (
    <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2" data-testid={testId}>
      <p className="text-[11px] uppercase tracking-wide text-gray-400">{label}</p>
      <p className="text-lg font-semibold text-gray-800">{value}</p>
      {sub && <p className="text-xs text-gray-500">{sub}</p>}
    </div>
  );
}

function Section({ title, children, action }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{title}</p>
        <span className="ml-auto">{action}</span>
      </div>
      {children}
    </div>
  );
}

/**
 * The runtime's prompt cache (deploy/models/app.py, "Prompt cache"): how
 * well it works, what it holds, what it takes, and its settings. The Models
 * page's Prompt cache tab shows it `standalone`: open from the start, with
 * no fold, and shown even before the first figures (`ready` says the
 * status came); elsewhere it is a card folded by default, whose header
 * still shows the share of prompt tokens served from the cache and the
 * time it saved.
 *
 * How it works comes from the runtime's call counts, which arrive with the
 * runtime status (`usage`: per model rows and a day of 5 minute buckets);
 * what it holds and takes, and the settings, from
 * GET /models/local/runtime/cache, read here while the card is open.
 */
export default function RuntimeCache({ usage, standalone = false, ready = false }) {
  const { t, language } = useI18n();
  const toast = useToast();
  const auth = useAuth();
  const units = {
    ms: t('localModels.runtimeCache.units.ms'), s: t('localModels.runtimeCache.units.s'),
    min: t('localModels.runtimeCache.units.min'), h: t('localModels.runtimeCache.units.h'),
  };
  const dur = (ms) => fmtDuration(ms, units);
  const canEdit = !isMultiUser(auth) || isAdmin(auth);
  const [open, setOpen] = useState(standalone);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [applying, setApplying] = useState(false);
  const [range, setRange] = useState('6h');
  const [asTable, setAsTable] = useState(false);
  const [now, setNow] = useState(() => Date.now() / 1000);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getRuntimeCache();
      setData(res.data || null);
    } catch (err) {
      setData({ ok: false, error: errorDetail(err) });
    } finally {
      setLoading(false);
      setNow(Date.now() / 1000);
    }
  }, []);

  const warming = (data?.models || []).some((m) => m.warmup?.state === 'running');
  useEffect(() => {
    if (!open) return undefined;
    load();
    const id = setInterval(load, warming ? BUSY_POLL_MS : POLL_MS);
    return () => clearInterval(id);
  }, [open, load, warming]);

  const models = useMemo(() => modelStats(usage?.rows), [usage]);
  const overall = useMemo(() => overallStats(models), [models]);
  const buckets = useMemo(() => bucketize(usage?.series, range, now), [usage, range, now]);
  const anyCalls = overall.cached + overall.computed > 0;

  const save = async (changes) => {
    setSaving(true);
    try {
      const res = await setRuntimeCacheSettings(changes);
      setData(res.data || null);
      toast.success(t('localModels.runtimeCache.saved_ok'));
    } catch (err) {
      toast.error(t('localModels.runtimeCache.saveFailed'), errorDetail(err));
    } finally {
      setSaving(false);
    }
  };

  const apply = async () => {
    setApplying(true);
    try {
      const res = await applyRuntimeCache();
      setData(res.data || null);
      const failed = res.data?.failed || [];
      if (failed.length) toast.error(t('localModels.runtimeCache.applyFailed'), failed.map((f) => `${f.name}: ${f.error}`).join('\n'));
      else toast.success(t('localModels.runtimeCache.applied', { count: (res.data?.reloaded || []).length }));
    } catch (err) {
      toast.error(t('localModels.runtimeCache.applyFailed'), errorDetail(err));
    } finally {
      setApplying(false);
    }
  };

  const warm = async (name) => {
    try {
      await warmRuntimeCache(name);
      load();
    } catch (err) {
      toast.error(t('localModels.runtimeCache.warmFailed'), errorDetail(err));
    }
  };

  const clear = async (name) => {
    if (!window.confirm(name ? t('localModels.runtimeCache.confirmClearOne', { name }) : t('localModels.runtimeCache.confirmClearAll'))) return;
    try {
      const res = await clearRuntimeCache(name);
      setData(res.data || null);
    } catch (err) {
      toast.error(t('localModels.runtimeCache.clearFailed'), errorDetail(err));
    }
  };

  if (!usage && !data && !(standalone && ready)) return null;

  const settings = data?.settings;
  const mem = data?.memory || {};
  const loaded = data?.models || [];
  const stored = (data?.stored || []).filter((s) => s.disk_bytes > 0 || (s.heads || []).length > 0);
  const total = mem.limit_bytes || mem.ram_total_bytes;
  const sum = (k) => loaded.reduce((n, m) => n + (m[k] || 0), 0);
  const memParts = [
    { label: t('localModels.runtimeCache.memory.weights'), value: sum('weights_bytes'), color: 'var(--chart-cat-1)' },
    { label: t('localModels.runtimeCache.memory.kv'), value: sum('kv_bytes'), color: 'var(--chart-cat-2)' },
    { label: t('localModels.runtimeCache.memory.ram'), value: sum('ram_cache_bytes'), color: 'var(--chart-cat-3)', opacity: 0.45 },
  ].map((p) => ({ ...p, text: humanBytes(p.value) }));
  const diskLimit = (settings?.disk_mib || 0) * 1024 * 1024;
  const labels = {
    title: t('localModels.runtimeCache.chart.tokensTitle'),
    cached: t('localModels.runtimeCache.chart.cached'),
    computed: t('localModels.runtimeCache.chart.computed'),
    hit: t('localModels.runtimeCache.hitRate'),
    requests: t('localModels.runtimeUsage.requests'),
    locale: language,
  };
  const ttftLabels = {
    title: t('localModels.runtimeCache.chart.ttftTitle'),
    ttft: t('localModels.runtimeCache.ttft'),
    streamed: t('localModels.runtimeCache.chart.streamed'),
    locale: language,
    duration: dur,
  };
  const disabled = settings && !settings.enabled;

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden" data-testid="runtime-cache">
      <div className={`flex items-center gap-2 bg-gray-50 border-gray-100 ${open ? 'border-b' : ''}`}>
        <button
          type="button"
          onClick={() => { if (!standalone) setOpen((v) => !v); }}
          className={`flex-1 min-w-0 flex items-center gap-2 px-4 py-3 text-left transition-colors focus:outline-none flex-wrap ${standalone ? 'cursor-default' : 'hover:bg-gray-100'}`}
          aria-expanded={open}
          data-testid="runtime-cache-toggle"
        >
          {standalone ? null : open ? <ChevronDown className="w-4 h-4 text-gray-500 shrink-0" /> : <ChevronRight className="w-4 h-4 text-gray-500 shrink-0" />}
          <span className="text-sm font-semibold text-gray-800 shrink-0">{t('localModels.runtimeCache.title')}</span>
          <span className="text-xs text-gray-400" data-testid="runtime-cache-summary">
            {disabled ? t('localModels.runtimeCache.off') : anyCalls ? (
              <>
                {t('localModels.runtimeCache.fromCache')}: {fmtPercent(overall.hitRate)}
                {overall.savedMs ? <>{' · '}{t('localModels.runtimeCache.savedShort', { time: dur(overall.savedMs) })}</> : null}
              </>
            ) : t('localModels.runtimeCache.noCalls')}
            {data?.pending?.length > 0 && <span className="text-amber-700">{' · '}{t('localModels.runtimeCache.pendingShort')}</span>}
          </span>
        </button>
        {open && (
          <div className="flex items-center gap-1.5 shrink-0 pr-4">
            <button type="button" onClick={load} disabled={loading} className={btnCls}>
              {loading ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              {t('common.refresh')}
            </button>
          </div>
        )}
      </div>

      {open && (
        <div className="p-3 space-y-5">
          <p className="text-xs text-gray-500">{t('localModels.runtimeCache.intro')}</p>

          {data && data.ok === false && (
            <p className="text-sm text-amber-800 bg-amber-50 rounded-lg px-3 py-2" data-testid="runtime-cache-error">
              {data.outdated ? t('localModels.runtimeCache.outdated') : data.error}
            </p>
          )}

          {data?.pending?.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-sm text-amber-800 bg-amber-50 rounded-lg px-3 py-2" data-testid="runtime-cache-pending">
              <span className="flex-1 min-w-0">{t('localModels.runtimeCache.pending', { models: data.pending.join(', ') })}</span>
              {canEdit && (
                <button type="button" onClick={apply} disabled={applying} className={`${btnCls} bg-white`} data-testid="runtime-cache-apply">
                  {applying ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RotateCw className="w-3.5 h-3.5" />}
                  {t('localModels.runtimeCache.apply')}
                </button>
              )}
            </div>
          )}

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Tile testId="cache-tile-hit" label={t('localModels.runtimeCache.fromCache')} value={fmtPercent(overall.hitRate)}
              sub={anyCalls ? t('localModels.runtimeCache.tokensOf', { cached: fmtCompact(overall.cached), total: fmtCompact(overall.cached + overall.computed) }) : null} />
            <Tile label={t('localModels.runtimeCache.hits')} value={fmtPercent(overall.callHitRate)}
              sub={overall.promptCalls ? t('localModels.runtimeCache.callsOf', { hits: overall.hits, total: overall.promptCalls }) : null} />
            <Tile label={t('localModels.runtimeCache.saved')} value={overall.savedMs ? `≈ ${dur(overall.savedMs)}` : '—'}
              sub={t('localModels.runtimeCache.savedHint')} />
            <Tile label={t('localModels.runtimeCache.ttft')} value={dur(overall.ttft)}
              sub={overall.ttftN ? t('localModels.runtimeCache.ttftOf', { count: overall.ttftN }) : null} />
          </div>

          <Section
            title={t('localModels.runtimeCache.chart.tokensTitle')}
            action={(
              <span className="flex items-center gap-1">
                {Object.keys(RANGES).map((r) => (
                  <button
                    key={r} type="button" onClick={() => setRange(r)}
                    className={`px-2 py-0.5 rounded-full border text-xs ${range === r ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-gray-200 text-gray-600 hover:bg-gray-50'}`}
                    aria-pressed={range === r}
                  >
                    {t(`localModels.runtimeCache.ranges.${r}`)}
                  </button>
                ))}
                <button type="button" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}
                  className={`ml-1 p-1 rounded border ${asTable ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-gray-200 text-gray-500 hover:bg-gray-50'}`}
                  title={t('localModels.runtimeCache.chart.table')} aria-label={t('localModels.runtimeCache.chart.table')} data-testid="cache-table-toggle">
                  <Table2 className="w-3.5 h-3.5" />
                </button>
              </span>
            )}
          >
            {asTable ? (
              <div className="overflow-x-auto border border-gray-100 rounded-lg max-h-64" data-testid="cache-bucket-table">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-gray-400 uppercase tracking-wider border-b border-gray-100 bg-gray-50">
                      <th className="px-2 py-1.5 font-medium">{t('localModels.runtimeCache.chart.time')}</th>
                      <th className="px-2 py-1.5 font-medium text-right">{t('localModels.runtimeUsage.requests')}</th>
                      <th className="px-2 py-1.5 font-medium text-right">{labels.cached}</th>
                      <th className="px-2 py-1.5 font-medium text-right">{labels.computed}</th>
                      <th className="px-2 py-1.5 font-medium text-right">{labels.hit}</th>
                      <th className="px-2 py-1.5 font-medium text-right">{ttftLabels.ttft}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {buckets.filter((b) => b.requests > 0).reverse().map((b) => (
                      <tr key={b.t} className="border-t border-gray-50 tabular-nums">
                        <td className="px-2 py-1">{new Date(b.t * 1000).toLocaleString(language, { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })}</td>
                        <td className="px-2 py-1 text-right">{b.requests}</td>
                        <td className="px-2 py-1 text-right">{b.cached.toLocaleString()}</td>
                        <td className="px-2 py-1 text-right">{b.computed.toLocaleString()}</td>
                        <td className="px-2 py-1 text-right">{fmtPercent(b.hit)}</td>
                        <td className="px-2 py-1 text-right">{dur(b.ttft)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!buckets.some((b) => b.requests > 0) && <p className="text-sm text-gray-400 px-2 py-3">{t('localModels.runtimeCache.noCalls')}</p>}
              </div>
            ) : (
              <div className="space-y-3">
                <Legend items={[
                  { label: labels.cached, color: 'var(--chart-accent)' },
                  { label: labels.computed, color: 'var(--chart-muted)' },
                ]} />
                <TokenColumns buckets={buckets} labels={labels} />
                <p className="text-xs font-medium text-gray-600 pt-1">{ttftLabels.title}</p>
                <TtftLine buckets={buckets} labels={ttftLabels} />
              </div>
            )}
          </Section>

          {models.length > 0 && (
            <Section title={t('localModels.runtimeCache.byModel')}>
              <div className="overflow-x-auto border border-gray-100 rounded-lg">
                <table className="w-full text-sm" data-testid="cache-model-table">
                  <thead>
                    <tr className="text-left text-xs text-gray-400 uppercase tracking-wider border-b border-gray-100 bg-gray-50">
                      <th className="px-2 py-1.5 font-medium">{t('localModels.runtimeUsage.model')}</th>
                      <th className="px-2 py-1.5 font-medium">{t('localModels.runtimeCache.fromCache')}</th>
                      <th className="px-2 py-1.5 font-medium text-right">{labels.cached} / {labels.computed}</th>
                      <th className="px-2 py-1.5 font-medium text-right">{t('localModels.runtimeCache.ttft')}</th>
                      <th className="px-2 py-1.5 font-medium text-right" title={t('localModels.runtimeCache.prefillHint')}>{t('localModels.runtimeCache.prefill')}</th>
                      <th className="px-2 py-1.5 font-medium text-right">{t('localModels.runtimeCache.saved')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {models.map((m) => (
                      <tr key={m.model} className="border-t border-gray-50 hover:bg-gray-50">
                        <td className="px-2 py-1.5 font-mono text-gray-700">{m.model}</td>
                        <td className="px-2 py-1.5"><Meter value={m.hitRate} /></td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{fmtCompact(m.cached)} / {fmtCompact(m.computed)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{dur(m.ttft)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{m.prefillTps ? `${Math.round(m.prefillTps).toLocaleString()} tok/s` : '—'}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{m.savedMs ? `≈ ${dur(m.savedMs)}` : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>
          )}

          {data?.ok && (
            <Section title={t('localModels.runtimeCache.memory.title')}>
              {loaded.length === 0 ? (
                <p className="text-sm text-gray-400">{t('localModels.runtimeCache.memory.nothingLoaded')}</p>
              ) : (
                <div className="space-y-2" data-testid="cache-memory">
                  <PartsBar parts={memParts} total={total} label={t('localModels.runtimeCache.memory.title')} />
                  <Legend items={memParts.map((p) => ({ label: `${p.label}: ${p.text}`, color: p.color, opacity: p.opacity }))} />
                  {total ? (
                    <p className="text-xs text-gray-500">
                      {t(mem.limit_bytes ? 'localModels.runtimeCache.memory.ofLimit' : 'localModels.runtimeCache.memory.ofRam', {
                        total: humanBytes(total), free: humanBytes(mem.ram_available_bytes),
                      })}
                      {mem.unified ? ` ${t('localModels.runtimeCache.memory.unified')}` : ''}
                    </p>
                  ) : null}
                </div>
              )}
              <div className="space-y-1.5 pt-1">
                <div className="flex items-center gap-2 text-xs text-gray-600">
                  <span className="font-medium">{t('localModels.runtimeCache.disk.title')}</span>
                  <span className="tabular-nums">{t('localModels.runtimeCache.disk.used', { used: humanBytes(data.disk_bytes || 0), limit: humanBytes(diskLimit) })}</span>
                  {canEdit && stored.length > 0 && (
                    <button type="button" onClick={() => clear(null)} className={`${btnCls} ml-auto py-1`} data-testid="cache-clear-all">
                      <Trash2 className="w-3.5 h-3.5" />
                      {t('localModels.runtimeCache.clearAll')}
                    </button>
                  )}
                </div>
                <PartsBar parts={[{ label: t('localModels.runtimeCache.disk.title'), value: data.disk_bytes || 0, color: 'var(--chart-accent)', text: humanBytes(data.disk_bytes || 0) }]}
                  total={diskLimit} label={t('localModels.runtimeCache.disk.title')} />
              </div>
            </Section>
          )}

          {(loaded.length > 0 || stored.length > 0) && (
            <Section title={t('localModels.runtimeCache.models.title')}>
              <div className="divide-y divide-gray-100 border border-gray-100 rounded-lg" data-testid="cache-models">
                {loaded.map((m) => (
                  <div key={m.name} className="px-3 py-2 text-xs text-gray-600 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm text-gray-800">{m.name}</span>
                      <span className="px-1.5 py-0.5 rounded bg-green-50 text-green-700">{t('localModels.runtime.loadedBadge')}</span>
                      <span className="text-gray-400">{m.engine}</span>
                      {m.pending && <span className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700">{t('localModels.runtimeCache.pendingShort')}</span>}
                      {canEdit && (
                        <button type="button" onClick={() => warm(m.name)} disabled={m.warmup?.state === 'running' || !m.heads}
                          className={`${btnCls} ml-auto py-1`} title={t('localModels.runtimeCache.models.warmHint')}>
                          <Flame className="w-3.5 h-3.5" />
                          {t('localModels.runtimeCache.models.warm')}
                        </button>
                      )}
                    </div>
                    <p className="tabular-nums">
                      {[
                        m.slots ? t('localModels.runtimeCache.models.slots', { count: m.slots }) : null,
                        m.kv_type ? `KV ${m.kv_type}` : null,
                        m.kv_bytes ? t('localModels.runtimeCache.models.kv', { size: humanBytes(m.kv_bytes), measured: m.bytes_per_token_measured ? '' : '≈ ' }) : null,
                        m.bytes_per_token ? t('localModels.runtimeCache.models.perToken', { size: humanBytes(m.bytes_per_token) }) : null,
                        t('localModels.runtimeCache.models.heads', { count: m.heads }),
                        m.disk_bytes ? t('localModels.runtimeCache.models.onDisk', { size: humanBytes(m.disk_bytes) }) : null,
                      ].filter(Boolean).join(' · ')}
                    </p>
                    {m.restored?.tokens > 0 && (
                      <p>{t('localModels.runtimeCache.models.restored', { tokens: fmtCompact(m.restored.tokens), slots: m.restored.slots, time: dur(m.restored.ms) })}</p>
                    )}
                    {m.warmup?.state && m.warmup.state !== 'idle' && (
                      <p data-testid="cache-warmup-state">
                        {m.warmup.state === 'running'
                          ? t('localModels.runtimeCache.models.warming', { done: m.warmup.done, total: m.warmup.total })
                          : t('localModels.runtimeCache.models.warmed', { done: m.warmup.done, time: dur(m.warmup.ms), cached: fmtPercent(m.warmup.prompt_tokens ? m.warmup.cached_tokens / m.warmup.prompt_tokens : null) })}
                        {m.warmup.errors > 0 && <span className="text-red-600">{' · '}{t('localModels.runtimeCache.models.warmErrors', { count: m.warmup.errors })}</span>}
                      </p>
                    )}
                  </div>
                ))}
                {stored.filter((s) => !s.loaded).map((s) => (
                  <div key={s.name} className="px-3 py-2 text-xs text-gray-600 flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm text-gray-800">{s.name}</span>
                    <span className="text-gray-400">{t('localModels.runtime.notLoaded')}</span>
                    <span className="tabular-nums">
                      {[
                        s.disk_bytes ? t('localModels.runtimeCache.models.onDiskTokens', { size: humanBytes(s.disk_bytes), tokens: fmtCompact(s.tokens) }) : null,
                        t('localModels.runtimeCache.models.heads', { count: (s.heads || []).length }),
                      ].filter(Boolean).join(' · ')}
                    </span>
                    {canEdit && (
                      <button type="button" onClick={() => clear(s.name)} className={`${btnCls} ml-auto py-1`}>
                        <Trash2 className="w-3.5 h-3.5" />
                        {t('localModels.runtimeCache.clear')}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </Section>
          )}

          {data?.ok && (
            <Section title={t('localModels.runtimeCache.settings.title')}>
              <CacheSettings data={data} canEdit={canEdit} saving={saving} onSave={save} />
            </Section>
          )}
        </div>
      )}
    </div>
  );
}
