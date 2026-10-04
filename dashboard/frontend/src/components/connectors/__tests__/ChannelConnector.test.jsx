import { act, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { StreamContext } from '../../stream';
import { WorkspaceContext } from '../../workspace';
import { I18nProvider } from '../../../i18n';
import ChannelConnector from '../ChannelConnector';

// Slack, Discord, Teams and mail all go through this one component
// (routes/channels.py). Scoped per workspace like the credential connectors
// (connectors/channels/store.py): the default workspace's bot works
// everywhere, another workspace's own bot works only there.

const api = vi.hoisted(() => ({
  listChannels: vi.fn(),
  getChannelConfig: vi.fn(),
  updateChannelConfig: vi.fn(),
  deleteChannelConfig: vi.fn(),
  testChannel: vi.fn(),
  getChannelStatus: vi.fn(),
  getChannelBindings: vi.fn(),
  createChannelBinding: vi.fn(),
  deleteChannelBinding: vi.fn(),
  getAgents: vi.fn(),
  listFlows: vi.fn(),
  getGmailStatus: vi.fn(),
}));
vi.mock('../../../api', () => api);

const silentStream = { connected: true, on: () => () => {}, acquireChannel: () => () => {}, onRefetch: () => () => {} };

const SPECS = [{ name: 'slack', fields: [{ key: 'bot_token', secret: true, kind: 'password', required: true, options: [] }], has_loop: true }];

describe('ChannelConnector: per workspace source', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listChannels.mockResolvedValue({ data: SPECS });
    api.getChannelStatus.mockResolvedValue({ data: { running: false } });
    api.getChannelBindings.mockResolvedValue({ data: [] });
    api.getAgents.mockResolvedValue({ data: [] });
    api.listFlows.mockResolvedValue({ data: [] });
  });

  const show = (workspace) => render(
    <StreamContext.Provider value={silentStream}>
      <WorkspaceContext.Provider value={{ selectedWorkspace: workspace }}>
        <I18nProvider><ChannelConnector name="slack" /></I18nProvider>
      </WorkspaceContext.Provider>
    </StreamContext.Provider>,
  );

  it('carries the selected workspace on config, status and bindings calls', async () => {
    api.getChannelConfig.mockResolvedValue({ data: { config: {}, enabled: false, configured: false, allowed: [], source: 'here' } });
    show('acme');
    await waitFor(() => expect(api.getChannelConfig).toHaveBeenCalledWith('slack', 'acme'));
    expect(api.getChannelStatus).toHaveBeenCalledWith('slack', 'acme');
    expect(api.getChannelBindings).toHaveBeenCalledWith('slack', 'acme');
  });

  it('falls back to "default" when no workspace is selected', async () => {
    api.getChannelConfig.mockResolvedValue({ data: { config: {}, enabled: false, configured: false, allowed: [], source: 'here' } });
    show('');
    await waitFor(() => expect(api.getChannelConfig).toHaveBeenCalledWith('slack', 'default'));
  });

  it('shows read only fields and "Define for this workspace" when the channel is only the default\'s', async () => {
    api.getChannelConfig.mockResolvedValue({
      data: { config: { has_bot_token: true }, enabled: true, configured: true, allowed: [], source: 'default', has_loop: true },
    });
    show('acme');
    const badge = await screen.findByTestId('connector-source-badge');
    expect(badge.textContent).toMatch(/default workspace/i);
    expect(screen.getByTestId('connector-define-here')).toBeInTheDocument();
    // Saving is not offered while the form only shows the inherited values.
    expect(screen.queryByText('Save')).toBeNull();
  });

  it('offers "Remove, use the default\'s" once this workspace has its own channel, and calls DELETE on confirm', async () => {
    api.getChannelConfig.mockResolvedValue({
      data: { config: { has_bot_token: true }, enabled: true, configured: true, allowed: [], source: 'here', has_loop: true },
    });
    api.deleteChannelConfig.mockResolvedValue({
      data: { config: {}, enabled: false, configured: false, allowed: [], source: 'default', has_loop: true },
    });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    show('acme');
    const removeButton = await screen.findByTestId('connector-remove-here');
    act(() => { removeButton.click(); });
    await waitFor(() => expect(api.deleteChannelConfig).toHaveBeenCalledWith('slack', 'acme'));
    confirmSpy.mockRestore();
  });

  it('shows the badge but no define/remove switch in the default workspace', async () => {
    api.getChannelConfig.mockResolvedValue({
      data: { config: {}, enabled: false, configured: false, allowed: [], source: 'here', has_loop: true, defined_in: ['default'] },
    });
    show('default');
    await screen.findByTestId('connector-source-badge');
    expect(screen.queryByTestId('connector-define-here')).toBeNull();
    expect(screen.queryByTestId('connector-remove-here')).toBeNull();
  });
});
