import {
  act, fireEvent, render, screen, waitFor,
} from '@testing-library/react';
import {
  afterEach, beforeEach, describe, expect, it, vi,
} from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { I18nProvider } from '../../i18n';

// The login screen with single sign-on (docs/sso.md): which ways in it offers
// is decided by `auth.oidc` and `auth.features.local_passwords`; the callback
// page adopts the session the backend put in the URL fragment; and an open
// tab sends the browser back through the provider shortly before an OIDC
// session ends.

const mockAuth = { current: {} };

// A sign-in opens the app with a full page load (sessionMeta.js enterApp),
// which jsdom cannot do: the call itself is what these tests check.
const enterApp = vi.hoisted(() => vi.fn());
vi.mock('../../components/sessionMeta', async (importOriginal) => ({
  ...(await importOriginal()),
  enterApp,
}));

vi.mock('../../components/auth', async (importOriginal) => ({
  ...(await importOriginal()),
  useAuth: () => mockAuth.current,
}));

const api = vi.hoisted(() => ({
  getAuthMode: vi.fn(),
  getMe: vi.fn(),
}));

vi.mock('../../api', () => ({
  oidcStartUrl: (next = '') => `/api/auth/oidc/start${next ? `?next=${encodeURIComponent(next)}` : ''}`,
  getAuthMode: (...a) => api.getAuthMode(...a),
  getMe: (...a) => api.getMe(...a),
  authLogin: vi.fn(),
  authLogout: vi.fn(() => Promise.resolve({ data: {} })),
  authBootstrap: vi.fn(),
  getSessionToken: () => window.localStorage.getItem('agents_hub_session_token') || '',
  setSessionToken: (token) => {
    if (token) window.localStorage.setItem('agents_hub_session_token', token);
    else window.localStorage.removeItem('agents_hub_session_token');
  },
}));

import Login from '../Login';
import { AuthProvider } from '../../components/AuthContext';
import {
  SESSION_META_KEY, oidcRenewalDue, readSessionMeta,
} from '../../components/sessionMeta';

const baseAuth = (extra = {}) => ({
  mode: 'multi',
  bootstrapRequired: false,
  features: { login: true, local_passwords: true, oidc: true },
  oidc: { provider_name: 'Keycloak', start_url: '/api/auth/oidc/start' },
  user: null,
  login: vi.fn(),
  bootstrap: vi.fn(),
  adoptSession: vi.fn(),
  ...extra,
});

const setUrl = (url) => window.history.replaceState(null, '', url);

const show = (initial = '/') => render(
  <I18nProvider>
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="*" element={<Login />} />
      </Routes>
      <Routes>
        <Route path="/tasks" element={<span data-testid="arrived">tasks</span>} />
        <Route path="*" element={null} />
      </Routes>
    </MemoryRouter>
  </I18nProvider>,
);

describe('Login with single sign-on', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    setUrl('/');
  });

  it('offers the provider button and the password form', () => {
    mockAuth.current = baseAuth();
    show();
    const button = screen.getByRole('link', { name: /Sign in with Keycloak/ });
    expect(button.getAttribute('href')).toBe(`/api/auth/oidc/start?next=${encodeURIComponent('/chat')}`);
    expect(screen.getByLabelText('Password')).toBeTruthy();
  });

  it('lands on the main chat, not on the page the last person left open', () => {
    mockAuth.current = baseAuth();
    setUrl('/tasks?view=board');
    show('/tasks?view=board');
    const button = screen.getByRole('link', { name: /Sign in with Keycloak/ });
    expect(button.getAttribute('href'))
      .toBe(`/api/auth/oidc/start?next=${encodeURIComponent('/chat')}`);
  });

  it('says a refused sign-in in the page language, not the server English', async () => {
    enterApp.mockClear();
    const login = vi.fn(() => Promise.reject({ response: { status: 401, data: { detail: 'Wrong username or password' } } }));
    mockAuth.current = baseAuth({ login });
    show();
    fireEvent.change(document.getElementById('auth-username'), { target: { value: 'bob' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'bad' } });
    fireEvent.submit(screen.getByLabelText('Password').closest('form'));
    await waitFor(() => expect(screen.getByText('Wrong username or password.')).toBeTruthy());
    expect(enterApp).not.toHaveBeenCalled();
  });

  it('opens the main chat after a password sign-in', async () => {
    enterApp.mockClear();
    const login = vi.fn(() => Promise.resolve({ id: 'u2', username: 'bob' }));
    mockAuth.current = baseAuth({ login });
    setUrl('/tasks');
    show('/tasks');
    fireEvent.change(document.getElementById('auth-username'), { target: { value: 'bob' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.submit(screen.getByLabelText('Password').closest('form'));
    await waitFor(() => expect(enterApp).toHaveBeenCalledWith('/chat'));
    expect(login).toHaveBeenCalledWith('bob', 'pw');
  });

  it('makes the first administrator only with the setup code, and says a wrong one plainly', async () => {
    enterApp.mockClear();
    const bootstrap = vi.fn()
      .mockImplementationOnce(() => Promise.reject({ response: { status: 403, data: { detail: 'the setup code is missing or wrong' } } }))
      .mockImplementationOnce(() => Promise.resolve({ id: 'u1', username: 'root' }));
    mockAuth.current = baseAuth({ bootstrapRequired: true, bootstrap });
    show();
    fireEvent.change(document.getElementById('auth-username'), { target: { value: 'root' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.change(screen.getByLabelText('Repeat password'), { target: { value: 'pw' } });
    const submit = screen.getByRole('button', { name: /Create administrator/ });
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Setup code'), { target: { value: 'nope' } });
    fireEvent.submit(submit.closest('form'));
    await waitFor(() => expect(screen.getByText('The setup code is missing or wrong.')).toBeTruthy());
    fireEvent.change(screen.getByLabelText('Setup code'), { target: { value: 'ab12-cd34-ef56-0789' } });
    fireEvent.submit(submit.closest('form'));
    await waitFor(() => expect(enterApp).toHaveBeenCalledWith('/chat'));
    expect(bootstrap).toHaveBeenLastCalledWith('root', 'pw', '', 'ab12-cd34-ef56-0789');
  });

  it('hides the password form when local passwords are off, behind an admin link', () => {
    mockAuth.current = baseAuth({ features: { login: true, local_passwords: false, oidc: true } });
    show();
    expect(screen.queryByLabelText('Password')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /reserved for administrators/ }));
    expect(screen.getByLabelText('Password')).toBeTruthy();
  });

  it('shows no provider button without OIDC', () => {
    mockAuth.current = baseAuth({ oidc: null, features: { login: true, local_passwords: true } });
    show();
    expect(screen.queryByRole('link', { name: /Sign in with/ })).toBeNull();
    expect(screen.getByLabelText('Password')).toBeTruthy();
  });

  it('shows an error carried in the fragment', () => {
    mockAuth.current = baseAuth();
    setUrl('/login#error=disabled');
    show('/login');
    expect(screen.getByText(/This account is disabled/)).toBeTruthy();
  });
});

describe('OIDC callback', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it('adopts the session from the fragment, remembers its expiry and moves on', async () => {
    const adoptSession = vi.fn(() => Promise.resolve({ id: 'u1', username: 'alice' }));
    mockAuth.current = baseAuth({ adoptSession });
    const expires = '2030-01-01T00:00:00+00:00';
    setUrl(`/login/oidc#token=tok-123&expires_at=${encodeURIComponent(expires)}&next=%2Ftasks`);
    show('/login/oidc');
    await waitFor(() => expect(adoptSession).toHaveBeenCalledWith('tok-123'));
    expect(window.location.hash).toBe('');
    expect(readSessionMeta()).toEqual({ kind: 'oidc', expires_at: expires });
    await waitFor(() => expect(enterApp).toHaveBeenCalledWith('/tasks'));
  });

  it('refuses a next that leaves the site', async () => {
    const adoptSession = vi.fn(() => Promise.resolve({ id: 'u1' }));
    mockAuth.current = baseAuth({ adoptSession });
    setUrl('/login/oidc#token=t&expires_at=x&next=%2F%2Fevil.example');
    show('/login/oidc');
    await waitFor(() => expect(adoptSession).toHaveBeenCalled());
    expect(window.sessionStorage.getItem('agents_hub_oidc_next')).toBe('/');
  });

  it('shows the error and a way back', () => {
    mockAuth.current = baseAuth();
    setUrl('/login/oidc#error=bad_state');
    show('/login/oidc');
    expect(screen.getByRole('alert').textContent).toMatch(/took too long/);
    expect(screen.getByRole('link', { name: 'Back to sign-in' }).getAttribute('href')).toBe('/login');
  });
});

describe('silent renewal', () => {
  const realLocation = window.location;
  let assign;

  beforeEach(() => {
    window.localStorage.clear();
    assign = vi.fn();
    delete window.location;
    window.location = {
      ...realLocation, pathname: '/tasks', search: '', hash: '', assign,
    };
    api.getAuthMode.mockResolvedValue({
      data: {
        mode: 'multi', features: { oidc: true },
        oidc: { provider_name: 'Keycloak', start_url: '/api/auth/oidc/start' },
      },
    });
    api.getMe.mockResolvedValue({ data: { id: 'u1', username: 'alice', role: 'member' } });
    window.localStorage.setItem('agents_hub_session_token', 'tok');
  });

  afterEach(() => {
    window.location = realLocation;
  });

  it('goes back through the provider when the session ends soon', async () => {
    window.localStorage.setItem(SESSION_META_KEY, JSON.stringify({
      kind: 'oidc', expires_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
    }));
    render(<AuthProvider><span /></AuthProvider>);
    await waitFor(() => expect(assign).toHaveBeenCalledWith(
      `/api/auth/oidc/start?next=${encodeURIComponent('/tasks')}`,
    ));
  });

  it('leaves a session with time to spare, and password sessions, alone', async () => {
    window.localStorage.setItem(SESSION_META_KEY, JSON.stringify({
      kind: 'oidc', expires_at: new Date(Date.now() + 3 * 3600 * 1000).toISOString(),
    }));
    render(<AuthProvider><span /></AuthProvider>);
    await act(async () => { await Promise.resolve(); });
    await waitFor(() => expect(api.getMe).toHaveBeenCalled());
    expect(assign).not.toHaveBeenCalled();
    const soon = Date.now() + 60 * 1000;
    expect(oidcRenewalDue({ kind: 'password', expires_at: new Date(soon).toISOString() })).toBe(false);
    expect(oidcRenewalDue({ kind: 'oidc', expires_at: new Date(soon).toISOString() })).toBe(true);
  });
});
