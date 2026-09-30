import { render, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import Settings from '../Settings';
import { I18nProvider } from '../../i18n';

// The demo workspace card on Settings → Demo workspace: it shows what
// GET /api/demo says and flips it through POST /api/demo. Mocked the same
// way as Settings.i18n.test.jsx.

const ok = (data) => Promise.resolve({ data });

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
  getWorkspacePolicy: () => ok({ require_tool_approval: false, hooks: {} }),
  updateWorkspacePolicy: () => ok({}),
  updateSettings: () => ok({}),
  getApiToken: () => '',
  setApiToken: () => {},
  API_ORIGIN: '',
}));

const demo = vi.hoisted(() => ({ getDemo: vi.fn(), setDemo: vi.fn() }));
vi.mock('../../api/demo', () => demo);

function renderDemo() {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={['/settings/demo']}>
        <Routes><Route path="/settings/:section" element={<Settings />} /></Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe('Settings demo workspace card', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('agents_hub_language', 'en');
    demo.getDemo.mockReset();
    demo.setDemo.mockReset();
  });

  it('shows the absent state and adds the workspace', async () => {
    demo.getDemo.mockReturnValue(ok({ enabled: true, present: false, workspace: 'demo', counts: {} }));
    demo.setDemo.mockReturnValue(ok({ enabled: true, present: true, workspace: 'demo', counts: { agents: 4, flows: 1 } }));
    const { findByText, getByRole, container } = renderDemo();

    await findByText('Not installed');
    expect(container.textContent).toMatch(/Seeds a workspace named demo/);
    fireEvent.click(getByRole('button', { name: /Add the demo workspace/ }));

    await findByText('Installed');
    expect(demo.setDemo).toHaveBeenCalledWith(true);
    expect(container.textContent).toMatch(/Agents\s*4/);
    expect(container.textContent).not.toMatch(/settings\.[a-zA-Z]/);
  });

  it('asks before removing and removes on confirm', async () => {
    demo.getDemo.mockReturnValue(ok({ enabled: true, present: true, workspace: 'demo', counts: { agents: 3, views: 2 } }));
    demo.setDemo.mockReturnValue(ok({ enabled: true, present: false, workspace: 'demo', counts: {} }));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    const { findByText, getByRole } = renderDemo();

    await findByText('Installed');
    const button = getByRole('button', { name: /Remove the demo workspace/ });
    fireEvent.click(button);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(demo.setDemo).not.toHaveBeenCalled();

    fireEvent.click(button);
    await findByText('Not installed');
    expect(demo.setDemo).toHaveBeenCalledWith(false);
  });

  it('renders in every language with no unresolved keys', async () => {
    demo.getDemo.mockReturnValue(ok({ enabled: true, present: true, workspace: 'demo', counts: { agents: 1 } }));
    for (const lang of ['en', 'ru', 'de']) {
      localStorage.setItem('agents_hub_language', lang);
      const { container, unmount } = renderDemo();
      await waitFor(() => expect(container.querySelector('section button')).toBeTruthy());
      expect(container.textContent).not.toMatch(/settings\.[a-zA-Z]/);
      unmount();
    }
  });
});
