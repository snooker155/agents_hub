import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../i18n';
import Models from '../Models';

// Feature 5's Local tab on the Models page: an external Ollama, the hub's own
// model runtime, and the hub's served /v1 endpoint with its usage. The
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

const OLLAMA_OK = {
  ok: true,
  base_url: 'http://localhost:11434',
  disk_bytes: 4_500_000_000,
  models: [
    {
      name: 'llama3.2:3b', size: 2_000_000_000, modified_at: '2026-09-01T00:00:00Z',
      digest: 'sha256:abc', family: 'llama', parameter_size: '3B', quantization_level: 'Q4_0', format: 'gguf',
    },
  ],
};

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

let jobs;
let runtimeState;

function installGet() {
  get.mockImplementation((url) => {
    if (url === '/models') return ok({ providers: {}, global_default: {} });
    if (url === '/models/usage') return ok({ rows: [], totals: {} });
    if (url.startsWith('/workspaces/')) return ok({ global_default: {}, workspace_default: {} });
    if (url === '/models/local/ollama') return ok(OLLAMA_OK);
    if (url === '/models/local/jobs') return ok({ jobs });
    if (url === '/models/local/runtime') return ok(runtimeState);
    if (url === '/models/serving/info') return ok(SERVING_INFO);
    if (url === '/models/serving/usage') return ok(SERVING_USAGE);
    return Promise.reject(new Error(`unhandled GET ${url}`));
  });
}

beforeEach(() => {
  jobs = [];
  runtimeState = RUNTIME_NOT_CONFIGURED;
  get.mockReset();
  post.mockReset();
  del.mockReset();
  put.mockReset();
  installGet();

  post.mockImplementation((url, body) => {
    if (url === '/models/local/ollama/pull') {
      jobs = [{
        id: 'job-1', kind: 'ollama_pull', name: body.name, status: 'running',
        completed: 40, total: 100, percent: 40, message: null, error: null,
      }];
      return ok({ job_id: 'job-1' });
    }
    if (url === '/models/local/runtime/load') return ok({ ok: true, model: {} });
    if (url === '/models/local/runtime/download') return ok({ job_id: 'job-2' });
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
  await screen.findByText('llama3.2:3b');
  return view;
}

describe('Models, Local tab', () => {
  it('renders the Ollama models with their sizes', async () => {
    await openLocalTab();
    expect(screen.getByText('llama3.2:3b')).toBeInTheDocument();
    expect(screen.getByText('3B')).toBeInTheDocument();
    expect(screen.getByText('Q4_0')).toBeInTheDocument();
    // humanBytes(2_000_000_000) -> "1.9 GB"
    expect(screen.getByText('1.9 GB')).toBeInTheDocument();
  });

  it('pulls a model and shows its job progress', async () => {
    await openLocalTab();
    const input = screen.getByPlaceholderText(/llama3\.2:3b/);
    fireEvent.change(input, { target: { value: 'phi3:mini' } });
    fireEvent.click(screen.getByRole('button', { name: 'Pull' }));

    await waitFor(() => expect(post).toHaveBeenCalledWith('/models/local/ollama/pull', { name: 'phi3:mini' }));
    await waitFor(() => expect(screen.getByText('phi3:mini')).toBeInTheDocument());
    expect(screen.getByText('40%')).toBeInTheDocument();
  });

  it('deletes an Ollama model after confirming', async () => {
    await openLocalTab();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.click(screen.getByTitle('Delete'));
    await waitFor(() => expect(del).toHaveBeenCalledWith(`/models/local/ollama/${encodeURIComponent('llama3.2:3b')}`));
  });

  it('shows the not-configured explainer when the hub runtime has no url set', async () => {
    await openLocalTab();
    expect(screen.getByText(/hub.s own model runtime is not set up/)).toBeInTheDocument();
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

  it('shows the serving base url, usage rows and totals', async () => {
    await openLocalTab();
    await screen.findByText('https://hub.example.com/v1');
    // "gpt-4o" and the request count "12" each appear twice: once in the
    // totals/usage table, once more in the recent-calls list or row itself.
    expect(screen.getAllByText('gpt-4o').length).toBeGreaterThan(0);
    expect(screen.getAllByText('12').length).toBeGreaterThan(0);
  });
});
