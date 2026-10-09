import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../../i18n';
import RuntimeCache from '../RuntimeCache';
import { bucketize, modelStats, overallStats, niceMax } from '../cacheStats';

// The prompt cache card on the Local tab: its figures come from the
// runtime's call counts (`usage`, handed down), what it holds and its
// settings from GET /models/local/runtime/cache.

const getRuntimeCache = vi.fn();
const setRuntimeCacheSettings = vi.fn();
const applyRuntimeCache = vi.fn();

vi.mock('../../../api/localModels', () => ({
  getRuntimeCache: (...a) => getRuntimeCache(...a),
  setRuntimeCacheSettings: (...a) => setRuntimeCacheSettings(...a),
  applyRuntimeCache: (...a) => applyRuntimeCache(...a),
  warmRuntimeCache: vi.fn(),
  clearRuntimeCache: vi.fn(),
}));

const NOW = 1_800_000_000;

const USAGE = {
  rows: [
    { model: 'qwen', source: 'hub', kind: 'chat', requests: 4, cached_tokens: 9000, computed_tokens: 1000,
      hits: 3, prompt_calls: 4, prefill_ms: 2000, prefill_tokens: 1000, ttft_ms: 400, ttft_n: 4 },
    { model: 'qwen', source: 'endpoint', kind: 'chat', requests: 1, cached_tokens: 0, computed_tokens: 1000,
      hits: 0, prompt_calls: 1, prefill_ms: 2000, prefill_tokens: 1000, ttft_ms: 2100, ttft_n: 1 },
    { model: 'whisper', source: 'voice', kind: 'transcription', requests: 2 },
  ],
  series: [
    { t: NOW - 600, requests: 2, cached_tokens: 0, computed_tokens: 2000, ttft_ms: 2500, ttft_n: 2 },
    { t: NOW - 300, requests: 3, cached_tokens: 9000, computed_tokens: 0, ttft_ms: 0, ttft_n: 0 },
  ],
};

const CACHE = {
  ok: true,
  settings: { enabled: true, ram_mib: 8192, slots: 0, reuse_tokens: 256, kv_type: 'f16', disk: true, disk_mib: 16384, warmup: true, warmup_prompts: 8 },
  defaults: { enabled: true, ram_mib: 8192, slots: 0, reuse_tokens: 256, kv_type: 'f16', disk: true, disk_mib: 16384, warmup: true, warmup_prompts: 8 },
  kv_types: ['f16', 'q8_0', 'q4_0'],
  limits: { ram_mib: [0, 1048576], slots: [0, 64], reuse_tokens: [0, 65536], disk_mib: [0, 4194304], warmup_prompts: [0, 64] },
  models: [{
    name: 'qwen', file: 'qwen.gguf', engine: 'llama', context_length: 8192, slots: 4, kv_type: 'f16',
    bytes_per_token: 114704, bytes_per_token_measured: true, kv_bytes: 939_656_806, weights_bytes: 639_447_744,
    ram_cache_bytes: 8 * 2 ** 30, restored: { slots: 1, tokens: 3526, ms: 56 },
    warmup: { state: 'done', total: 3, done: 3, errors: 0, prompt_tokens: 10563, cached_tokens: 3515, ms: 1727 },
    heads: 3, pending: false, disk_bytes: 404_447_028,
  }],
  stored: [{ name: 'old-model', loaded: false, disk_bytes: 200_000_000, tokens: 2000, heads: [{ key: 'a' }] }],
  disk_bytes: 604_447_028,
  memory: { ram_total_bytes: 64 * 2 ** 30, ram_available_bytes: 30 * 2 ** 30, limit_bytes: null, unified: true },
  pending: [],
};

function renderCard(usage = USAGE) {
  return render(<I18nProvider><RuntimeCache usage={usage} /></I18nProvider>);
}

describe('cache figures', () => {
  it('adds up chat rows per model and prices the time saved', () => {
    const [qwen] = modelStats(USAGE.rows);
    expect(qwen.model).toBe('qwen');
    expect(qwen.hitRate).toBeCloseTo(9000 / 11000);
    expect(qwen.callHitRate).toBeCloseTo(3 / 5);
    // 4000 ms for 2000 computed tokens: 2 ms a token, 9000 cached tokens.
    expect(qwen.savedMs).toBe(18000);
    expect(qwen.ttft).toBe(500);
    expect(modelStats(USAGE.rows)).toHaveLength(1);
    expect(overallStats(modelStats(USAGE.rows)).savedMs).toBe(18000);
  });

  it('fills the time axis and sums buckets into wider columns', () => {
    const hour = bucketize(USAGE.series, '1h', NOW);
    expect(hour).toHaveLength(12);
    // The last column is the current five minutes, still empty.
    expect(hour.at(-1).requests).toBe(0);
    expect(hour.at(-2).cached).toBe(9000);
    expect(hour.at(-3).ttft).toBe(1250);
    const day = bucketize(USAGE.series, '24h', NOW);
    expect(day).toHaveLength(24);
    expect(day.reduce((n, b) => n + b.cached + b.computed, 0)).toBe(11000);
    expect(niceMax(11000)).toBe(20000);
    expect(niceMax(0)).toBe(1);
  });
});

describe('RuntimeCache', () => {
  beforeEach(() => {
    getRuntimeCache.mockReset().mockResolvedValue({ data: CACHE });
    setRuntimeCacheSettings.mockReset().mockResolvedValue({ data: { ...CACHE, pending: ['qwen'] } });
    applyRuntimeCache.mockReset().mockResolvedValue({ data: { ...CACHE, reloaded: ['qwen'], failed: [] } });
  });

  it('shows the hit share folded and reads the cache only when opened', async () => {
    renderCard();
    expect(screen.getByTestId('runtime-cache-summary')).toHaveTextContent('From cache: 82%');
    expect(getRuntimeCache).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('runtime-cache-toggle'));
    await waitFor(() => expect(screen.getByTestId('cache-models')).toBeInTheDocument());
    expect(screen.getByTestId('cache-tile-hit')).toHaveTextContent('82%');
    expect(screen.getByTestId('cache-model-table')).toHaveTextContent('qwen');
    expect(screen.getByTestId('cache-models')).toHaveTextContent('Restored from disk at load: 3.5K tokens');
    expect(screen.getByTestId('cache-warmup-state')).toHaveTextContent('Warmed up 3 prompts');
    expect(screen.getByTestId('cache-models')).toHaveTextContent('old-model');
  });

  it('stands open on its own tab, even before any call was counted', async () => {
    getRuntimeCache.mockResolvedValue({ data: CACHE });
    render(<I18nProvider><RuntimeCache usage={null} standalone ready /></I18nProvider>);
    expect(screen.getByTestId('runtime-cache-toggle')).toHaveAttribute('aria-expanded', 'true');
    await waitFor(() => expect(getRuntimeCache).toHaveBeenCalled());
    expect(await screen.findByText('What the cache holds')).toBeInTheDocument();
    // Nothing to fold: the header is not a toggle.
    fireEvent.click(screen.getByTestId('runtime-cache-toggle'));
    expect(screen.getByTestId('runtime-cache-toggle')).toHaveAttribute('aria-expanded', 'true');
  });

  it('shows the columns as a table on demand', async () => {
    renderCard();
    fireEvent.click(screen.getByTestId('runtime-cache-toggle'));
    await waitFor(() => expect(getRuntimeCache).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId('cache-table-toggle'));
    expect(screen.getByTestId('cache-bucket-table')).toBeInTheDocument();
  });

  it('saves only the changed settings and offers to reload the models', async () => {
    renderCard();
    fireEvent.click(screen.getByTestId('runtime-cache-toggle'));
    await waitFor(() => expect(screen.getByTestId('cache-settings')).toBeInTheDocument());
    expect(screen.getByTestId('cache-save')).toBeDisabled();
    fireEvent.change(screen.getByTestId('cache-kv-type'), { target: { value: 'q8_0' } });
    fireEvent.change(screen.getByTestId('cache-ram'), { target: { value: '4' } });
    fireEvent.click(screen.getByTestId('cache-save'));
    await waitFor(() => expect(setRuntimeCacheSettings).toHaveBeenCalledWith({ kv_type: 'q8_0', ram_mib: 4096 }));
    const apply = await screen.findByTestId('runtime-cache-apply');
    fireEvent.click(apply);
    await waitFor(() => expect(applyRuntimeCache).toHaveBeenCalled());
  });

  it('says when the runtime is older than the cache', async () => {
    getRuntimeCache.mockResolvedValue({ data: { ok: false, outdated: true, error: 'Not Found' } });
    renderCard();
    fireEvent.click(screen.getByTestId('runtime-cache-toggle'));
    expect(await screen.findByTestId('runtime-cache-error')).toHaveTextContent('Restart it');
    expect(screen.queryByTestId('cache-settings')).toBeNull();
  });
});
