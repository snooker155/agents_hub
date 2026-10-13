import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import Settings from '../Settings';
import { I18nProvider } from '../../i18n';

// Settings → Isolation shows the selected workspace's isolation and data class,
// the same two cards as the workspace's own Isolation tab (which have their own
// tests); here they are stubs that say which workspace they were given.

const ok = (data) => Promise.resolve({ data });

vi.mock('../../components/workspace/WorkspaceIsolation', () => ({
  default: ({ workspace }) => <div data-testid="iso-card">isolation of {workspace}</div>,
}));
vi.mock('../../components/workspace/WorkspaceDataClass', () => ({
  default: ({ workspace }) => <div data-testid="dc-card">data class of {workspace}</div>,
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
  useWorkspace: () => ({ selectedWorkspace: 'team-a' }),
}));

vi.mock('../../api', () => ({
  getSettings: () => ok({ env_defined_fields: [] }),
  getWorkspaceSettingsOverrides: () => ok({ overrides: {} }),
  updateWorkspaceSettingsOverrides: () => ok({}),
  updateSettings: () => ok({}),
  getApiToken: () => '',
  setApiToken: () => {},
  API_ORIGIN: 'http://localhost:8000',
}));

const renderAt = (path) => render(
  <I18nProvider>
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/settings/:section" element={<Settings />} /></Routes>
    </MemoryRouter>
  </I18nProvider>,
);

describe('Settings isolation', () => {
  it('shows both cards for the selected workspace and names it', async () => {
    renderAt('/settings/isolation');
    await waitFor(() => expect(screen.getByTestId('iso-card')).toBeTruthy());
    expect(screen.getByTestId('iso-card').textContent).toBe('isolation of team-a');
    expect(screen.getByTestId('dc-card').textContent).toBe('data class of team-a');
    expect(screen.getByTestId('settings-isolation-workspace').textContent).toMatch(/team-a/);
  });

  it('is in the section menu, under System', async () => {
    renderAt('/settings/isolation');
    await waitFor(() => expect(screen.getByTestId('iso-card')).toBeTruthy());
    const link = screen.getByRole('link', { name: 'Isolation' });
    expect(link.getAttribute('href')).toBe('/settings/isolation');
    expect(link.className).toMatch(/font-semibold/);
  });
});
