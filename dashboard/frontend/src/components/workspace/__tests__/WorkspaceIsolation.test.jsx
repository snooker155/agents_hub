import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

import { I18nProvider, translate } from '../../../i18n';

// The perimeter around a workspace (common/isolation.py, routes/isolation.py):
// the switch, the hub's readiness checks, the sites an isolated run may read
// from, and the agents the workspace owns that hold a tool from outside the
// allowlist. Each test uses its own workspace name so the hook's module level
// cache (shared with the badge and the tool picker) never leaks state between
// cases that happen to run in the same file.

const ok = (data) => Promise.resolve({ data });
// A rejection value for `mockRejectedValue`, not a pre-built rejected promise:
// building one eagerly (`Promise.reject(...)`) races the test runner's
// unhandled rejection detector against the `await` inside the component.
const failure = (status, detail) => ({ response: { status, data: { detail } } });

const getWorkspaceIsolation = vi.fn();
const updateWorkspaceIsolation = vi.fn();

vi.mock('../../../api/isolation', () => ({
  getWorkspaceIsolation: (...a) => getWorkspaceIsolation(...a),
  updateWorkspaceIsolation: (...a) => updateWorkspaceIsolation(...a),
}));

import WorkspaceIsolation from '../WorkspaceIsolation';

const t = (key, vars) => translate('en', key, vars);
const wrap = (node) => render(<MemoryRouter><I18nProvider>{node}</I18nProvider></MemoryRouter>);

const state = (overrides = {}) => ({
  workspace: 'ws',
  isolated: false,
  allow_domains: [],
  changed_at: null,
  changed_by: null,
  readiness: [
    { id: 'docker', ok: true, detail: 'Docker 24.0.5' },
    { id: 'sandbox_image', ok: false, detail: 'build the sandbox image first' },
  ],
  offending_agents: [],
  allowed_tools: ['read_file', 'run_shell'],
  gateway_tools: ['fetch_url'],
  ...overrides,
});

// vite.config.js sets `test.restoreMocks: true`: every mock is restored to a
// clean slate before each test runs here, so no local beforeEach is needed.
describe('WorkspaceIsolation', () => {
  it('renders the explainer, the switch and every readiness check with its detail', async () => {
    getWorkspaceIsolation.mockReturnValue(ok(state({ workspace: 'ws-render' })));
    wrap(<WorkspaceIsolation workspace="ws-render" />);
    expect(await screen.findByText(t('isolation.explainer'))).toBeInTheDocument();
    const list = await screen.findByTestId('isolation-readiness');
    expect(list).toHaveTextContent(t('isolation.checks.docker'));
    expect(list).toHaveTextContent('Docker 24.0.5');
    expect(list).toHaveTextContent(t('isolation.checks.sandbox_image'));
    expect(list).toHaveTextContent('build the sandbox image first');
    expect(screen.getByRole('checkbox')).not.toBeChecked();
  });

  it('falls back to the raw id for a readiness check it has no label for', async () => {
    getWorkspaceIsolation.mockReturnValue(ok(state({
      workspace: 'ws-unknown-check',
      readiness: [{ id: 'some_future_check', ok: false, detail: 'not wired yet' }],
    })));
    wrap(<WorkspaceIsolation workspace="ws-unknown-check" />);
    const list = await screen.findByTestId('isolation-readiness');
    expect(list).toHaveTextContent('some_future_check');
    expect(list).toHaveTextContent('not wired yet');
  });

  it('turns isolation on with a PUT and no confirmation', async () => {
    getWorkspaceIsolation.mockReturnValue(ok(state({ workspace: 'ws-on' })));
    updateWorkspaceIsolation.mockReturnValue(ok(state({ workspace: 'ws-on', isolated: true })));
    const confirmSpy = vi.spyOn(window, 'confirm');
    wrap(<WorkspaceIsolation workspace="ws-on" />);
    const toggle = await screen.findByRole('checkbox');
    fireEvent.click(toggle);
    await waitFor(() => expect(updateWorkspaceIsolation).toHaveBeenCalledWith('ws-on', { isolated: true }));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(await screen.findByText(t('isolation.statusOn'))).toBeInTheDocument();
  });

  it('asks for confirmation before turning isolation off, and does nothing when declined', async () => {
    getWorkspaceIsolation.mockReturnValue(ok(state({ workspace: 'ws-off-decline', isolated: true })));
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    wrap(<WorkspaceIsolation workspace="ws-off-decline" />);
    const toggle = await screen.findByRole('checkbox');
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    expect(window.confirm).toHaveBeenCalledWith(t('isolation.confirmOff'));
    expect(updateWorkspaceIsolation).not.toHaveBeenCalled();
  });

  it('turns isolation off once the confirmation is accepted', async () => {
    getWorkspaceIsolation.mockReturnValue(ok(state({ workspace: 'ws-off-accept', isolated: true })));
    updateWorkspaceIsolation.mockReturnValue(ok(state({ workspace: 'ws-off-accept', isolated: false })));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    wrap(<WorkspaceIsolation workspace="ws-off-accept" />);
    const toggle = await screen.findByRole('checkbox');
    fireEvent.click(toggle);
    await waitFor(() => expect(updateWorkspaceIsolation).toHaveBeenCalledWith('ws-off-accept', { isolated: false }));
    expect(await screen.findByText(t('isolation.statusOff'))).toBeInTheDocument();
  });

  it('shows the 409 the backend sends back when the switch cannot turn on', async () => {
    getWorkspaceIsolation.mockReturnValue(ok(state({ workspace: 'ws-409' })));
    updateWorkspaceIsolation.mockRejectedValue(failure(409, 'The hub is not ready for an isolated workspace: sandbox_image: build it first'));
    wrap(<WorkspaceIsolation workspace="ws-409" />);
    const toggle = await screen.findByRole('checkbox');
    fireEvent.click(toggle);
    expect(await screen.findByTestId('isolation-error')).toHaveTextContent('sandbox_image: build it first');
  });

  it('saves the domains textarea as one host per line', async () => {
    getWorkspaceIsolation.mockReturnValue(ok(state({ workspace: 'ws-domains', allow_domains: ['wikipedia.org'] })));
    updateWorkspaceIsolation.mockReturnValue(ok(state({
      workspace: 'ws-domains', allow_domains: ['wikipedia.org', 'api.example.com'],
    })));
    wrap(<WorkspaceIsolation workspace="ws-domains" />);
    const textarea = await screen.findByLabelText(t('isolation.domains'));
    expect(textarea).toHaveValue('wikipedia.org');
    fireEvent.change(textarea, { target: { value: 'wikipedia.org\napi.example.com\n\n  ' } });
    fireEvent.click(screen.getByRole('button', { name: t('isolation.saveDomains') }));
    await waitFor(() => expect(updateWorkspaceIsolation).toHaveBeenCalledWith(
      'ws-domains', { allow_domains: ['wikipedia.org', 'api.example.com'] },
    ));
    expect(await screen.findByText(t('isolation.domainsSaved'))).toBeInTheDocument();
  });

  it('shows the 400 the backend sends back for a bad domain', async () => {
    getWorkspaceIsolation.mockReturnValue(ok(state({ workspace: 'ws-bad-domain' })));
    updateWorkspaceIsolation.mockRejectedValue(failure(400, 'allow_domains: not a host'));
    wrap(<WorkspaceIsolation workspace="ws-bad-domain" />);
    const textarea = await screen.findByLabelText(t('isolation.domains'));
    fireEvent.change(textarea, { target: { value: 'not a host' } });
    fireEvent.click(screen.getByRole('button', { name: t('isolation.saveDomains') }));
    expect(await screen.findByTestId('isolation-error')).toHaveTextContent('allow_domains: not a host');
  });

  it('lists the agents that hold a tool outside the allowlist, linked to their agent page', async () => {
    getWorkspaceIsolation.mockReturnValue(ok(state({
      workspace: 'ws-offenders',
      offending_agents: [{ agent_id: 'scout', tools: ['browser_act', 'mcp__tickets__search'] }],
    })));
    wrap(<WorkspaceIsolation workspace="ws-offenders" />);
    const list = await screen.findByTestId('isolation-offending');
    expect(list).toHaveTextContent('browser_act, mcp__tickets__search');
    expect(screen.getByRole('link', { name: 'scout' })).toHaveAttribute('href', '/agents/scout');
  });

  it('shows nothing load failed beyond the error line', async () => {
    getWorkspaceIsolation.mockRejectedValue(failure(403, 'Administrator access required'));
    wrap(<WorkspaceIsolation workspace="ws-load-fail" />);
    expect(await screen.findByText('Administrator access required')).toBeInTheDocument();
    expect(screen.queryByTestId('isolation-readiness')).toBeNull();
  });
});
