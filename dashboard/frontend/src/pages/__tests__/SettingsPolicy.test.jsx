import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import Settings from '../Settings';
import { I18nProvider } from '../../i18n';

// The approval gate and the hooks are read live by the agent process, so they
// are saved through their own endpoint rather than the page's workspace Save.
// These tests cover that the toggle writes straight away, that the hooks box is
// prefilled from what the workspace holds, and that invalid JSON is refused
// here rather than sent to the backend.

const ok = (data) => Promise.resolve({ data });
const updateSettings = vi.fn(() => ok({}));

const HOOKS = {
  PreToolUse: [{ matcher: 'run_shell', type: 'command', command: './check.sh' }],
};

const updateWorkspacePolicy = vi.fn((_name, patch) => ok({
  require_tool_approval: patch.require_tool_approval ?? false,
  hooks: patch.hooks ?? HOOKS,
}));

// The tool policy and agent loop cards on this page have their own tests
// (components/__tests__); here they would only need more of the api mocked.
vi.mock('../../components/settings/ToolPolicySettings', () => ({ default: () => null }));
vi.mock('../../components/settings/LoopSettingsWorkspace', () => ({ default: () => null }));

vi.mock('axios', () => ({
  default: {
    create: () => ({
      get: () => ok({ env_defined_fields: [], backends: [], adapters: [] }),
      post: () => ok({ ok: true }),
      put: () => ok({}),
      delete: () => ok({}),
    }),
  },
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'default' }),
}));

vi.mock('../../api', () => ({
  getSettings: () => ok({ env_defined_fields: [] }),
  testProvider: () => ok({ ok: true }),
  testLocalModel: () => ok({ ok: true, models: [] }),
  getWorkspaceSettingsOverrides: () => ok({ overrides: {} }),
  updateWorkspaceSettingsOverrides: () => ok({}),
  getWorkspacePolicy: () => ok({ require_tool_approval: false, hooks: HOOKS }),
  updateWorkspacePolicy: (...args) => updateWorkspacePolicy(...args),
  updateSettings: (...args) => updateSettings(...args),
  getApiToken: () => '',
  setApiToken: () => {},
  API_ORIGIN: 'http://localhost:8000',
}));

const renderExecution = (path = '/settings/toolPolicy') => render(
  <I18nProvider>
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/settings/:section" element={<Settings />} /></Routes>
    </MemoryRouter>
  </I18nProvider>,
);

/** The approval toggle is the only checkbox in the Tool Policy card. */
const policyCard = () => screen.getByText('Tool Policy').closest('section');
const approvalToggle = () => policyCard().querySelector('input[type="checkbox"]');

describe('Settings tool policy', () => {
  it('shows the approval toggle with its one line of explanation', async () => {
    const { container } = renderExecution();
    await waitFor(() => expect(container.textContent).toMatch(/Tool Policy/));
    expect(screen.getByText('Tool approval')).toBeTruthy();
    expect(container.textContent).toMatch(/Hold a tool call until a person approves it/);
    expect(approvalToggle().checked).toBe(false);
  });

  it('keeps a toggled gate until the page is saved, and saves it then', async () => {
    updateWorkspacePolicy.mockClear();
    const { container } = renderExecution();
    await waitFor(() => expect(container.textContent).toMatch(/Tool Policy/));
    expect(screen.getByTestId('settings-save')).toBeDisabled();

    fireEvent.click(approvalToggle());
    expect(updateWorkspacePolicy).not.toHaveBeenCalled();
    expect(approvalToggle().checked).toBe(true);
    expect(screen.getByTestId('settings-save')).toBeEnabled();
    // Switching it back is no change: the Save button is inactive again.
    fireEvent.click(approvalToggle());
    await waitFor(() => expect(screen.getByTestId('settings-save')).toBeDisabled());

    fireEvent.click(approvalToggle());
    fireEvent.click(await screen.findByTestId('settings-save'));
    await waitFor(() => expect(updateWorkspacePolicy).toHaveBeenCalledWith(
      'default', { require_tool_approval: true },
    ));
  });

  it('prefills the hooks box with what the workspace holds', async () => {
    const { container } = renderExecution();
    await waitFor(() => expect(container.textContent).toMatch(/Tool Policy/));
    const box = policyCard().querySelector('textarea');
    expect(JSON.parse(box.value)).toEqual(HOOKS);
  });

  it('refuses invalid hook JSON without calling the backend', async () => {
    updateWorkspacePolicy.mockClear();
    const { container } = renderExecution();
    await waitFor(() => expect(container.textContent).toMatch(/Tool Policy/));

    const box = policyCard().querySelector('textarea');
    fireEvent.change(box, { target: { value: '{ not json' } });
    fireEvent.click(await screen.findByTestId('settings-save'));

    await waitFor(() => expect(container.textContent).toMatch(/not valid JSON/));
    expect(updateWorkspacePolicy).not.toHaveBeenCalled();
  });

  it('sends parsed hooks on save', async () => {
    updateWorkspacePolicy.mockClear();
    const { container } = renderExecution();
    await waitFor(() => expect(container.textContent).toMatch(/Tool Policy/));

    const box = policyCard().querySelector('textarea');
    fireEvent.change(box, { target: { value: '{"PostToolUse": []}' } });
    fireEvent.click(await screen.findByTestId('settings-save'));

    await waitFor(() => expect(updateWorkspacePolicy).toHaveBeenCalledWith(
      'default', { hooks: { PostToolUse: [] } },
    ));
  });
});

describe('Settings capability guard', () => {
  it('makes the Save button active once the mode is switched, and writes it only on Save', async () => {
    updateSettings.mockClear();
    const { container } = renderExecution('/settings/execution');
    await waitFor(() => expect(container.querySelector('input[type="radio"][value="warn"]')).not.toBeNull());
    expect(screen.getByTestId('settings-save')).toBeDisabled();
    fireEvent.click(container.querySelector('input[type="radio"][value="warn"]'));
    expect(updateSettings).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId('settings-save'));
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith({ capability_guard: 'warn' }));
  });
});

describe('Settings agents group', () => {
  it('names the sections the way the workspace page did, and points to what stayed there', async () => {
    renderExecution('/settings/tasks');
    expect(await screen.findByText('Assignment Mode')).toBeTruthy();
    const menu = document.querySelector('nav.md\\:block');
    for (const label of ['Execution', 'Tool policy', 'Web access', 'Task assignment']) {
      expect(menu.textContent).toContain(label);
    }
    expect(screen.queryByText('Tool Policy')).toBeNull();
    const link = screen.getByRole('link', { name: /Settings of workspace default/ });
    expect(link.getAttribute('href')).toBe('/workspaces/default?tab=settings&section=personalMemory');
  });
});
