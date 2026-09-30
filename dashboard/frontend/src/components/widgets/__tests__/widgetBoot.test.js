// The widget script booting on a page: it reads its tag, asks the hub for the
// widget's config with the publishable key, and draws nothing at all when the
// hub refuses (a wrong origin, a switched off widget).
import { describe, it, expect, vi, afterEach } from 'vitest';

const addTag = (attrs) => {
  const script = document.createElement('script');
  Object.entries(attrs).forEach(([k, v]) => script.setAttribute(k, v));
  document.body.appendChild(script);
  return script;
};

const load = async () => {
  vi.resetModules();
  await import('../../../../widget/widget.js');
  // Let the config fetch settle.
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
};

afterEach(() => {
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

describe('widget boot', () => {
  it('asks for its config with the key and draws the bubble', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true, status: 200, headers: { get: () => null },
      json: async () => ({ widget_id: 'wgt_1', title: 'Help', accent: 'teal', language: 'en',
        greeting: '', placeholder: '', limits: { max_attachments: 0, attachment_max_bytes: 0 } }),
    }));
    vi.stubGlobal('fetch', fetchMock);
    addTag({ 'data-widget': 'wgt_1', 'data-key': 'ahw_k', 'data-hub': 'https://hub.example' });
    await load();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://hub.example/api/widgets/public/wgt_1/config');
    expect(init.headers['X-Widget-Key']).toBe('ahw_k');
    expect(init.credentials).toBe('omit');
    expect(document.querySelector('[data-agents-hub-widget="wgt_1"]')).not.toBeNull();
  });

  it('stays invisible when the hub refuses', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false, status: 403, headers: { get: () => null },
      json: async () => ({ code: 'origin_not_allowed' }),
    })));
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    addTag({ 'data-widget': 'wgt_2', 'data-key': 'ahw_k', 'data-hub': 'https://hub.example' });
    await load();
    expect(document.querySelector('[data-agents-hub-widget]')).toBeNull();
    expect(warn).toHaveBeenCalled();
  });

  it('needs both the widget id and the key', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    addTag({ 'data-widget': 'wgt_3' });
    await load();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
