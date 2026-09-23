import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The GitHub App card says whether the app is configured (naming the
// variables when not), links to the install page, lists installations with
// their workspace binding, binds and unbinds through the workspace route,
// syncs, and hides itself from a non-admin the backend refuses.

const ok = (data) => Promise.resolve({ data });
const fail = (status, detail) => Promise.reject({ response: { status, data: { detail } } });

const getGitHubApp = vi.fn();
const syncGitHubApp = vi.fn();
const setWorkspaceGitHubInstallation = vi.fn(() => ok({}));
const getWorkspaces = vi.fn(() => ok([{ name: 'default' }, { name: 'acme' }]));

vi.mock('../../api', () => ({
  getGitHubApp: (...a) => getGitHubApp(...a),
  syncGitHubApp: (...a) => syncGitHubApp(...a),
  setWorkspaceGitHubInstallation: (...a) => setWorkspaceGitHubInstallation(...a),
  getWorkspaces: (...a) => getWorkspaces(...a),
}));

import GitHubAppCard from '../connectors/GitHubAppCard';

const APP = {
  configured: true,
  install_url: 'https://github.com/apps/hub-bot/installations/new',
  env: [],
  installations: [
    { installation_id: 11, account_login: 'acme-org', account_type: 'Organization',
      target: 'selected', workspace: 'acme', permissions: {} },
    { installation_id: 22, account_login: 'octo', account_type: 'User',
      target: 'all', workspace: '', permissions: {} },
  ],
};

const show = () => render(<I18nProvider><GitHubAppCard /></I18nProvider>);

beforeEach(() => {
  vi.clearAllMocks();
  getGitHubApp.mockImplementation(() => ok(APP));
  syncGitHubApp.mockImplementation(() => ok(APP));
});

describe('GitHubAppCard', () => {
  it('names the variables when the app is not configured', async () => {
    getGitHubApp.mockImplementation(() => ok({
      configured: false, installations: [], env: ['GITHUB_APP_ID', 'GITHUB_APP_PRIVATE_KEY'],
    }));
    show();
    await waitFor(() => expect(screen.getByText(/not configured/i)).toBeInTheDocument());
    expect(screen.getByText(/GITHUB_APP_PRIVATE_KEY/)).toBeInTheDocument();
    expect(screen.queryByText(/^install$/i)).not.toBeInTheDocument();
  });

  it('lists installations with the install link and their binding', async () => {
    show();
    await waitFor(() => expect(screen.getByText('acme-org')).toBeInTheDocument());
    expect(screen.getByText(/^install$/i).closest('a').getAttribute('href')).toBe(APP.install_url);
    expect(screen.getByLabelText(/workspace for acme-org/i).value).toBe('acme');
    expect(screen.getByLabelText(/workspace for octo/i).value).toBe('');
  });

  it('binds, unbinds and syncs', async () => {
    show();
    await waitFor(() => expect(screen.getByText('octo')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/workspace for octo/i), { target: { value: 'default' } });
    await waitFor(() => expect(setWorkspaceGitHubInstallation).toHaveBeenCalledWith('default', 22));
    fireEvent.click(screen.getByText(/^unbind$/i));
    await waitFor(() => expect(setWorkspaceGitHubInstallation).toHaveBeenCalledWith('acme', null));
    fireEvent.click(screen.getByText(/^sync$/i));
    await waitFor(() => expect(syncGitHubApp).toHaveBeenCalled());
  });

  it('hides itself from someone the backend refuses', async () => {
    getGitHubApp.mockImplementation(() => fail(403, 'Administrator access required'));
    const { container } = show();
    await waitFor(() => expect(getGitHubApp).toHaveBeenCalled());
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
