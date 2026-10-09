import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../i18n';
import Models from '../Models';

// Feature 5's Local tab on the Models page: the hub's own model runtime (with
// the import of what Ollama already downloaded), and the hub's served /v1
// endpoint with its usage. The
// backend routes are being built in parallel by other agents, so this test
// mocks the shared axios instance directly (same pattern as
// HealthDiagnostics.test.jsx) rather than the higher-level api/* modules,
// since api/localModels.js and api/serving.js both share the one `api`
// instance from src/api/index.js that Models.jsx's own catalog/usage calls
// also go through.

const ok = (data) => Promise.resolve({ data });

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'default' }),
}));

const get = vi.fn();
const post = vi.fn();
const del = vi.fn();
const put = vi.fn();

vi.mock('axios', () => ({
  default: {
    create: () => ({
      get: (...args) => get(...args),
      post: (...args) => post(...args),
      delete: (...args) => del(...args),
      put: (...args) => put(...args),
      // src/api/index.js is the real module here (localModels.js and
      // serving.js both share its `api` instance), so it needs the shape it
      // registers its auth/401 interceptors on.
      interceptors: {
        request: { use: () => {} },
        response: { use: () => {} },
      },
    }),
  },
}));

const RUNTIME_NOT_CONFIGURED = { configured: false };

const RUNTIME_OK = {
  configured: true,
  ok: true,
  url: 'http://localhost:8001',
  models: [
    {
      name: 'model.gguf', file: 'model.gguf', size_bytes: 5_000_000_000, loaded: false,
      port: null, context_length: null, loaded_at: null, format: 'gguf',
    },
  ],
  memory: { ram_total_bytes: 16_000_000_000, ram_available_bytes: 8_000_000_000, models: {}, gpu: null },
};

const SERVING_INFO = { base_url: 'https://hub.example.com/v1', models: 5, auth: 'api_key' };

const SERVING_USAGE = {
  rows: [
    { provider: 'openai', model: 'gpt-4o', requests: 12, prompt_tokens: 1000, completion_tokens: 500, total_tokens: 1500, errors: 0, last_at: '2026-09-20T00:00:00Z' },
  ],
  totals: { requests: 12, prompt_tokens: 1000, completion_tokens: 500, total_tokens: 1500 },
  recent: [
    { at: '2026-09-20T10:00:00Z', actor_name: 'agent-1', actor_kind: 'agent', provider: 'openai', model: 'gpt-4o', prompt_tokens: 100, completion_tokens: 50, duration_ms: 200, stream: false, status: 'ok' },
  ],
};

const RUNTIME_SPEECH = {
  ...RUNTIME_OK,
  engines: { whisper: true, piper: true, kokoro: false },
  models: [
    ...RUNTIME_OK.models,
    {
      name: 'piper-ru_RU-irina-medium', file: 'piper-ru_RU-irina-medium', format: 'onnx', engine: 'piper',
      kind: 'speech', loadable: true, size_bytes: 63_000_000, loaded: false, voices: [],
    },
    {
      name: 'kokoro-v1.0', file: 'kokoro-v1.0', format: 'onnx', engine: 'kokoro', kind: 'speech',
      loadable: false, size_bytes: 350_000_000, loaded: false, voices: ['af_heart', 'bf_emma'],
      note: 'the kokoro engine is not installed in this runtime',
    },
  ],
};

const HF_PIPER = {
  repo: 'rhasspy/piper-voices',
  files: [],
  packages: [
    { name: 'piper-de_DE-thorsten-medium', engine: 'piper', kind: 'speech', language: 'de_DE', size_bytes: 63_000_000, downloaded: false, files: [] },
    { name: 'piper-ru_RU-irina-medium', engine: 'piper', kind: 'speech', language: 'ru_RU', size_bytes: 63_000_000, downloaded: false, files: [] },
  ],
};

let jobs;
let runtimeState;
let ollamaState;
let catalogProviders;
let localServers;
let lmstudioState;
// The runtime's own call counts; a number is the HTTP status it fails with.
let runtimeUsage;
// The recorded voices the runtime keeps; null: the route fails (no card).
let voicesState;

function installGet() {
  get.mockImplementation((url) => {
    if (url === '/models') return ok({ providers: catalogProviders, global_default: {} });
    if (url === '/models/local/servers') return ok({ servers: localServers });
    if (url === '/models/usage') return ok({ rows: [], totals: {} });
    if (url.startsWith('/workspaces/')) return ok({ global_default: {}, workspace_default: {} });
    if (url === '/models/local/runtime/ollama') return ok(ollamaState);
    if (url === '/models/local/runtime/lmstudio') return ok(lmstudioState);
    if (url === '/models/local/jobs') return ok({ jobs });
    if (url === '/models/local/runtime') {
      // The route reports the counts only for a runtime that answers; a
      // runtime too old to count answers 404 on its own /usage.
      if (!runtimeState.ok) return ok(runtimeState);
      return ok({
        ...runtimeState,
        usage: typeof runtimeUsage === 'number' ? null : runtimeUsage,
        usage_outdated: runtimeUsage === 404,
      });
    }
    if (url === '/models/local/runtime/cache') {
      return ok({ ok: true, settings: { enabled: true }, defaults: {}, kv_types: [], limits: {}, models: [], stored: [],
                  memory: {}, pending: [] });
    }
    if (url === '/models/serving/info') return ok(SERVING_INFO);
    if (url === '/models/serving/usage') return ok(SERVING_USAGE);
    if (url === '/models/local/runtime/hf/files') return ok(HF_PIPER);
    if (url === '/models/local/runtime/voices') {
      return voicesState ? ok({ voices: voicesState }) : Promise.reject(new Error('no voices'));
    }
    return Promise.reject(new Error(`unhandled GET ${url}`));
  });
}

beforeEach(() => {
  jobs = [];
  runtimeState = RUNTIME_NOT_CONFIGURED;
  ollamaState = { found: false, models: [] };
  catalogProviders = {};
  localServers = {};
  lmstudioState = { found: false, models: [] };
  runtimeUsage = 502;
  voicesState = null;
  get.mockReset();
  post.mockReset();
  del.mockReset();
  put.mockReset();
  installGet();

  post.mockImplementation((url) => {
    if (url === '/models/local/runtime/load') return ok({ ok: true, model: {} });
    if (url === '/models/local/runtime/ollama/import') return ok({ ok: true, file: 'gpt-oss-20b.gguf', linked: true, job_id: null });
    if (url === '/models/local/runtime/lmstudio/import') return ok({ ok: true, file: 'gpt-oss-20b-MXFP4-Q8', linked: true, job_id: null });
    if (url === '/models/local/runtime/download') return ok({ job_id: 'job-2' });
    if (url.startsWith('/models/local/runtime/engines/')) return ok({ job_id: 'job-3' });
    if (url === '/models/local/runtime/start') return ok({ managed: true, state: 'starting' });
    if (url === '/models/local/runtime/stop') return ok({ managed: true, state: 'stopped' });
    if (url === '/models/local/runtime/restart') return ok({ managed: true, state: 'starting' });
    return Promise.reject(new Error(`unhandled POST ${url}`));
  });

  del.mockImplementation((url) => {
    if (url.startsWith('/models/local/ollama/')) return ok({ ok: true });
    return Promise.reject(new Error(`unhandled DELETE ${url}`));
  });
});

function renderModels() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <Models />
      </MemoryRouter>
    </I18nProvider>,
  );
}

async function openLocalTab() {
  const view = renderModels();
  fireEvent.click(screen.getByRole('button', { name: /Local/ }));
  await screen.findByText('Hub model runtime');
  return view;
}

describe('Models, Prompt cache tab', () => {
  it('shows the cache open on its own tab and not on the Local tab', async () => {
    runtimeState = RUNTIME_OK;
    runtimeUsage = { rows: [], series: [] };
    await openLocalTab();
    expect(screen.queryByTestId('runtime-cache')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Prompt cache/ }));
    const card = await screen.findByTestId('runtime-cache');
    expect(screen.getByTestId('runtime-cache-toggle')).toHaveAttribute('aria-expanded', 'true');
    await waitFor(() => expect(get.mock.calls.some(([url]) => url === '/models/local/runtime/cache')).toBe(true));
    expect(card).toHaveTextContent('Local models compute only the part of a prompt');
    expect(screen.queryByText('Hub model runtime')).toBeNull();
  });
});

describe('Models, Local tab', () => {



  it('shows the not-configured explainer when the hub runtime has no url set', async () => {
    await openLocalTab();
    expect(screen.getByText(/does not run a model runtime here/)).toBeInTheDocument();
  });

  it('loads a hub runtime model with the default context length and gpu layers', async () => {
    runtimeState = RUNTIME_OK;
    await openLocalTab();
    await screen.findByText('model.gguf');
    // The icon-only toggle button carries "Load" as its title; the inline
    // form's own submit button carries it as visible text, so the two are
    // queried differently to stay unambiguous.
    fireEvent.click(screen.getByTitle('Load'));
    fireEvent.click(screen.getByText('Load'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/models/local/runtime/load', {
      file: 'model.gguf', context_length: 4096, gpu_layers: -1,
    }));
  });

  it('offers to resume a job the service closed as resumable', async () => {
    jobs = [{
      id: 'job-9', kind: 'hf_download', name: 'org/r/m.gguf', status: 'error', resumable: true,
      completed: 1000, total: 5000, percent: 20, message: '', error: 'interrupted by a restart',
      meta: { repo: 'org/r', file: 'm.gguf', revision: 'main', dest: 'm.gguf' },
    }];
    await openLocalTab();
    // The downloads block starts folded: one header line with what is in
    // progress and what failed.
    expect(await screen.findByText('Nothing in progress')).toBeInTheDocument();
    expect(screen.getByText('1 failed')).toBeInTheDocument();
    expect(screen.queryByText('Resume')).toBeNull();
    fireEvent.click(screen.getByTestId('local-jobs-toggle'));
    fireEvent.click(await screen.findByText('Resume'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/models/local/runtime/download',
      { repo: 'org/r', file: 'm.gguf', revision: 'main' }));
  });

  it('folds the downloads to a header with how many run and one bar', async () => {
    jobs = [
      { id: 'a', kind: 'hf_download', name: 'org/r/a.gguf', status: 'running', completed: 100, total: 400 },
      { id: 'b', kind: 'hf_download', name: 'org/r/b.gguf', status: 'running', completed: 300, total: 400 },
      { id: 'c', kind: 'hf_download', name: 'org/r/c.gguf', status: 'done', completed: 10, total: 10 },
    ];
    await openLocalTab();
    expect(await screen.findByText('Downloads')).toBeInTheDocument();
    expect(screen.getByText('2 in progress')).toBeInTheDocument();
    expect(screen.getByTestId('local-jobs-overall')).toHaveTextContent('50%');
    expect(screen.queryByText('org/r/a.gguf')).toBeNull();
    fireEvent.click(screen.getByTestId('local-jobs-toggle'));
    expect(screen.getByText('org/r/a.gguf')).toBeInTheDocument();
  });

  it('lists the newest download first', async () => {
    jobs = [
      { id: 'old', kind: 'hf_download', name: 'org/r/old.gguf', status: 'done', started_at: '2026-10-01T10:00:00+00:00' },
      { id: 'new', kind: 'hf_download', name: 'org/r/new.gguf', status: 'running', started_at: '2026-10-06T10:00:00+00:00' },
      { id: 'mid', kind: 'hf_download', name: 'org/r/mid.gguf', status: 'done', started_at: '2026-10-03T10:00:00+00:00' },
    ];
    await openLocalTab();
    fireEvent.click(await screen.findByTestId('local-jobs-toggle'));
    const names = screen.getAllByText(/^org\/r\/\w+\.gguf$/).map((el) => el.textContent);
    expect(names).toEqual(['org/r/new.gguf', 'org/r/mid.gguf', 'org/r/old.gguf']);
  });

  it('shows the serving base url, usage rows and totals on a tab of its own', async () => {
    await openLocalTab();
    expect(screen.queryByTestId('serving-section')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Endpoint \/v1/ }));
    await screen.findByText('https://hub.example.com/v1');
    expect(screen.queryByText('Hub model runtime')).toBeNull();
    // Nothing folds on its own tab: statistics first, then how to connect.
    const stats = screen.getByTestId('serving-stats');
    const howTo = screen.getByTestId('serving-howto');
    expect(stats.compareDocumentPosition(howTo) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(howTo).toHaveTextContent(/curl https:\/\/hub\.example\.com/);
    // "gpt-4o" and the request count "12" each appear twice: once in the
    // totals/usage table, once more in the recent-calls list or row itself.
    expect(screen.getAllByText('gpt-4o').length).toBeGreaterThan(0);
    expect(screen.getAllByText('12').length).toBeGreaterThan(0);
  });
});

describe('Models, Local tab, Hugging Face search', () => {
  const HW = { kind: 'apple', name: 'Apple M1 Max, 32-core GPU', ram_bytes: 68_719_476_736, gpu_bytes: 51_539_607_552, bandwidth_gbps: 400, known: true, calibrated: true };
  const est = (quant, verdict, tps) => ({ quant, verdict, tokens_per_second: tps, need_bytes: 1, budget_bytes: 2, size_bytes: 1 });
  const RESULTS = {
    hardware: HW,
    results: [{
      repo: 'unsloth/Qwen3-30B-A3B-GGUF', params: 30.5e9, moe: true, license: 'apache-2.0', context_length: 262144,
      downloads: 1_200_000, likes: 900, gated: false,
      estimates: [est('Q4_K_M', 'fits', 60), est('Q8_0', 'tight', 32)],
      best: est('Q8_0', 'tight', 32),
    }],
  };
  const FILES = {
    repo: 'unsloth/Qwen3-30B-A3B-GGUF', files: [
      { file: 'BF16/Qwen3-30B-A3B-BF16-00001-of-00002.gguf', size_bytes: 49_700_000_000, fit: est('', 'no', null) },
      { file: 'Qwen3-30B-A3B-Q4_K_M.gguf', size_bytes: 18_600_000_000, fit: est('', 'fits', 60) },
      { file: 'Qwen3-30B-A3B-Q8_0.gguf', size_bytes: 32_500_000_000, fit: est('', 'tight', 32) },
    ],
    packages: [], model: { params: 30.5e9, moe: true, license: 'apache-2.0', context_length: 262144 }, hardware: HW,
  };

  it('finds models by text and filters, shows what fits, and picks one into the download form', async () => {
    runtimeState = RUNTIME_OK;
    const base = get.getMockImplementation();
    get.mockImplementation((url, ...rest) => {
      if (url === '/models/local/runtime/hf/search') return ok(RESULTS);
      if (url === '/models/local/runtime/hf/files') return ok(FILES);
      return base(url, ...rest);
    });
    await openLocalTab();
    fireEvent.change(await screen.findByTestId('model-search-q'), { target: { value: 'qwen3' } });
    fireEvent.change(screen.getByTestId('model-search-license'), { target: { value: 'permissive' } });
    fireEvent.click(screen.getByTestId('model-search-submit'));
    const row = await screen.findByTestId('model-result-unsloth/Qwen3-30B-A3B-GGUF');
    const call = get.mock.calls.find(([url]) => url === '/models/local/runtime/hf/search');
    expect(call[1].params).toEqual({ q: 'qwen3', purpose: 'chat', license: 'permissive', sort: 'downloads' });
    expect(row).toHaveTextContent('30.5B');
    expect(row).toHaveTextContent('MoE');
    expect(row).toHaveTextContent('apache-2.0');
    expect(screen.getByTestId('model-best-unsloth/Qwen3-30B-A3B-GGUF')).toHaveTextContent('Q8_0Tight· ~32 tok/s');
    expect(screen.getByTestId('hardware-line')).toHaveTextContent('Apple M1 Max');
    expect(screen.getByTestId('hardware-line')).toHaveTextContent('measurements on this machine');

    fireEvent.click(screen.getByTestId('model-pick-unsloth/Qwen3-30B-A3B-GGUF'));
    expect(screen.getByPlaceholderText('Repo, e.g. TheBloke/Llama-3-8B-GGUF')).toHaveValue('unsloth/Qwen3-30B-A3B-GGUF');
    // Offered first: the largest file that fits with room, not the list's first.
    expect(await screen.findByTestId('selected-file-fit')).toHaveTextContent('Fits· ~60 tok/s');
    expect(screen.getByDisplayValue(/Qwen3-30B-A3B-Q4_K_M.gguf/)).toBeInTheDocument();
    expect(screen.getByText(/Qwen3-30B-A3B-Q8_0.gguf .* · Tight · ~32 tok\/s/)).toBeInTheDocument();
    expect(screen.getByTestId('hf-model-info')).toHaveTextContent('30.5B · MoE · apache-2.0 · context 256k');
    // Said once, above the results, not again under the files.
    expect(screen.getAllByTestId('hardware-line')).toHaveLength(1);

    // Clear empties the form and what it listed.
    fireEvent.click(screen.getByTestId('hf-clear'));
    expect(screen.getByPlaceholderText('Repo, e.g. TheBloke/Llama-3-8B-GGUF')).toHaveValue('');
    expect(screen.queryByTestId('selected-file-fit')).toBeNull();
    expect(screen.queryByTestId('hf-model-info')).toBeNull();
    expect(screen.getByTestId('hf-clear')).toBeDisabled();
  });
});

describe('Models, Local tab, speech models from the search', () => {
  it('finds voices by purpose, shows their engine and packages, and opens them in the package list', async () => {
    runtimeState = RUNTIME_SPEECH;
    const base = get.getMockImplementation();
    get.mockImplementation((url, ...rest) => (url === '/models/local/runtime/hf/search'
      ? ok({
        hardware: { kind: 'apple', name: 'Apple M1 Max', ram_bytes: 1, gpu_bytes: 1, bandwidth_gbps: 400, known: true },
        results: [{ repo: 'rhasspy/piper-voices', engine: 'piper', kind: 'speech', packages: 177, size_min: 20_000_000, size_max: 120_000_000, license: 'mit', downloads: 0, likes: 900 }],
      })
      : base(url, ...rest)));
    await openLocalTab();
    fireEvent.change(await screen.findByTestId('model-search-purpose'), { target: { value: 'speech' } });
    fireEvent.click(screen.getByTestId('model-search-submit'));
    const row = await screen.findByTestId('model-result-rhasspy/piper-voices');
    const call = get.mock.calls.find(([url]) => url === '/models/local/runtime/hf/search');
    expect(call[1].params.purpose).toBe('speech');
    expect(row).toHaveTextContent('Speech');
    expect(row).toHaveTextContent('Piper');
    expect(row).toHaveTextContent('177 packages, 19 MB to 114 MB');
    // No estimates for speech models, so nothing for the hardware line to explain.
    expect(screen.queryByTestId('hardware-line')).toBeNull();
    fireEvent.click(screen.getByTestId('model-pick-rhasspy/piper-voices'));
    expect(await screen.findByTestId('speech-packages')).toBeInTheDocument();
  });

  it('clears the search text and its results, keeping the filters', async () => {
    runtimeState = RUNTIME_SPEECH;
    const base = get.getMockImplementation();
    get.mockImplementation((url, ...rest) => (url === '/models/local/runtime/hf/search'
      ? ok({ results: [{ repo: 'rhasspy/piper-voices', engine: 'piper', kind: 'speech', packages: 1, downloads: 0, likes: 0 }] })
      : base(url, ...rest)));
    await openLocalTab();
    const clear = await screen.findByTestId('model-search-clear');
    expect(clear).toBeDisabled();
    fireEvent.change(screen.getByTestId('model-search-purpose'), { target: { value: 'speech' } });
    fireEvent.change(screen.getByTestId('model-search-q'), { target: { value: 'piper' } });
    expect(clear).toBeEnabled();
    fireEvent.click(screen.getByTestId('model-search-submit'));
    await screen.findByTestId('model-result-rhasspy/piper-voices');
    fireEvent.click(clear);
    expect(screen.getByTestId('model-search-q')).toHaveValue('');
    expect(screen.queryByTestId('model-result-rhasspy/piper-voices')).toBeNull();
    expect(screen.getByTestId('model-search-purpose')).toHaveValue('speech');
    expect(clear).toBeDisabled();
  });
});

describe('Models, Local tab, runtime load', () => {
  const USAGE = {
    since: '2026-10-01T00:00:00+00:00',
    totals: { requests: 7, errors: 1, prompt_tokens: 900, completion_tokens: 300, duration_ms: 7000 },
    rows: [
      { model: 'qwen3-8b', source: 'hub', kind: 'chat', requests: 4, errors: 0, prompt_tokens: 800, completion_tokens: 250, duration_ms: 4000 },
      { model: 'qwen3-8b', source: 'endpoint', kind: 'chat', requests: 2, errors: 1, prompt_tokens: 100, completion_tokens: 50, duration_ms: 2000 },
      { model: 'whisper-small', source: 'voice', kind: 'transcription', requests: 1, errors: 0, prompt_tokens: 0, completion_tokens: 0, duration_ms: 1000 },
    ],
    recent: [{ at: '2026-10-06T10:00:00+00:00', model: 'whisper-small', source: 'voice', kind: 'transcription', status: 'ok', code: 200, duration_ms: 1000 }],
  };

  it('counts every caller of the runtime, split by source', async () => {
    runtimeState = RUNTIME_OK;
    runtimeUsage = USAGE;
    await openLocalTab();
    const card = await screen.findByTestId('runtime-usage');
    expect(card).toHaveTextContent('Runtime load');
    expect(screen.getByTestId('runtime-usage-summary')).toHaveTextContent('Calls: 7 · Errors: 1');
    expect(screen.queryByTestId('runtime-usage-sources')).toBeNull();
    fireEvent.click(screen.getByTestId('runtime-usage-toggle'));
    const sources = screen.getByTestId('runtime-usage-sources');
    expect(sources).toHaveTextContent('Hub agents4');
    expect(sources).toHaveTextContent('Endpoint /v12');
    expect(sources).toHaveTextContent('Assistant voice1');
    expect(card).toHaveTextContent('Transcription');
  });

  it('resets the counts after asking', async () => {
    runtimeState = RUNTIME_OK;
    runtimeUsage = USAGE;
    del.mockImplementation(() => ok({ since: '2026-10-06T00:00:00+00:00', totals: {}, rows: [], recent: [] }));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await openLocalTab();
    fireEvent.click(await screen.findByTestId('runtime-usage-toggle'));
    fireEvent.click(screen.getByTestId('runtime-usage-reset'));
    await waitFor(() => expect(del.mock.calls.some(([url]) => url === '/models/local/runtime/usage')).toBe(true));
    expect(await screen.findByText('No calls yet.')).toBeInTheDocument();
    confirm.mockRestore();
  });

  it('asks for a restart when the runtime is too old to count', async () => {
    runtimeState = RUNTIME_OK;
    runtimeUsage = 404;
    await openLocalTab();
    expect(await screen.findByText('Needs a restart')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('runtime-usage-toggle'));
    expect(screen.getByTestId('runtime-usage-outdated')).toBeInTheDocument();
  });

  it('shows nothing while the runtime is unreachable', async () => {
    await openLocalTab();
    await waitFor(() => expect(get.mock.calls.some(([url]) => url === '/models/local/runtime')).toBe(true));
    expect(screen.queryByTestId('runtime-usage')).toBeNull();
    expect(get.mock.calls.some(([url]) => url === '/models/local/runtime/usage')).toBe(false);
  });
});

describe('Models, Local tab, speech models', () => {
  it('lists speech models with their kind and loads one in one click', async () => {
    runtimeState = RUNTIME_SPEECH;
    await openLocalTab();
    const row = await screen.findByTestId('runtime-model-piper-ru_RU-irina-medium');
    expect(row).toHaveTextContent('Speech');
    expect(row).not.toHaveTextContent('Structure');
    expect(screen.getByTestId('runtime-model-kokoro-v1.0')).toHaveTextContent('Voices: 2');
    expect(screen.getByTestId('runtime-load-kokoro-v1.0')).toBeDisabled();
    fireEvent.click(screen.getByTestId('runtime-load-piper-ru_RU-irina-medium'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/models/local/runtime/load', {
      file: 'piper-ru_RU-irina-medium', context_length: 4096, gpu_layers: -1,
    }));
  });

  it('installs a missing engine', async () => {
    runtimeState = RUNTIME_SPEECH;
    await openLocalTab();
    const row = await screen.findByTestId('speech-engines');
    expect(row).toHaveTextContent('Whisper');
    fireEvent.click(screen.getByTestId('install-engine-kokoro'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/models/local/runtime/engines/kokoro/install'));
    expect(screen.queryByTestId('install-engine-piper')).toBeNull();
  });

  it('a preset lists its repo, picks its package and downloads it', async () => {
    runtimeState = RUNTIME_SPEECH;
    await openLocalTab();
    fireEvent.click(await screen.findByText('Piper, Russian (Irina)'));
    await waitFor(() => expect(get).toHaveBeenCalledWith('/models/local/runtime/hf/files',
      { params: { repo: 'rhasspy/piper-voices' } }));
    const select = await screen.findByTestId('speech-package-select');
    expect(select).toHaveValue('piper-ru_RU-irina-medium');
    fireEvent.click(screen.getByTestId('speech-package-download'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/models/local/runtime/download',
      { repo: 'rhasspy/piper-voices', package: 'piper-ru_RU-irina-medium' }));
  });

  it('clearing the repo drops the listed packages, the filter and the pick', async () => {
    runtimeState = RUNTIME_SPEECH;
    // Enough packages for the filter field to show.
    const many = Array.from({ length: 13 }, (_, i) => ({ ...HF_PIPER.packages[0], name: `piper-x-${i}` }));
    const base = get.getMockImplementation();
    get.mockImplementation((url, ...rest) => (url === '/models/local/runtime/hf/files'
      ? ok({ ...HF_PIPER, packages: many }) : base(url, ...rest)));
    await openLocalTab();
    fireEvent.click(await screen.findByText('Piper, Russian (Irina)'));
    const filter = await screen.findByPlaceholderText('Filter, e.g. ru_RU');
    fireEvent.change(filter, { target: { value: 'x-1' } });
    const repo = screen.getByPlaceholderText('Repo, e.g. TheBloke/Llama-3-8B-GGUF');
    expect(repo).toHaveValue('rhasspy/piper-voices');
    fireEvent.change(repo, { target: { value: '' } });
    expect(screen.queryByTestId('speech-packages')).toBeNull();
    expect(screen.queryByPlaceholderText('Filter, e.g. ru_RU')).toBeNull();
    // Listed again, the filter starts empty.
    fireEvent.click(screen.getByText('Piper, Russian (Irina)'));
    expect(await screen.findByPlaceholderText('Filter, e.g. ru_RU')).toHaveValue('');
  });

  it('resumes a speech model download by its package', async () => {
    jobs = [{
      id: 'job-8', kind: 'hf_package', name: 'rhasspy/piper-voices: piper-x', status: 'error', resumable: true,
      completed: 10, total: 50, percent: 20, message: '', error: 'interrupted by a restart',
      meta: { repo: 'rhasspy/piper-voices', revision: 'main', dest: 'piper-x' },
    }];
    await openLocalTab();
    fireEvent.click(await screen.findByTestId('local-jobs-toggle'));
    expect(screen.getByText('Speech model download')).toBeInTheDocument();
    fireEvent.click(await screen.findByText('Resume'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/models/local/runtime/download',
      { repo: 'rhasspy/piper-voices', package: 'piper-x', revision: 'main' }));
  });
});

describe('Models, Local tab, the runtime the hub runs itself', () => {
  const managed = (state, extra = {}) => ({
    configured: true, ok: false, url: 'http://127.0.0.1:8200', models: [], memory: null,
    managed: { managed: true, state, message: '', stale: false, log_tail: '', ...extra },
  });

  it('has no Ollama section of its own', async () => {
    runtimeState = { ...RUNTIME_OK, managed: { managed: true, state: 'running' } };
    await openLocalTab();
    expect(screen.queryByText('External servers (optional)')).toBeNull();
    expect(get).not.toHaveBeenCalledWith('/models/local/ollama');
  });

  it('shows the first start setting up its environment', async () => {
    runtimeState = managed('preparing', { message: 'installing the runtime\'s packages', log_tail: 'Collecting fastapi' });
    await openLocalTab();
    expect(await screen.findByText('Setting up the model runtime…')).toBeInTheDocument();
    expect(screen.getByText(/about a minute/)).toBeInTheDocument();
    expect(screen.getByText('Runtime log')).toBeInTheDocument();
  });

  it('starts a stopped runtime from the page', async () => {
    runtimeState = managed('stopped');
    await openLocalTab();
    fireEvent.click(await screen.findByTestId('runtime-start'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/models/local/runtime/start'));
  });

  it('shows why a start failed and offers to try again', async () => {
    runtimeState = managed('failed', { message: 'the runtime exited with code 1', log_tail: 'Traceback' });
    await openLocalTab();
    expect(await screen.findByText(/did not start: the runtime exited with code 1/)).toBeInTheDocument();
    expect(screen.getByText('Traceback')).toBeInTheDocument();
    expect(screen.getByTestId('runtime-start')).toBeInTheDocument();
  });

  it('restarts and stops a running runtime, and says when its code changed', async () => {
    runtimeState = { ...RUNTIME_OK, managed: { managed: true, state: 'running', stale: true } };
    await openLocalTab();
    expect(await screen.findByTestId('runtime-stale')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('runtime-restart'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/models/local/runtime/restart'));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.click(screen.getByTestId('runtime-stop'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/models/local/runtime/stop'));
  });
});

describe('Models, Local tab, import from Ollama and LM Studio', () => {
  const OLLAMA = {
    dir: '/Users/me/.ollama/models',
    found: true,
    models: [
      { name: 'qwen3:30b', file: 'qwen3-30b.gguf', size_bytes: 18_500_000_000, family: 'qwen3moe', parameter_size: '30.5B', quantization: 'Q4_K_M', imported: false, compatible: true, note: null },
      { name: 'gpt-oss:20b', file: 'gpt-oss-20b.gguf', size_bytes: 13_800_000_000, family: 'gptoss', parameter_size: '20.9B', quantization: 'MXFP4', imported: false, compatible: false, note: "made for Ollama's own engine (architecture gptoss)" },
      { name: 'llama3.1:8b', file: 'llama3.1-8b.gguf', size_bytes: 4_900_000_000, family: 'llama', parameter_size: '8.0B', quantization: 'Q4_K_M', imported: true, note: null },
      { name: 'nomic-embed-text:latest', file: 'nomic-embed-text.gguf', size_bytes: 270_000_000, family: 'nomic-bert', imported: false, note: 'an embedding model: it makes vectors, not answers' },
    ],
  };

  it('lists what Ollama has on disk and imports one', async () => {
    runtimeState = { ...RUNTIME_OK, managed: { managed: true, state: 'running' } };
    ollamaState = OLLAMA;
    await openLocalTab();
    const toggle = await screen.findByTestId('model-import-toggle');
    expect(toggle).toHaveTextContent('Ollama: 4 · 2 not imported');
    fireEvent.click(toggle);
    expect(screen.getByTestId('ollama-model-llama3.1:8b')).toHaveTextContent('imported');
    expect(screen.getByTestId('ollama-model-nomic-embed-text:latest')).toHaveTextContent('embedding model');
    // A file only Ollama's own engine reads is shown, but cannot be imported.
    expect(screen.getByTestId('ollama-model-gpt-oss:20b')).toHaveTextContent('Ollama only');
    expect(screen.queryByTestId('ollama-import-gpt-oss:20b')).toBeNull();
    fireEvent.click(screen.getByTestId('ollama-import-qwen3:30b'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/models/local/runtime/ollama/import', { name: 'qwen3:30b' }));
  });

  it('shows nothing when there is no Ollama folder', async () => {
    runtimeState = { ...RUNTIME_OK, managed: { managed: true, state: 'running' } };
    await openLocalTab();
    await screen.findByText('model.gguf');
    expect(screen.queryByTestId('model-import')).toBeNull();
  });

  it('imports an MLX model from LM Studio', async () => {
    runtimeState = { ...RUNTIME_OK, managed: { managed: true, state: 'running' } };
    ollamaState = OLLAMA;
    lmstudioState = {
      dir: '/Users/me/.lmstudio/models',
      found: true,
      models: [
        { name: 'mlx-community/gpt-oss-20b-MXFP4-Q8', file: 'gpt-oss-20b-MXFP4-Q8', format: 'mlx', family: 'gpt_oss', quantization: '4-bit', size_bytes: 12_100_000_000, imported: false, compatible: true, note: null },
      ],
    };
    await openLocalTab();
    const toggle = await screen.findByTestId('model-import-toggle');
    expect(toggle).toHaveTextContent('Ollama: 4, LM Studio: 1 · 3 not imported');
    fireEvent.click(toggle);
    expect(screen.getByTestId('lmstudio-model-mlx-community/gpt-oss-20b-MXFP4-Q8')).toHaveTextContent('MLX');
    fireEvent.click(screen.getByTestId('lmstudio-import-mlx-community/gpt-oss-20b-MXFP4-Q8'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/models/local/runtime/lmstudio/import',
      { name: 'mlx-community/gpt-oss-20b-MXFP4-Q8' }));
  });
});

describe('Models, catalog markers for local servers', () => {
  it('says which local servers are running', async () => {
    catalogProviders = {
      openai: { default: '', models: [{ id: 'gpt-4o', enabled: true }] },
      ollama: { default: '', models: [{ id: 'llama3', enabled: true }] },
      lmstudio: { default: '', models: [{ id: 'qwen', enabled: false }] },
    };
    localServers = {
      ollama: { ok: true, url: 'http://localhost:11434' },
      lmstudio: { ok: false, url: 'http://localhost:1234', error: 'ConnectError' },
    };
    renderModels();
    const markers = await screen.findAllByTestId('server-status');
    expect(markers.map((m) => m.textContent)).toEqual(['running', 'not running']);
    expect(markers[1]).toHaveAttribute('title', 'http://localhost:1234 (ConnectError)');
  });
});

describe('Models, Local tab, recorded voices', () => {
  const RUNTIME_CLONING = {
    ...RUNTIME_SPEECH,
    engines: { ...RUNTIME_SPEECH.engines, chatterbox: true, openvoice: true },
    models: [
      ...RUNTIME_SPEECH.models,
      {
        name: 'chatterbox-multilingual', file: 'chatterbox-multilingual', format: 'torch', engine: 'chatterbox',
        kind: 'speech', loadable: true, size_bytes: 3_200_000_000, loaded: false, voices: ['default', 'anna'],
      },
      {
        name: 'OpenVoiceV2-converter', file: 'OpenVoiceV2-converter', format: 'torch', engine: 'openvoice',
        kind: 'speech', loadable: true, size_bytes: 131_000_000, loaded: false, voices: ['anna'],
      },
    ],
  };

  beforeEach(() => {
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:x');
    globalThis.URL.revokeObjectURL = vi.fn();
    window.HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
    window.HTMLMediaElement.prototype.pause = vi.fn();
  });

  it('offers the MLX preset only where the runtime runs MLX', async () => {
    runtimeState = RUNTIME_CLONING;
    await openLocalTab();
    const presets = await screen.findByTestId('speech-presets');
    expect(presets).toHaveTextContent('Chatterbox, your voice');
    expect(presets).not.toHaveTextContent('Chatterbox MLX');
  });

  it('offers the MLX preset on Apple silicon', async () => {
    runtimeState = { ...RUNTIME_CLONING, engines: { ...RUNTIME_CLONING.engines, chatterbox_mlx: false } };
    await openLocalTab();
    expect(await screen.findByTestId('speech-presets')).toHaveTextContent('Chatterbox MLX');
  });

  it('offers the Qwen3-TTS presets where the runtime lists that engine', async () => {
    runtimeState = RUNTIME_CLONING;
    await openLocalTab();
    expect(await screen.findByTestId('speech-presets')).not.toHaveTextContent('Qwen3-TTS');
    cleanup();
    runtimeState = { ...RUNTIME_CLONING, engines: { ...RUNTIME_CLONING.engines, qwen3_tts: false } };
    await openLocalTab();
    const presets = await screen.findByTestId('speech-presets');
    expect(presets).toHaveTextContent('Qwen3-TTS 1.7B, your voice, 10 languages');
    expect(presets).toHaveTextContent('Qwen3-TTS 0.6B');
  });

  it('has no card while the runtime keeps no voices list', async () => {
    runtimeState = RUNTIME_CLONING;
    await openLocalTab();
    await screen.findByTestId('runtime-model-chatterbox-multilingual');
    expect(screen.queryByTestId('voices-card')).toBeNull();
  });

  it('lists the voices and reads a line in one with each cloning model', async () => {
    runtimeState = RUNTIME_CLONING;
    voicesState = [
      { name: 'anna', language: 'ru', gender: 'female', duration: 14.2, mine: true, editable: true, shared: false },
      { name: 'team', mine: false, editable: false, shared: true },
    ];
    post.mockImplementation((url) => {
      if (url.endsWith('/try')) return Promise.resolve({ data: new Blob(['mp3']), headers: { 'x-synthesis-seconds': '2.5' } });
      return Promise.reject(new Error(`unhandled POST ${url}`));
    });
    await openLocalTab();
    const list = await screen.findByTestId('voices-list');
    expect(list).toHaveTextContent('anna');
    expect(list).toHaveTextContent('yours');
    expect(list).toHaveTextContent('shared');
    expect(screen.queryByTestId('voices-no-models')).toBeNull();
    fireEvent.click(screen.getAllByTestId('voice-try-chatterbox')[0]);
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      '/models/local/runtime/voices/anna/try',
      { model: 'chatterbox-multilingual', language: 'en', text: '' },
      { responseType: 'blob', timeout: 600000 },
    ));
    // Only the owner's row can be changed.
    expect(screen.getAllByText('Reader for OpenVoice')).toHaveLength(1);
  });

  it('names the model on the try button when an engine has several', async () => {
    runtimeState = {
      ...RUNTIME_CLONING,
      engines: { ...RUNTIME_CLONING.engines, chatterbox_mlx: true },
      models: [
        ...RUNTIME_CLONING.models,
        ...['chatterbox-4bit-mlx', 'chatterbox-8bit-mlx'].map((name) => ({
          name, file: name, format: 'mlx', engine: 'chatterbox_mlx', kind: 'speech', loadable: true,
          size_bytes: 1_100_000_000, loaded: false, voices: ['default', 'anna'],
        })),
      ],
    };
    voicesState = [{ name: 'anna', language: 'ru', duration: 14.2, mine: true, editable: true, shared: false }];
    await openLocalTab();
    await screen.findByTestId('voices-list');
    // One model of an engine: the engine's name; two: each model's own.
    expect(screen.getByTestId('voice-try-chatterbox')).toHaveTextContent('Chatterbox');
    expect(screen.getByTestId('voice-try-openvoice')).toHaveTextContent('OpenVoice');
    const mlx = screen.getAllByTestId('voice-try-chatterbox_mlx').map((b) => b.textContent);
    expect(mlx).toEqual(['chatterbox-4bit-mlx', 'chatterbox-8bit-mlx']);
  });

  it('saves a recording from a file only after the consent box', async () => {
    runtimeState = RUNTIME_CLONING;
    voicesState = [];
    post.mockImplementation((url) => (url === '/models/local/runtime/voices'
      ? ok({ name: 'boris' }) : Promise.reject(new Error(`unhandled POST ${url}`))));
    await openLocalTab();
    fireEvent.click(await screen.findByTestId('voice-add'));
    fireEvent.change(screen.getByTestId('voice-name'), { target: { value: 'boris' } });
    const file = new File(['RIFF'], 'boris.wav', { type: 'audio/wav' });
    fireEvent.change(screen.getByTestId('voice-file'), { target: { files: [file] } });
    expect(screen.getByTestId('voice-save')).toBeDisabled();
    fireEvent.click(screen.getByTestId('voice-consent'));
    expect(screen.getByTestId('voice-save')).not.toBeDisabled();
    fireEvent.click(screen.getByTestId('voice-save'));
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      '/models/local/runtime/voices', expect.any(FormData), expect.objectContaining({ timeout: 120000 }),
    ));
    const form = post.mock.calls.find(([url]) => url === '/models/local/runtime/voices')[1];
    expect(form.get('name')).toBe('boris');
    expect(form.get('consent')).toBe('true');
    expect(form.get('file').name).toBe('boris.wav');
  });
});
