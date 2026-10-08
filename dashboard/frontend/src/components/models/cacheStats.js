/**
 * The prompt cache's figures, worked out of the runtime's call counts
 * (deploy/models/app.py, Usage): per model and in time buckets.
 *
 * Every chat call says how many of its prompt tokens came from the cache
 * (`cached_tokens`) and how many the engine computed (`computed_tokens`);
 * llama-server also says how long computing took (`prefill_ms` over
 * `prefill_tokens`), which prices a cached token in time: the time saved is
 * the cached tokens at the model's own prefill speed, an estimate.
 */

// The chart's ranges: how far back, and how wide one column is. The runtime
// keeps a day in 5 minute buckets; the wider ranges add those up.
export const RANGES = {
  '1h': { seconds: 3600, step: 300 },
  '6h': { seconds: 6 * 3600, step: 900 },
  '24h': { seconds: 24 * 3600, step: 3600 },
};

const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);

/** The runtime's buckets added up into the columns of `range`, oldest first,
 * empty columns included so the time axis has no holes. */
export function bucketize(series, range, nowSeconds = Date.now() / 1000) {
  const { seconds, step } = RANGES[range] || RANGES['24h'];
  const count = Math.round(seconds / step);
  const last = Math.floor(nowSeconds / step) * step;
  const first = last - step * (count - 1);
  const cols = Array.from({ length: count }, (_, i) => ({
    t: first + i * step, requests: 0, cached: 0, computed: 0, ttftMs: 0, ttftN: 0,
  }));
  for (const b of series || []) {
    const i = Math.floor((num(b.t) - first) / step);
    if (i < 0 || i >= count) continue;
    const c = cols[i];
    c.requests += num(b.requests);
    c.cached += num(b.cached_tokens);
    c.computed += num(b.computed_tokens);
    c.ttftMs += num(b.ttft_ms);
    c.ttftN += num(b.ttft_n);
  }
  return cols.map((c) => ({
    ...c,
    ttft: c.ttftN ? c.ttftMs / c.ttftN : null,
    hit: c.cached + c.computed ? c.cached / (c.cached + c.computed) : null,
  }));
}

function derive(s) {
  const prompt = s.cached + s.computed;
  const msPerToken = s.prefillTokens > 0 ? s.prefillMs / s.prefillTokens : null;
  return {
    ...s,
    hitRate: prompt ? s.cached / prompt : null,
    callHitRate: s.promptCalls ? s.hits / s.promptCalls : null,
    ttft: s.ttftN ? s.ttftMs / s.ttftN : null,
    prefillTps: s.prefillMs > 0 ? (s.prefillTokens * 1000) / s.prefillMs : null,
    savedMs: msPerToken !== null ? s.cached * msPerToken : null,
  };
}

const empty = (model) => ({
  model, requests: 0, cached: 0, computed: 0, hits: 0, promptCalls: 0,
  prefillMs: 0, prefillTokens: 0, ttftMs: 0, ttftN: 0,
});

/** Chat calls per model (every caller added up), busiest first. */
export function modelStats(rows) {
  const by = new Map();
  for (const r of rows || []) {
    if (r.kind !== 'chat') continue;
    const s = by.get(r.model) || empty(r.model);
    s.requests += num(r.requests);
    s.cached += num(r.cached_tokens);
    s.computed += num(r.computed_tokens);
    s.hits += num(r.hits);
    s.promptCalls += num(r.prompt_calls);
    s.prefillMs += num(r.prefill_ms);
    s.prefillTokens += num(r.prefill_tokens);
    s.ttftMs += num(r.ttft_ms);
    s.ttftN += num(r.ttft_n);
    by.set(r.model, s);
  }
  return [...by.values()].map(derive).sort((a, b) => b.requests - a.requests);
}

/** All models together; `savedMs` adds up the models that can price it. */
export function overallStats(models) {
  const s = empty('');
  let saved = null;
  for (const m of models) {
    for (const k of ['requests', 'cached', 'computed', 'hits', 'promptCalls', 'prefillMs', 'prefillTokens', 'ttftMs', 'ttftN']) {
      s[k] += m[k];
    }
    if (m.savedMs !== null) saved = (saved || 0) + m.savedMs;
  }
  return { ...derive(s), savedMs: saved };
}

/** A clean axis maximum (1, 2 or 5 times a power of ten) at or above `v`. */
export function niceMax(v) {
  if (!(v > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

export function fmtCompact(n) {
  const v = num(n);
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M`;
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(v >= 1e4 ? 0 : 1)}K`;
  return String(Math.round(v));
}

const UNITS = { ms: 'ms', s: 's', min: 'min', h: 'h' };

/** A duration in the largest unit that keeps it readable; `units` names
 * them in the page's language. */
export function fmtDuration(ms, units = UNITS) {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${Math.round(ms)} ${units.ms}`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} ${units.s}`;
  if (ms < 3_600_000) return `${(ms / 60_000).toFixed(1)} ${units.min}`;
  return `${(ms / 3_600_000).toFixed(1)} ${units.h}`;
}

export const fmtPercent = (r) => (r === null || r === undefined ? '—' : `${Math.round(r * 100)}%`);
