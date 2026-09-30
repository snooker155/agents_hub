import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import api, { setSessionToken } from '../index';

// Identical GETs on the wire at the same time share one request (StrictMode
// mounts effects twice in development), and /auth/preferences is not asked
// for at all outside AUTH_MODE=multi.

let adapter;
const original = api.defaults.adapter;

beforeEach(() => {
  adapter = vi.fn(async (config) => ({
    data: { url: config.url, list: [3, 1, 2] }, status: 200, statusText: 'OK', headers: {}, config,
  }));
  api.defaults.adapter = adapter;
});

afterEach(() => {
  api.defaults.adapter = original;
  setSessionToken(null);
});

// A fresh copy of the API modules, so the mode palette.js remembers does not
// carry over from one test to the next.
const freshPalette = async () => {
  vi.resetModules();
  const index = await import('../index');
  index.default.defaults.adapter = adapter;
  return import('../palette');
};

describe('GET sharing', () => {
  it('sends one request for two identical GETs in flight', async () => {
    const [a, b] = await Promise.all([api.get('/agents'), api.get('/agents')]);
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(a.data).toEqual(b.data);
    a.data.list.sort();
    expect(b.data.list).toEqual([3, 1, 2]);
  });

  it('asks again once the first request has finished', async () => {
    await api.get('/agents');
    await api.get('/agents');
    expect(adapter).toHaveBeenCalledTimes(2);
  });

  it('keeps different params and abortable calls apart', async () => {
    await Promise.all([
      api.get('/agents', { params: { w: 'a' } }),
      api.get('/agents', { params: { w: 'b' } }),
      api.get('/agents', { signal: new AbortController().signal }),
    ]);
    expect(adapter).toHaveBeenCalledTimes(3);
  });
});

describe('preferences outside multi mode', () => {
  it('rejects with a 404 and sends nothing without a session', async () => {
    const { getMyPreferences } = await freshPalette();
    await expect(getMyPreferences()).rejects.toMatchObject({ response: { status: 404 } });
    expect(adapter).not.toHaveBeenCalled();
  });

  it('asks the mode once and skips preferences in single mode', async () => {
    setSessionToken('stale');
    adapter.mockImplementation(async (config) => ({
      data: { mode: 'single' }, status: 200, statusText: 'OK', headers: {}, config,
    }));
    const { getMyPreferences, putMyPreferences } = await freshPalette();
    await expect(getMyPreferences()).rejects.toMatchObject({ response: { status: 404 } });
    await expect(putMyPreferences({ a: 1 })).rejects.toMatchObject({ response: { status: 404 } });
    expect(adapter.mock.calls.map(([c]) => c.url)).toEqual(['/auth/mode']);
  });

  it('reads preferences in multi mode', async () => {
    setSessionToken('sess');
    adapter.mockImplementation(async (config) => ({
      data: config.url === '/auth/mode' ? { mode: 'multi' } : { palette: {} },
      status: 200, statusText: 'OK', headers: {}, config,
    }));
    const { getMyPreferences } = await freshPalette();
    const { data } = await getMyPreferences();
    expect(data).toEqual({ palette: {} });
  });
});
