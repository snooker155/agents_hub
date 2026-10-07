import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../i18n';
import Models from '../Models';

// The catalog's temperature and reasoning columns: a model's own temperature
// (empty = the global default), disabled where the model rejects one, and the
// reasoning effort the provider applies by default next to what thinking
// level off sends.

const ok = (data) => Promise.resolve({ data });

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'default' }),
}));

const get = vi.fn();
const put = vi.fn();

vi.mock('axios', () => ({
  default: {
    create: () => ({
      get: (...args) => get(...args),
      post: () => Promise.reject(new Error('unexpected POST')),
      delete: () => Promise.reject(new Error('unexpected DELETE')),
      put: (...args) => put(...args),
      interceptors: { request: { use: () => {} }, response: { use: () => {} } },
    }),
  },
}));

const model = (id, extra) => ({
  id, enabled: true, input_price: 1, output_price: 2, cached_input_price: 0.1,
  context_window: 128000, released_at: 0, price_source: 'auto', temperature: null, ...extra,
});

const CATALOG = {
  providers: {
    openai: {
      default: 'gpt-5',
      models: [
        model('gpt-5', { reasoning: { default: 'medium', off: 'minimal', temperature: 'never' } }),
        model('gpt-5.4', { temperature: 0.7, reasoning: { default: 'none', off: 'none', temperature: 'when_off' } }),
        model('gpt-4o', { reasoning: null }),
      ],
    },
  },
  global_default: { provider: 'openai', model: 'gpt-5' },
  global_temperature: 0.2,
};

beforeEach(() => {
  try { localStorage.clear(); } catch { /* storage unavailable */ }
  get.mockReset();
  put.mockReset();
  get.mockImplementation((url) => {
    if (url === '/models') return ok(CATALOG);
    if (url === '/models/usage') return ok({ rows: [], totals: {} });
    if (url.startsWith('/workspaces/')) return ok({ global_default: {}, workspace_default: {} });
    return ok({});
  });
  put.mockImplementation((url, body) => ok({ ...CATALOG, providers: body.providers }));
});

function row(id) {
  // The id also shows in the global default line; the catalog row is the
  // table cell whose own text starts with it.
  return screen.getAllByRole('cell').find((td) => td.firstChild?.textContent === id).closest('tr');
}

async function renderCatalog() {
  render(
    <I18nProvider>
      <MemoryRouter>
        <Models />
      </MemoryRouter>
    </I18nProvider>,
  );
  await screen.findByText('gpt-4o');
}

describe('Models catalog: temperature and reasoning', () => {
  it('shows the default effort and what off sends', async () => {
    await renderCatalog();
    const gpt5 = within(row('gpt-5'));
    expect(gpt5.getByText('medium')).toBeInTheDocument();
    expect(gpt5.getByText('off → minimal')).toBeInTheDocument();
    // Default and off coincide: nothing to add under it.
    const gpt54 = within(row('gpt-5.4'));
    expect(gpt54.getByText('none')).toBeInTheDocument();
    expect(gpt54.queryByText(/off →/)).toBeNull();
    // No table for the model: a dash.
    expect(within(row('gpt-4o')).getByText('—')).toBeInTheDocument();
  });

  it('takes a temperature per model, falls back to the global one, and saves it', async () => {
    await renderCatalog();
    const temp = (id) => within(row(id)).getByRole('spinbutton', { name: 'Temp.' });
    expect(temp('gpt-5')).toBeDisabled();
    expect(temp('gpt-5.4')).toHaveValue(0.7);
    expect(temp('gpt-4o')).toHaveValue(null);
    expect(temp('gpt-4o')).toHaveAttribute('placeholder', '0.2');

    fireEvent.change(temp('gpt-4o'), { target: { value: '0.9' } });
    fireEvent.change(temp('gpt-5.4'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));

    await waitFor(() => expect(put).toHaveBeenCalled());
    const saved = put.mock.calls[0][1].providers.openai.models;
    expect(saved.find((m) => m.id === 'gpt-4o').temperature).toBe(0.9);
    expect(saved.find((m) => m.id === 'gpt-5.4').temperature).toBeNull();
  });
});
