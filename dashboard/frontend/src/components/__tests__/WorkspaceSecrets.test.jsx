import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The secrets card lists names, scopes and hints (never values), stores a new
// secret with its optional scope, deletes one at exactly its scope, hides
// itself from a viewer the backend refuses, and explains the missing key
// when the backend says none is configured.

const ok = (data) => Promise.resolve({ data });
const fail = (status, detail) => Promise.reject({ response: { status, data: { detail } } });

const getWorkspaceSecrets = vi.fn();
const setWorkspaceSecret = vi.fn(() => ok({}));
const deleteWorkspaceSecret = vi.fn(() => ok({ deleted: true }));
const getUsers = vi.fn(() => ok([{ id: 'u1', username: 'alice' }]));

vi.mock('../../api', () => ({
  getWorkspaceSecrets: (...a) => getWorkspaceSecrets(...a),
  setWorkspaceSecret: (...a) => setWorkspaceSecret(...a),
  deleteWorkspaceSecret: (...a) => deleteWorkspaceSecret(...a),
  getUsers: (...a) => getUsers(...a),
}));

let authMode = 'single';
vi.mock('../auth', () => ({
  MULTI: 'multi',
  useAuth: () => ({ mode: authMode }),
}));

import WorkspaceSecrets from '../workspace/WorkspaceSecrets';

const ROWS = [
  { name: 'GITHUB_TOKEN', agent_id: 'publisher', user_id: '', hint: 'x9Qa',
    updated_at: '2026-09-23T10:00:00Z', created_by: 'local' },
  { name: 'SHORT', agent_id: '', user_id: 'u1', hint: '****',
    updated_at: '2026-09-23T11:00:00Z', created_by: 'local' },
];

const show = (props = {}) => render(
  <I18nProvider>
    <WorkspaceSecrets workspace="alpha" agents={['publisher', 'writer']} {...props} />
  </I18nProvider>,
);

describe('WorkspaceSecrets', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authMode = 'single';
    getWorkspaceSecrets.mockImplementation(() => ok(ROWS));
  });

  it('lists names, scopes and hints, in single mode too', async () => {
    show();
    expect(await screen.findByText('GITHUB_TOKEN')).toBeTruthy();
    expect(screen.getByText('Agent publisher')).toBeTruthy();
    expect(screen.getByText('••••x9Qa')).toBeTruthy();
    expect(screen.getByText('User u1')).toBeTruthy();
    // No user picker outside multi mode.
    expect(screen.queryByLabelText('Any user')).toBeNull();
    expect(getUsers).not.toHaveBeenCalled();
  });

  it('stores a secret with its agent scope', async () => {
    show();
    await screen.findByText('GITHUB_TOKEN');
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'deploy_key' } });
    fireEvent.change(screen.getByLabelText('Value'), { target: { value: 's3cr3t-value' } });
    fireEvent.change(screen.getByLabelText('Any agent'), { target: { value: 'writer' } });
    fireEvent.click(screen.getByText('Save secret'));
    await waitFor(() => expect(setWorkspaceSecret).toHaveBeenCalledWith(
      'alpha', 'DEPLOY_KEY', { value: 's3cr3t-value', agent_id: 'writer' }));
    expect(screen.getByLabelText('Value').getAttribute('type')).toBe('password');
  });

  it('deletes at exactly the row scope', async () => {
    show();
    await screen.findByText('GITHUB_TOKEN');
    fireEvent.click(screen.getByLabelText('Delete secret GITHUB_TOKEN'));
    await waitFor(() => expect(deleteWorkspaceSecret).toHaveBeenCalledWith(
      'alpha', 'GITHUB_TOKEN', { agent_id: 'publisher' }));
  });

  it('explains the missing key when the backend refuses to store', async () => {
    getWorkspaceSecrets.mockImplementation(() => ok([]));
    setWorkspaceSecret.mockImplementationOnce(() => fail(
      400, 'no secret key is configured: set AGENTS_HUB_SECRET_KEY, generate one with ah secrets keygen'));
    show();
    expect(await screen.findByText('No secrets in this workspace yet.')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'X' } });
    fireEvent.change(screen.getByLabelText('Value'), { target: { value: 'v' } });
    fireEvent.click(screen.getByText('Save secret'));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('ah secrets keygen');
  });

  it('offers the user scope in multi mode', async () => {
    authMode = 'multi';
    show();
    await screen.findByText('GITHUB_TOKEN');
    await waitFor(() => expect(getUsers).toHaveBeenCalled());
    expect(await screen.findByText('alice', { selector: 'option' })).toBeTruthy();
  });

  it('hides itself from a viewer the backend refuses', async () => {
    authMode = 'multi';
    getWorkspaceSecrets.mockImplementation(() => fail(403, 'no'));
    const { container } = show();
    await waitFor(() => expect(getWorkspaceSecrets).toHaveBeenCalled());
    await waitFor(() => expect(container.textContent).toBe(''));
  });
});
