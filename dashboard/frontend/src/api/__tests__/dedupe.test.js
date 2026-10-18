import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import api from '../index';

// Identical GETs on the wire at the same time share one request (StrictMode
// mounts effects twice in development).

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
});

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
