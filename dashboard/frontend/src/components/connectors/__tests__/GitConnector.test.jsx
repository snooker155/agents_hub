import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { WorkspaceContext } from '../../workspace';
import { I18nProvider } from '../../../i18n';
import GitConnector from '../GitConnector';

// GitHub, GitLab, Bitbucket and Gitea tokens, one per provider, each scoped
// per workspace like the other connectors (connectors/channels/store.py):
// the default workspace's token works everywhere, another workspace's own
// token works only there. The config GET answers `sources` (per provider,
// "here" or "default") and, from the default workspace, `defined_in` per
// provider.

const api = vi.hoisted(() => ({
  getGitConfig: vi.fn(),
  updateGitConfig: vi.fn(),
  deleteGitConfig: vi.fn(),
  testGitConnection: vi.fn(),
  getGitHubApp: vi.fn(() => Promise.reject({ response: { status: 404 } })),
  getWorkspaces: vi.fn(() => Promise.resolve({ data: [] })),
  getWorkspaceGithubInstallation: vi.fn(() => Promise.resolve({ data: { own: null, effective: null, source: 'default' } })),
}));
vi.mock('../../../api', () => api);

const BASE_CONFIG = {
  github: { has_token: false }, gitlab: { has_token: false, base_url: '' },
  bitbucket: { has_token: false, username: '' }, gitea: { has_token: false, base_url: '' },
};

describe('GitConnector: per workspace, per provider source', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getGitHubApp.mockImplementation(() => Promise.reject({ response: { status: 404 } }));
    api.getWorkspaces.mockResolvedValue({ data: [] });
    api.getWorkspaceGithubInstallation.mockResolvedValue({ data: { own: null, effective: null, source: 'default' } });
  });

  const show = (workspace) => render(
    <WorkspaceContext.Provider value={{ selectedWorkspace: workspace }}>
      <I18nProvider><GitConnector /></I18nProvider>
    </WorkspaceContext.Provider>,
  );

  it('carries the selected workspace on load, falling back to "default"', async () => {
    api.getGitConfig.mockResolvedValue({ data: { ...BASE_CONFIG, sources: {} } });
    show('');
    await waitFor(() => expect(api.getGitConfig).toHaveBeenCalledWith('default'));
  });

  it('shows github read only with "Define for this workspace" when only the default defines it', async () => {
    api.getGitConfig.mockResolvedValue({
      data: {
        ...BASE_CONFIG,
        github: { has_token: true },
        sources: { github: 'default', gitlab: 'here', bitbucket: 'here', gitea: 'here' },
      },
    });
    show('acme');
    const badges = await screen.findAllByTestId('connector-source-badge');
    expect(badges[0].textContent).toMatch(/default workspace/i);
    // The github section's token field is read only; the others are not.
    const tokenInputs = screen.getAllByPlaceholderText('Leave empty to keep the existing token');
    expect(tokenInputs[0]).toBeDisabled();
    expect(screen.getAllByTestId('connector-define-here')).toHaveLength(1);
  });

  it('"Define for this workspace" makes the token field editable and saves with the workspace', async () => {
    api.getGitConfig.mockResolvedValue({
      data: { ...BASE_CONFIG, sources: { github: 'default', gitlab: 'here', bitbucket: 'here', gitea: 'here' } },
    });
    api.updateGitConfig.mockResolvedValue({
      data: { ...BASE_CONFIG, github: { has_token: true }, sources: { github: 'here', gitlab: 'here', bitbucket: 'here', gitea: 'here' } },
    });
    show('acme');
    const defineButton = await screen.findByTestId('connector-define-here');
    act(() => { defineButton.click(); });
    const tokenInput = screen.getAllByPlaceholderText(/token/i)[0];
    await waitFor(() => expect(tokenInput).not.toBeDisabled());
    fireEvent.change(tokenInput, { target: { value: 'ghp_secret' } });
    fireEvent.click(screen.getAllByText('Save')[0]);
    await waitFor(() => expect(api.updateGitConfig).toHaveBeenCalledWith(
      { provider: 'github', token: 'ghp_secret' }, 'acme',
    ));
  });

  it('offers "Remove, use the default\'s" once this workspace has its own token, calling DELETE with the provider', async () => {
    api.getGitConfig.mockResolvedValue({
      data: {
        ...BASE_CONFIG,
        github: { has_token: true },
        sources: { github: 'here', gitlab: 'here', bitbucket: 'here', gitea: 'here' },
      },
    });
    api.deleteGitConfig.mockResolvedValue({
      data: { ...BASE_CONFIG, sources: { github: 'default', gitlab: 'here', bitbucket: 'here', gitea: 'here' } },
    });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    show('acme');
    // Every provider is "here" in this fixture; the github card is first.
    const removeButtons = await screen.findAllByTestId('connector-remove-here');
    act(() => { removeButtons[0].click(); });
    await waitFor(() => expect(api.deleteGitConfig).toHaveBeenCalledWith('github', 'acme'));
    confirmSpy.mockRestore();
  });

  it('lists the other workspaces defining their own github token, from the default workspace', async () => {
    api.getGitConfig.mockResolvedValue({
      data: {
        ...BASE_CONFIG,
        sources: { github: 'here', gitlab: 'here', bitbucket: 'here', gitea: 'here' },
        defined_in: { github: ['default', 'acme'], gitlab: ['default'], bitbucket: ['default'], gitea: ['default'] },
      },
    });
    show('default');
    const note = await screen.findByTestId('connector-defined-in');
    expect(note.textContent).toMatch(/acme/);
  });
});
