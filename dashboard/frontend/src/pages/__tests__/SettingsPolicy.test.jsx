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

const HOOKS = {
  PreToolUse: [{ matcher: 'run_shell', type: 'command', command: './check.sh' }],
};

const updateWorkspacePolicy = vi.fn((_name, patch) => ok({
  require_tool_approval: patch.require_tool_approval ?? false,
  hooks: patch.hooks ?? HOOKS,
}));

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
  getWorkspaceSettingsOverrides: () => ok({ overrides: {} }),
  updateWorkspaceSettingsOverrides: () => ok({}),
  getWorkspacePolicy: () => ok({ require_tool_approval: false, hooks: HOOKS }),
  updateWorkspacePolicy: (...args) => updateWorkspacePolicy(...args),
  updateSettings: () => ok({}),
  getApiToken: () => '',
  setApiToken: () => {},
  API_ORIGIN: 'http://localhost:8000',
}));

const renderExecution = () => render(
  <I18nProvider>
    <MemoryRouter initialEntries={['/settings/execution']}>
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

  it('saves the gate as soon as it is toggled', async () => {
    updateWorkspacePolicy.mockClear();
    const { container } = renderExecution();
    await waitFor(() => expect(container.textContent).toMatch(/Tool Policy/));

    fireEvent.click(approvalToggle());

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
    fireEvent.click(screen.getByRole('button', { name: /Save hooks/ }));

    await waitFor(() => expect(container.textContent).toMatch(/not valid JSON/));
    expect(updateWorkspacePolicy).not.toHaveBeenCalled();
  });

  it('sends parsed hooks on save', async () => {
    updateWorkspacePolicy.mockClear();
    const { container } = renderExecution();
    await waitFor(() => expect(container.textContent).toMatch(/Tool Policy/));

    const box = policyCard().querySelector('textarea');
    fireEvent.change(box, { target: { value: '{"PostToolUse": []}' } });
    fireEvent.click(screen.getByRole('button', { name: /Save hooks/ }));

    await waitFor(() => expect(updateWorkspacePolicy).toHaveBeenCalledWith(
      'default', { hooks: { PostToolUse: [] } },
    ));
  });
});
