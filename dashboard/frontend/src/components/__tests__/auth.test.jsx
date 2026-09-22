import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { AuthProvider } from '../AuthContext';
import { isAdmin, needsLogin, useAuth } from '../auth';
import * as api from '../../api';

/**
 * What the app renders is a function of the mode alone, so the probe reports
 * exactly the three decisions every identity-aware component makes: is there a
 * login to do, is this viewer an administrator, and who is signed in.
 */
function Probe() {
  const auth = useAuth();
  return (
    <>
      <span data-testid="mode">{auth.mode}</span>
      <span data-testid="login">{String(needsLogin(auth))}</span>
      <span data-testid="admin">{String(isAdmin(auth))}</span>
      <span data-testid="user">{auth.user?.username || 'none'}</span>
    </>
  );
}

const show = () => render(<AuthProvider><Probe /></AuthProvider>);
const at = (id) => screen.getByTestId(id).textContent;

const modeIs = (mode, extra = {}) => vi.spyOn(api, 'getAuthMode')
  .mockResolvedValue({ data: { mode, bootstrap_required: false, ...extra } });

describe('AuthProvider', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it('shows nothing identity-related in single mode', async () => {
    modeIs('single');
    show();
    await waitFor(() => expect(at('mode')).toBe('single'));
    expect(at('login')).toBe('false');
    expect(at('admin')).toBe('false');
    expect(at('user')).toBe('none');
  });

  it('asks for no login in token mode either', async () => {
    modeIs('token');
    show();
    await waitFor(() => expect(at('mode')).toBe('token'));
    expect(at('login')).toBe('false');
  });

  it('asks for a login in multi mode when nobody is signed in', async () => {
    modeIs('multi');
    const me = vi.spyOn(api, 'getMe');
    show();
    await waitFor(() => expect(at('login')).toBe('true'));
    // No session token in this browser, so there is nobody to ask about.
    expect(me).not.toHaveBeenCalled();
  });

  it('resolves the signed-in user in multi mode', async () => {
    api.setSessionToken('a-session-token');
    modeIs('multi');
    vi.spyOn(api, 'getMe').mockResolvedValue({
      data: { id: 'u1', username: 'root', role: 'admin', kind: 'user' },
    });
    show();
    await waitFor(() => expect(at('user')).toBe('root'));
    expect(at('login')).toBe('false');
    expect(at('admin')).toBe('true');
  });

  it('does not call a member an administrator', async () => {
    api.setSessionToken('a-session-token');
    modeIs('multi');
    vi.spyOn(api, 'getMe').mockResolvedValue({
      data: { id: 'u2', username: 'bob', role: 'member', kind: 'user' },
    });
    show();
    await waitFor(() => expect(at('user')).toBe('bob'));
    expect(at('admin')).toBe('false');
  });

  it('drops a session the backend no longer accepts', async () => {
    api.setSessionToken('a-stale-token');
    modeIs('multi');
    vi.spyOn(api, 'getMe').mockRejectedValue({ response: { status: 401 } });
    show();
    await waitFor(() => expect(at('login')).toBe('true'));
    expect(api.getSessionToken()).toBe('');
  });

  it('renders as a single operator while the answer is on the wire', () => {
    vi.spyOn(api, 'getAuthMode').mockReturnValue(new Promise(() => {}));
    show();
    // Not "false" by accident: a login form must not flash in front of
    // somebody who is never going to need one.
    expect(at('mode')).toBe('single');
    expect(at('login')).toBe('false');
  });

  it('hides identity when the backend cannot answer at all', async () => {
    vi.spyOn(api, 'getAuthMode').mockRejectedValue(new Error('offline'));
    show();
    await waitFor(() => expect(api.getAuthMode).toHaveBeenCalled());
    expect(at('mode')).toBe('single');
    expect(at('login')).toBe('false');
  });

  it('signs a user out and forgets their token', async () => {
    api.setSessionToken('a-session-token');
    modeIs('multi');
    vi.spyOn(api, 'getMe').mockResolvedValue({
      data: { id: 'u1', username: 'root', role: 'admin', kind: 'user' },
    });
    vi.spyOn(api, 'authLogout').mockResolvedValue({ data: { ok: true } });

    function WithLogout() {
      const auth = useAuth();
      return <button type="button" onClick={auth.logout}>{auth.user?.username || 'none'}</button>;
    }
    render(<AuthProvider><WithLogout /></AuthProvider>);
    const button = await screen.findByRole('button', { name: 'root' });
    button.click();
    await waitFor(() => expect(api.getSessionToken()).toBe(''));
  });
});
