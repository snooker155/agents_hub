import { describe, it, expect, vi, beforeEach } from 'vitest';

// The header's model picker subscribes to the workspace summary: a forced read
// after the Models page saves a model reaches it, and a global change makes
// every stored answer stale, not only the selected workspace's.

const get = vi.fn();
vi.mock('../index', () => ({ default: { get: (...args) => get(...args) } }));
vi.mock('../workspaces', () => ({ getWorkspaces: () => Promise.resolve({ data: [] }) }));

import {
  loadWorkspaceSummary, subscribeWorkspaceSummary, invalidateAllWorkspaceSummaries,
} from '../workspaceSummary';

const summary = (provider, model) => ({ data: { model: { global_default: { provider, model } } } });

beforeEach(() => { get.mockReset(); invalidateAllWorkspaceSummaries(); });

describe('workspace summary cache', () => {
  it('tells subscribers about a forced read, and forgets every workspace on a global change', async () => {
    get.mockResolvedValueOnce(summary('openai', 'gpt-4o')).mockResolvedValueOnce(summary('openai', 'gpt-4o'));
    await loadWorkspaceSummary('a');
    await loadWorkspaceSummary('b');
    expect(get).toHaveBeenCalledTimes(2);

    const seen = [];
    const off = subscribeWorkspaceSummary('a', (data) => seen.push(data.model.global_default.model));
    get.mockResolvedValueOnce(summary('anthropic', 'claude-opus-5-5'));
    await loadWorkspaceSummary('a', { force: true });
    expect(seen).toEqual(['claude-opus-5-5']);
    off();

    // Fresh answers are served from the cache until the global change clears them.
    await loadWorkspaceSummary('b');
    expect(get).toHaveBeenCalledTimes(3);
    invalidateAllWorkspaceSummaries();
    get.mockResolvedValueOnce(summary('anthropic', 'claude-opus-5-5'));
    expect((await loadWorkspaceSummary('b')).model.global_default.model).toBe('claude-opus-5-5');
    expect(get).toHaveBeenCalledTimes(4);
  });
});
