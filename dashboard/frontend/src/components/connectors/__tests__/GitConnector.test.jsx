import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { WorkspaceContext } from '../../workspace';
import { I18nProvider } from '../../../i18n';
import GitConnector from '../GitConnector';

// The GitLab token and base URL, scoped per workspace like the other
// connectors (connectors/channels/store.py): the default workspace's token
// works everywhere, another workspace's own token works only there. The
// config GET answers `sources` ("here" or "default") and, from the default
// workspace, `defined_in`.

const api = vi.hoisted(() => ({
  getGitConfig: vi.fn(),
  updateGitConfig: vi.fn(),
  deleteGitConfig: vi.fn(),
  testGitConnection: vi.fn(),
  getWorkspaces: vi.fn(() => Promise.resolve({ data: [] })),
}));
vi.mock('../../../api', () => api);

const BASE_CONFIG = { gitlab: { has_token: false, base_url: '' } };

describe('GitConnector: per workspace source', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getWorkspaces.mockResolvedValue({ data: [] });
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

  it('shows the token read only with "Define for this workspace" when only the default defines it', async () => {
    api.getGitConfig.mockResolvedValue({
      data: { gitlab: { has_token: true, base_url: '' }, sources: { gitlab: 'default' } },
    });
    show('acme');
    const badge = await screen.findByTestId('connector-source-badge');
    expect(badge.textContent).toMatch(/default workspace/i);
    expect(screen.getByPlaceholderText('Leave empty to keep the existing token')).toBeDisabled();
    expect(screen.getByTestId('connector-define-here')).toBeInTheDocument();
  });

  it('"Define for this workspace" makes the token field editable and saves with the workspace', async () => {
    api.getGitConfig.mockResolvedValue({ data: { ...BASE_CONFIG, sources: { gitlab: 'default' } } });
    api.updateGitConfig.mockResolvedValue({
      data: { gitlab: { has_token: true, base_url: '' }, sources: { gitlab: 'here' } },
    });
    show('acme');
    const defineButton = await screen.findByTestId('connector-define-here');
    act(() => { defineButton.click(); });
    const tokenInput = screen.getByPlaceholderText(/token/i);
    await waitFor(() => expect(tokenInput).not.toBeDisabled());
    fireEvent.change(tokenInput, { target: { value: 'glpat_secret' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(api.updateGitConfig).toHaveBeenCalledWith(
      { provider: 'gitlab', token: 'glpat_secret' }, 'acme',
    ));
  });

  it('offers "Remove, use the default\'s" once this workspace has its own token, calling DELETE with the provider', async () => {
    api.getGitConfig.mockResolvedValue({
      data: { gitlab: { has_token: true, base_url: '' }, sources: { gitlab: 'here' } },
    });
    api.deleteGitConfig.mockResolvedValue({ data: { ...BASE_CONFIG, sources: { gitlab: 'default' } } });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    show('acme');
    const removeButton = await screen.findByTestId('connector-remove-here');
    act(() => { removeButton.click(); });
    await waitFor(() => expect(api.deleteGitConfig).toHaveBeenCalledWith('gitlab', 'acme'));
    confirmSpy.mockRestore();
  });

  it('lists the other workspaces defining their own token, from the default workspace', async () => {
    api.getGitConfig.mockResolvedValue({
      data: { ...BASE_CONFIG, sources: { gitlab: 'here' }, defined_in: { gitlab: ['default', 'acme'] } },
    });
    show('default');
    const note = await screen.findByTestId('connector-defined-in');
    expect(note.textContent).toMatch(/acme/);
  });
});
