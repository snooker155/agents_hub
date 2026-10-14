import { afterEach, describe, expect, it, vi } from 'vitest';

// A workspace this browser remembers from another hub served on the same
// address is forgotten before anything asks for its summary (which 404s).

const fresh = async (names, { fail = false } = {}) => {
  vi.resetModules();
  const index = await import('../index');
  const adapter = vi.fn(async (config) => {
    if (fail) throw Object.assign(new Error('offline'), { config });
    return { data: names.map((name) => ({ name })), status: 200, statusText: 'OK', headers: {}, config };
  });
  index.default.defaults.adapter = adapter;
  const mod = await import('../workspaceSummary');
  return { ...mod, adapter };
};

afterEach(() => { localStorage.clear(); });

describe('checkStoredWorkspace', () => {
  it('forgets a remembered workspace the backend does not have', async () => {
    localStorage.setItem('selectedWorkspace', 'dev');
    const { checkStoredWorkspace } = await fresh(['default', 'system']);
    await expect(checkStoredWorkspace()).resolves.toBe('');
    expect(localStorage.getItem('selectedWorkspace')).toBeNull();
  });

  it('keeps one that exists, and asks only once', async () => {
    localStorage.setItem('selectedWorkspace', 'system');
    const { checkStoredWorkspace, adapter } = await fresh(['default', 'system']);
    await expect(checkStoredWorkspace()).resolves.toBe('system');
    await checkStoredWorkspace();
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem('selectedWorkspace')).toBe('system');
  });

  it('keeps it when the list cannot be read', async () => {
    localStorage.setItem('selectedWorkspace', 'dev');
    const { checkStoredWorkspace } = await fresh([], { fail: true });
    await expect(checkStoredWorkspace()).resolves.toBe('dev');
    expect(localStorage.getItem('selectedWorkspace')).toBe('dev');
  });
});
