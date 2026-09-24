import { afterEach, describe, expect, it, vi } from 'vitest';

import api, { connectGitHub, setSessionToken, withAuthTicket } from '../index';

// withAuthTicket puts a one-time ticket in a URL that cannot carry a header
// (docs/identity.md, "Tickets for streams"), and only falls back to the
// legacy token form when the backend cannot mint one.

afterEach(() => {
  vi.restoreAllMocks();
  setSessionToken(null);
});

describe('withAuthTicket', () => {
  it('adds nothing when the browser holds no credential', async () => {
    const post = vi.spyOn(api, 'post');
    expect(await withAuthTicket('/api/x')).toBe('/api/x');
    expect(post).not.toHaveBeenCalled();
  });

  it('mints a ticket and never puts the session in the URL', async () => {
    setSessionToken('sess');
    vi.spyOn(api, 'post').mockResolvedValue({ data: { ticket: 'T1', expires_in: 60 } });
    expect(await withAuthTicket('/api/x?a=1')).toBe('/api/x?a=1&ticket=T1');
  });

  it('falls back to the token when the backend answers 404', async () => {
    setSessionToken('sess');
    vi.spyOn(api, 'post').mockRejectedValue(Object.assign(new Error('404'), { response: { status: 404 } }));
    expect(await withAuthTicket('/api/x')).toBe('/api/x?token=sess');
  });

  it('connectGitHub navigates with a ticket', async () => {
    setSessionToken('sess');
    vi.spyOn(api, 'post').mockResolvedValue({ data: { ticket: 'T9' } });
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });
    await connectGitHub();
    expect(assign).toHaveBeenCalledWith('/api/auth/github/connect?ticket=T9');
    vi.unstubAllGlobals();
  });
});
