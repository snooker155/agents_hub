import { act, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { WorkspaceContext } from '../../workspace';
import { I18nProvider } from '../../../i18n';
import CredentialConnector from '../CredentialConnector';

// Jira, Linear, Google, Microsoft, Notion and Confluence all go through this
// one component (routes/connectors.py). It is scoped per workspace like
// every other connector (connectors/channels/store.py): the default
// workspace's definition works everywhere, another workspace's own
// definition works only there. These tests stand in for all six names with
// "jira", which is what the page actually shows first.

const api = vi.hoisted(() => ({
  listConnectors: vi.fn(),
  getConnectorConfig: vi.fn(),
  updateConnectorConfig: vi.fn(),
  deleteConnectorConfig: vi.fn(),
  testConnector: vi.fn(),
}));
vi.mock('../../../api', () => api);

const SPECS = [{ name: 'jira', fields: [{ key: 'base_url', kind: 'text', required: true, options: [] }] }];

describe('CredentialConnector: per workspace source', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listConnectors.mockResolvedValue({ data: SPECS });
  });

  const show = (workspace) => render(
    <WorkspaceContext.Provider value={{ selectedWorkspace: workspace }}>
      <I18nProvider><CredentialConnector name="jira" /></I18nProvider>
    </WorkspaceContext.Provider>,
  );

  it('passes the selected workspace on every call, falling back to "default" when none is selected', async () => {
    api.getConnectorConfig.mockResolvedValue({ data: { config: {}, configured: false, source: 'here' } });
    show('');
    await waitFor(() => expect(api.getConnectorConfig).toHaveBeenCalledWith('jira', 'default'));
  });

  it('carries a real workspace selection the same way', async () => {
    api.getConnectorConfig.mockResolvedValue({ data: { config: {}, configured: false, source: 'here' } });
    show('acme');
    await waitFor(() => expect(api.getConnectorConfig).toHaveBeenCalledWith('jira', 'acme'));
  });

  it('shows "Defined in this workspace" with no define/remove switch in the default workspace', async () => {
    api.getConnectorConfig.mockResolvedValue({ data: { config: {}, configured: false, source: 'here', defined_in: ['default'] } });
    show('default');
    const badge = await screen.findByTestId('connector-source-badge');
    expect(badge.textContent).toMatch(/this workspace/i);
    expect(screen.queryByTestId('connector-define-here')).toBeNull();
    expect(screen.queryByTestId('connector-remove-here')).toBeNull();
    expect(screen.queryByTestId('connector-defined-in')).toBeNull();
  });

  it('lists the other workspaces that keep their own copy, from the default workspace', async () => {
    api.getConnectorConfig.mockResolvedValue({
      data: { config: {}, configured: false, source: 'here', defined_in: ['default', 'acme', 'beta'] },
    });
    show('default');
    const note = await screen.findByTestId('connector-defined-in');
    expect(note.textContent).toMatch(/acme, beta/);
  });

  it('from a non default workspace inheriting the default, shows read only fields and "Define for this workspace"', async () => {
    api.getConnectorConfig.mockResolvedValue({
      data: { config: { base_url: 'https://acme.atlassian.net' }, configured: true, source: 'default' },
    });
    show('acme');
    const badge = await screen.findByTestId('connector-source-badge');
    expect(badge.textContent).toMatch(/default workspace/i);
    const field = await screen.findByDisplayValue('https://acme.atlassian.net');
    expect(field).toBeDisabled();
    expect(screen.queryByText('Save')).toBeNull();
    expect(screen.getByTestId('connector-define-here')).toBeInTheDocument();
  });

  it('"Define for this workspace" switches the form to editable; saving defines it here', async () => {
    api.getConnectorConfig.mockResolvedValue({
      data: { config: { base_url: 'https://acme.atlassian.net' }, configured: true, source: 'default' },
    });
    api.updateConnectorConfig.mockResolvedValue({
      data: { config: { base_url: 'https://acme.atlassian.net' }, configured: true, source: 'here' },
    });
    show('acme');
    const defineButton = await screen.findByTestId('connector-define-here');
    act(() => { defineButton.click(); });
    const field = await screen.findByDisplayValue('https://acme.atlassian.net');
    await waitFor(() => expect(field).not.toBeDisabled());
    screen.getByText('Save').click();
    await waitFor(() => expect(api.updateConnectorConfig).toHaveBeenCalledWith(
      'jira', { config: { base_url: 'https://acme.atlassian.net' } }, 'acme',
    ));
  });

  it('offers "Remove, use the default\'s" once this workspace has its own copy, and calls DELETE on confirm', async () => {
    api.getConnectorConfig.mockResolvedValue({ data: { config: {}, configured: true, source: 'here' } });
    api.deleteConnectorConfig.mockResolvedValue({ data: { config: {}, configured: false, source: 'default' } });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    show('acme');
    const removeButton = await screen.findByTestId('connector-remove-here');
    act(() => { removeButton.click(); });
    await waitFor(() => expect(api.deleteConnectorConfig).toHaveBeenCalledWith('jira', 'acme'));
    expect(confirmSpy).toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('does not call DELETE when the confirmation is declined', async () => {
    api.getConnectorConfig.mockResolvedValue({ data: { config: {}, configured: true, source: 'here' } });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    show('acme');
    const removeButton = await screen.findByTestId('connector-remove-here');
    act(() => { removeButton.click(); });
    expect(api.deleteConnectorConfig).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});
