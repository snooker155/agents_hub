import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../i18n';

// The header's model picker lists only the models every member may pick (the
// enabled catalog models): no row of its own for the global default and no
// "global" mark. The model the workspace gets anyway clears the header's
// choice instead of pinning it.

const ok = (data) => Promise.resolve({ data });
const updateWorkspaceModel = vi.fn(() => ok({}));
let catalog;
let summary;

vi.mock('../workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'ws1', setSelectedWorkspace: () => {}, liveUpdates: true, toggleLiveUpdates: () => {} }),
}));
vi.mock('../theme', () => ({ useTheme: () => ({ theme: 'light', setTheme: () => {} }) }));
vi.mock('../stream', () => ({ useStream: () => ({ connected: true, on: () => () => {} }), useLiveRefetch: () => {} }));
vi.mock('../features', () => ({ useFeatures: () => ({}) }));
vi.mock('../auth', async (importOriginal) => ({
  ...(await importOriginal()), useAuth: () => ({ mode: 'single', user: null }),
}));
vi.mock('../../api', () => ({
  getWorkspaces: () => ok([{ name: 'ws1' }]),
  updateWorkspaceModel: (...args) => updateWorkspaceModel(...args),
  testProvider: () => ok({ ok: true }),
  getModelsCatalog: () => ok(catalog),
  getModelReasoning: () => ok({ profile: null }),
}));
vi.mock('../../api/workspaceSummary', () => ({
  loadWorkspaceSummary: () => Promise.resolve(summary),
  patchWorkspaceSummary: () => {},
  subscribeWorkspaceSummary: () => () => {},
  invalidateAllWorkspaceSummaries: () => {},
}));
vi.mock('../setup/useSetupGuide', () => ({ default: () => ({ guide: null, act: () => {} }) }));
const Nothing = () => null;
for (const path of ['../NotificationBell', '../WatchersIndicator', '../SyncIndicator', '../vault/SyncedDataBanner',
  '../LanguageSwitcher', '../workspace/IsolationBadge', '../docs/OnboardingModal', '../pageChat/PageChatPanel',
  '../help/HelpPanel', '../assistant/WakeListener', '../InstallAppButton', '../WorkspacePicker']) {
  vi.doMock(path, () => ({ default: Nothing }));
}

const { default: Layout } = await import('../Layout');

const model = (id, enabled = true) => ({ id, enabled });

function renderHeader() {
  render(
    <I18nProvider>
      <MemoryRouter><Layout><div /></Layout></MemoryRouter>
    </I18nProvider>,
  );
}

const picker = () => screen.getByRole('button', { name: /gpt-4o|claude/ });

beforeEach(() => {
  updateWorkspaceModel.mockClear();
  catalog = { providers: { openai: { default: 'gpt-4o', models: [model('gpt-4o')] } } };
  summary = { model: {
    global_default: { provider: 'openai', model: 'gpt-4o' },
    workspace_default: { provider: '', model: '' },
    override: { provider: '', model: '' },
  } };
});

describe('header model picker', () => {
  it('shows the one model there is, checked, with no global row or mark', async () => {
    renderHeader();
    await waitFor(() => expect(picker()).toHaveTextContent('gpt-4o'));
    const chip = picker();
    expect(chip).not.toHaveTextContent(/global/i);
    fireEvent.click(chip);
    const rows = await screen.findAllByRole('button', { name: /gpt-4o/ });
    const row = rows.find((b) => b !== chip);
    expect(row).toHaveTextContent('✓');
    expect(screen.queryByText(/^global$/i)).toBeNull();
    expect(screen.queryByText('Global')).toBeNull();
    // Picking the model the workspace gets anyway does not pin it.
    fireEvent.click(row);
    await waitFor(() => expect(updateWorkspaceModel).toHaveBeenCalledWith('ws1', { provider: '', model: '' }));
  });

  it('lists only the enabled models and pins another one', async () => {
    catalog.providers.anthropic = { default: '', models: [model('claude-opus-5-5'), model('claude-off', false)] };
    renderHeader();
    await waitFor(() => expect(picker()).toHaveTextContent('gpt-4o'));
    fireEvent.click(picker());
    const claude = await screen.findByRole('button', { name: /claude-opus-5-5/ });
    expect(screen.queryByText('claude-off')).toBeNull();
    expect(within(claude).queryByText('✓')).toBeNull();
    fireEvent.click(claude);
    await waitFor(() => expect(updateWorkspaceModel).toHaveBeenCalledWith('ws1', { provider: 'anthropic', model: 'claude-opus-5-5' }));
  });
});
