import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

const { ok, api, getAgents, getWorkspaces } = vi.hoisted(() => {
  const resolve = (data) => Promise.resolve({ data });
  return {
    ok: resolve,
    api: {},
    getAgents: vi.fn(() => resolve([{ id: 'helper', name: 'Helper' }])),
    getWorkspaces: vi.fn(() => resolve([{ name: 'default' }, { name: 'team' }])),
  };
});

const INSTALL = {
  org_id: 'T123', name: 'Acme', status: 'pending', workspace: '', agent_id: '', via: 'hub',
  installed_at: '2026-10-01T10:00:00Z', has_bot_token: true,
};

const info = (over = {}) => ({
  public_url: 'https://hub.example', public_url_configured: true, auth_mode: 'multi', warnings: [],
  mcp: { url: 'https://hub.example/v1/mcp', server_name: 'agents-hub', workspace_header: 'X-Agents-Hub-Workspace' },
  obsidian: { available: true, version: '0.1.0', plugin_id: 'agents-hub' },
  slack: {
    available: true, workspace: 'default', configured: true, enabled: true, distribution: 'private',
    app_name: 'Hub', installs: [INSTALL], mode: 'events', oauth_ready: true, has_signing_secret: true,
    public_install_url: null, redirect_url: 'https://hub.example/r', events_url: 'https://hub.example/e',
  },
  teams: {
    available: true, workspace: 'default', configured: true, enabled: true, distribution: 'private',
    app_name: 'Hub', installs: [], app_id: 'abc', messaging_endpoint: 'https://hub.example/api/messages',
  },
  ...over,
});

Object.assign(api, {
  getDistribution: vi.fn(() => ok(info())),
  downloadObsidianPlugin: vi.fn(() => ok(new Blob(['x']))),
  downloadTeamsPackage: vi.fn(() => Promise.reject({ response: { status: 409, data: { detail: 'Set the Teams app id first' } } })),
  getSlackManifest: vi.fn(() => ok({ manifest: { a: 1 }, create_app_url: 'https://api.slack.com/apps' })),
  createSlackInstallLink: vi.fn(() => ok({ url: 'https://slack.com/oauth' })),
  getInstalls: vi.fn(() => ok({ workspace: 'default', installs: [] })),
  addInstall: vi.fn(() => ok({})),
  updateInstall: vi.fn(() => ok({})),
  removeInstall: vi.fn(() => ok({})),
  saveBlob: vi.fn(),
});

vi.mock('../../api/distribution', () => Object.fromEntries(
  ['getDistribution', 'downloadObsidianPlugin', 'downloadTeamsPackage', 'getSlackManifest', 'createSlackInstallLink',
    'getInstalls', 'addInstall', 'updateInstall', 'removeInstall', 'saveBlob']
    .map((name) => [name, (...args) => api[name](...args)]),
));

vi.mock('../../api', () => ({
  API_ORIGIN: '',
  getAgents: (...args) => getAgents(...args),
  getWorkspaces: (...args) => getWorkspaces(...args),
}));

import Distribution from '../Distribution';

const show = (path = '/distribution') => render(
  <I18nProvider><MemoryRouter initialEntries={[path]}><Distribution /></MemoryRouter></I18nProvider>,
);

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockClear());
  api.getDistribution.mockImplementation(() => ok(info()));
});

describe('Distribution page', () => {
  it('puts the address and the typed key into the snippet of the chosen client', async () => {
    show();
    const snippet = await screen.findByTestId('mcp-snippet');
    expect(snippet.textContent).toContain('claude mcp add --transport http agents-hub https://hub.example/v1/mcp');
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'ah_secret' } });
    expect(screen.getByTestId('mcp-snippet').textContent).toContain("'Authorization: Bearer ah_secret'");
    fireEvent.click(screen.getByRole('button', { name: 'Cursor' }));
    expect(screen.getByTestId('mcp-snippet').textContent).toContain('"Authorization": "Bearer ah_secret"');
    expect(screen.getByRole('link', { name: /add to cursor/i }).getAttribute('href')).toMatch(/^cursor:\/\//);
    fireEvent.click(screen.getByRole('button', { name: 'VS Code' }));
    expect(screen.getByTestId('mcp-snippet').textContent).toContain('"type": "http"');
    expect(screen.getByRole('link', { name: /add to vs code/i }).getAttribute('href')).toMatch(/^vscode:mcp\/install\?/);
    fireEvent.click(screen.getByRole('button', { name: 'Codex CLI' }));
    expect(screen.getByTestId('mcp-snippet').textContent).toContain('[mcp_servers.agents-hub]');
    expect(screen.queryByRole('link', { name: /add to/i })).toBeNull();
    expect(screen.getByText('ah mcp connect codex --write')).toBeInTheDocument();
  });

  it('leaves the Authorization header out when the hub has no sign-in', async () => {
    api.getDistribution.mockImplementation(() => ok(info({ auth_mode: 'single' })));
    show();
    const snippet = await screen.findByTestId('mcp-snippet');
    expect(snippet.textContent).not.toContain('Authorization');
    fireEvent.click(screen.getByRole('button', { name: 'Windsurf' }));
    expect(screen.getByTestId('mcp-snippet').textContent).not.toContain('headers');
  });

  it('approves a pending install with its workspace and agent', async () => {
    show();
    await screen.findByText('Acme');
    const approve = screen.getByRole('button', { name: /approve/i });
    expect(approve).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Workspace Acme'), { target: { value: 'team' } });
    await waitFor(() => expect(getAgents).toHaveBeenCalledWith('team'));
    await screen.findAllByRole('option', { name: 'Helper' });
    fireEvent.change(screen.getByLabelText('Agent Acme'), { target: { value: 'helper' } });
    fireEvent.click(screen.getByRole('button', { name: /approve/i }));
    await waitFor(() => expect(api.updateInstall).toHaveBeenCalledWith(
      'slack', 'T123', { status: 'approved', workspace: 'team', agent_id: 'helper' },
    ));
  });

  it('shows the server detail when the Teams package cannot be built', async () => {
    show();
    fireEvent.click(await screen.findByRole('button', { name: /download app package/i }));
    expect(await screen.findByText(/Set the Teams app id first/)).toBeInTheDocument();
  });

  it('warns about a missing public URL and shows the install banner', async () => {
    api.getDistribution.mockImplementation(() => ok(info({ warnings: ['public_url_unset'] })));
    show('/distribution?installed=slack&org=T9');
    expect(await screen.findByText(/AGENTS_HUB_PUBLIC_URL/)).toBeInTheDocument();
    expect(screen.getByText(/installed for T9/)).toBeInTheDocument();
  });
});
