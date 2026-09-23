import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The account page draws from four endpoints (sessions, keys, workspaces,
// the viewer itself via useAuth) and posts to three more (revoke a session,
// change the password, create/revoke a key). What matters here is that each
// section renders what the backend returns, that the current session is
// marked, that a password change signs the browser out, and that a freshly
// created key is shown once with a copy affordance.

const ok = (data) => Promise.resolve({ data });

const getMySessions = vi.fn();
const revokeMySession = vi.fn(() => ok({ deleted: true }));
const revokeOtherSessions = vi.fn(() => ok({ revoked: 1 }));
const changeMyPassword = vi.fn(() => ok({ ok: true, relogin: true }));
const getMyApiKeys = vi.fn();
const createMyApiKey = vi.fn();
const revokeMyApiKey = vi.fn(() => ok({ deleted: true }));
const getWorkspaces = vi.fn(() => ok([{ name: 'default' }, { name: 'acme' }]));
const getMyGitHub = vi.fn();
const disconnectMyGitHub = vi.fn(() => ok({ deleted: true }));

vi.mock('../../api', () => ({
  getMyGitHub: (...a) => getMyGitHub(...a),
  disconnectMyGitHub: (...a) => disconnectMyGitHub(...a),
  githubConnectUrl: () => '/api/auth/github/connect?token=t0k',
  getMySessions: (...a) => getMySessions(...a),
  revokeMySession: (...a) => revokeMySession(...a),
  revokeOtherSessions: (...a) => revokeOtherSessions(...a),
  changeMyPassword: (...a) => changeMyPassword(...a),
  getMyApiKeys: (...a) => getMyApiKeys(...a),
  createMyApiKey: (...a) => createMyApiKey(...a),
  revokeMyApiKey: (...a) => revokeMyApiKey(...a),
  getWorkspaces: (...a) => getWorkspaces(...a),
}));

const logout = vi.fn(() => Promise.resolve());
let authUser;

vi.mock('../../components/auth', () => ({
  useAuth: () => ({
    user: authUser,
    features: { local_passwords: true },
    logout,
  }),
}));

import Account from '../Account';

const USER = {
  id: 'u1', username: 'alice', display_name: 'Alice', email: 'alice@example.com',
  source: 'local', role: 'member', has_password: true,
  groups: ['engineering'], workspaces: ['default', 'acme'],
};

const SESSIONS = [
  {
    id: 's1', kind: 'password', ip: '10.0.0.1', user_agent: 'Firefox',
    created_at: '2026-01-01T10:00:00Z', last_seen_at: '2026-01-02T10:00:00Z',
    expires_at: '2026-02-01T10:00:00Z', current: true,
  },
  {
    id: 's2', kind: 'password', ip: '10.0.0.2', user_agent: 'Chrome',
    created_at: '2026-01-01T09:00:00Z', last_seen_at: '2026-01-01T09:30:00Z',
    expires_at: '2026-02-01T09:00:00Z', current: false,
  },
];

const KEYS = [
  {
    id: 'k1', name: 'laptop', hint: 'abcd', workspaces: null,
    created_at: '2026-01-01T00:00:00Z', last_used_at: null, expires_at: null,
  },
];

const show = () => render(
  <I18nProvider><MemoryRouter><Account /></MemoryRouter></I18nProvider>,
);

beforeEach(() => {
  vi.clearAllMocks();
  authUser = USER;
  getMySessions.mockImplementation(() => ok(SESSIONS));
  getMyApiKeys.mockImplementation(() => ok(KEYS));
  getWorkspaces.mockImplementation(() => ok([{ name: 'default' }, { name: 'acme' }]));
  getMyGitHub.mockImplementation(() => ok({ configured: true, key_configured: true, connected: false }));
});

describe('Account', () => {
  it('shows the profile drawn from the viewer', async () => {
    show();
    await waitFor(() => expect(screen.getByText('alice')).toBeInTheDocument());
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('alice@example.com')).toBeInTheDocument();
    expect(screen.getByText('engineering')).toBeInTheDocument();
  });

  it('lists sessions and marks the current one', async () => {
    show();
    await waitFor(() => expect(getMySessions).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText('10.0.0.1')).toBeInTheDocument());
    expect(screen.getByText('10.0.0.2')).toBeInTheDocument();
    expect(screen.getByText('this session')).toBeInTheDocument();
  });

  it('revokes a session by its row', async () => {
    show();
    await waitFor(() => expect(screen.getByText('10.0.0.2')).toBeInTheDocument());
    const row = screen.getByText('10.0.0.2').closest('tr');
    fireEvent.click(row.querySelector('button'));
    await waitFor(() => expect(revokeMySession).toHaveBeenCalledWith('s2'));
  });

  it('signs out every other session on request', async () => {
    show();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await waitFor(() => expect(screen.getByText('10.0.0.1')).toBeInTheDocument());
    fireEvent.click(screen.getByText(/sign out everywhere else/i));
    await waitFor(() => expect(revokeOtherSessions).toHaveBeenCalled());
    confirmSpy.mockRestore();
  });

  it('changes the password and signs the browser out', async () => {
    show();
    await waitFor(() => expect(screen.getByLabelText(/current password/i)).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/current password/i), { target: { value: 'old-pw' } });
    fireEvent.change(screen.getByLabelText(/^new password/i), { target: { value: 'new-pw-long' } });
    fireEvent.change(screen.getByLabelText(/repeat new password/i), { target: { value: 'new-pw-long' } });
    fireEvent.click(screen.getByText(/change password/i));
    await waitFor(() => expect(changeMyPassword).toHaveBeenCalledWith('old-pw', 'new-pw-long'));
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });

  it('hides the password form when the account has none', async () => {
    authUser = { ...USER, has_password: false };
    show();
    await waitFor(() => expect(screen.getByText('alice')).toBeInTheDocument());
    expect(screen.queryByText(/change password/i)).not.toBeInTheDocument();
  });

  it('lists API keys with their scope', async () => {
    show();
    await waitFor(() => expect(screen.getByText('laptop')).toBeInTheDocument());
    expect(screen.getByText(/abcd/)).toBeInTheDocument();
    expect(screen.getByText(/full reach/i)).toBeInTheDocument();
  });

  it('creates a key and shows it once, copyable', async () => {
    createMyApiKey.mockImplementation(() => ok({
      id: 'k2', name: 'ci', hint: 'wxyz', workspaces: null,
      created_at: '2026-01-03T00:00:00Z', last_used_at: null, expires_at: null,
      key: 'ahk_brandnewsecret',
    }));
    show();
    await waitFor(() => expect(screen.getByPlaceholderText(/laptop CLI/i)).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/laptop CLI/i), { target: { value: 'ci' } });
    fireEvent.click(screen.getByText(/create key/i));
    await waitFor(() => expect(createMyApiKey).toHaveBeenCalledWith({
      name: 'ci', workspaces: null, expires_in_days: 30,
    }));
    await waitFor(() => expect(screen.getByText('ahk_brandnewsecret')).toBeInTheDocument());
  });

  it('revokes a key by its row', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    show();
    await waitFor(() => expect(screen.getByText('laptop')).toBeInTheDocument());
    fireEvent.click(screen.getByText(/^revoke$/i));
    await waitFor(() => expect(revokeMyApiKey).toHaveBeenCalledWith('k1'));
    confirmSpy.mockRestore();
  });

  it('offers to connect GitHub with the credential in the link', async () => {
    show();
    const link = await screen.findByText(/^connect$/i);
    expect(link.closest('a').getAttribute('href')).toBe('/api/auth/github/connect?token=t0k');
  });

  it('shows the connected GitHub login and disconnects', async () => {
    getMyGitHub.mockImplementation(() => ok({
      configured: true, key_configured: true, connected: true, login: 'alice-gh',
      access_expires_at: '2026-09-23T18:00:00Z', refresh_expires_at: '2027-03-23T10:00:00Z',
    }));
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    show();
    await waitFor(() => expect(screen.getByText(/connected as alice-gh/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/^disconnect$/i));
    await waitFor(() => expect(disconnectMyGitHub).toHaveBeenCalled());
    confirmSpy.mockRestore();
  });

  it('reports the outcome the GitHub callback left in the hash', async () => {
    window.location.hash = '#github=error&reason=bad_state';
    show();
    await waitFor(() => expect(screen.getByText(/connecting github failed: bad_state/i)).toBeInTheDocument());
    expect(window.location.hash).toBe('');
  });
});
