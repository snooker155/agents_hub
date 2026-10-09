import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import Settings from '../Settings';
import { I18nProvider } from '../../i18n';

// The global temperature, its own item in the Models group: the machine-wide value
// (LLM_TEMPERATURE in .env) a model without its own on the Models page runs
// at. It saves straight to the global settings, never into a workspace's
// overrides, and refuses a value outside 0 to 2.

const ok = (data) => Promise.resolve({ data });

const updateSettings = vi.fn(() => ok({ ok: true }));
const updateWorkspaceSettingsOverrides = vi.fn(() => ok({}));

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
  getSettings: () => ok({ env_defined_fields: [], temperature: 0.2 }),
  testProvider: () => ok({ ok: true }),
  testLocalModel: () => ok({ ok: true, models: [] }),
  getWorkspaceSettingsOverrides: () => ok({ overrides: {} }),
  updateWorkspaceSettingsOverrides: (...args) => updateWorkspaceSettingsOverrides(...args),
  getWorkspacePolicy: () => ok({ require_tool_approval: false, hooks: {} }),
  updateWorkspacePolicy: () => ok({}),
  updateSettings: (...args) => updateSettings(...args),
  getApiToken: () => '',
  setApiToken: () => {},
  API_ORIGIN: 'http://localhost:8000',
}));

const renderSection = (section) => render(
  <I18nProvider>
    <MemoryRouter initialEntries={[`/settings/${section}`]}>
      <Routes><Route path="/settings/:section" element={<Settings />} /></Routes>
    </MemoryRouter>
  </I18nProvider>,
);

const field = () => screen.getByRole('spinbutton', { name: 'Temperature' });
const saveButton = () => field().parentElement.querySelector('button');

beforeEach(() => {
  updateSettings.mockClear();
  updateWorkspaceSettingsOverrides.mockClear();
});

describe('Settings global temperature', () => {
  it('is its own item in the Models group, not part of Cloud providers', async () => {
    renderSection('providers');
    await waitFor(() => expect(screen.getAllByText('Temperature').length).toBeGreaterThan(0));
    expect(screen.queryByRole('spinbutton', { name: 'Temperature' })).toBeNull();
    const link = screen.getAllByRole('link').find((a) => a.getAttribute('href') === '/settings/temperature');
    expect(link).toBeTruthy();
  });

  it('shows the stored value and saves a new one globally', async () => {
    renderSection('temperature');
    await waitFor(() => expect(field()).toHaveValue(0.2));
    expect(saveButton()).toBeDisabled();

    fireEvent.change(field(), { target: { value: '0.7' } });
    // A typed value stays typed: nothing resets the draft after the change.
    expect(field()).toHaveValue(0.7);
    expect(saveButton()).toBeEnabled();
    fireEvent.click(saveButton());

    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith({ temperature: 0.7 }));
    expect(updateWorkspaceSettingsOverrides).not.toHaveBeenCalled();
  });

  it('refuses a value outside 0 to 2', async () => {
    renderSection('temperature');
    await waitFor(() => expect(field()).toHaveValue(0.2));
    fireEvent.change(field(), { target: { value: '3' } });
    expect(saveButton()).toBeDisabled();
    expect(screen.getByText('Enter a number from 0 to 2.')).toBeTruthy();
  });
});
