import { render, screen, within, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import WorkspaceDetails from '../WorkspaceDetails';
import { I18nProvider } from '../../i18n';
import { WorkspaceContext } from '../../components/workspace';

// The workspace palette card: the default preset shows as selected when
// nothing is saved, and "Custom" is a choice of its own that brings back the
// person's colors after a preset was tried on.

const ok = (data) => Promise.resolve({ data });

const updateOverrides = vi.fn(() => ok({}));
let savedPalette = null;

const AGENTS = [
  { id: 'orchestrator', name: 'Orchestrator', system: true },
  { id: 'visualizer', name: 'Visualizer', system: true },
  { id: 'job_scout', name: 'Job Scout', description: 'finds jobs' },
  { id: 'poet', name: 'Poet', description: 'writes verse' },
];

vi.mock('../../api', () => ({
  getWorkspace: () => ok({
    name: 'jobs', path: '/ws/jobs', tasks: [],
    metadata: { allowed_agents: ['orchestrator', 'visualizer', 'job_scout'] },
  }),
  getWorkspaceFilesByName: () => ok({ files: [], directories: [] }),
  getAgents: () => ok(AGENTS),
  getProjects: () => ok([]),
  getWorkspaceInstructions: () => ok({ instructions: '' }),
  listFlows: () => ok([]),
  getWorkspaceSettingsOverrides: () => ok({ overrides: savedPalette ? { palette: savedPalette } : {} }),
  updateWorkspaceSettingsOverrides: (...args) => updateOverrides(...args),
  getWorkspacePolicy: () => ok({ require_tool_approval: false, hooks: {} }),
  updateWorkspacePolicy: () => ok({ require_tool_approval: false, hooks: {} }),
  getSettings: () => ok({ env_defined_fields: [], orch_log_level: 'INFO' }),
  updateSettings: () => ok({}),
  testProvider: () => ok({ ok: true }),
  testLocalModel: () => ok({ ok: true, models: [] }),
  addAgentToWorkspace: () => ok({}),
  removeAgentFromWorkspace: () => ok({}),
  deleteWorkspace: () => ok({}),
  getWorkspaceFileContent: () => ok({}),
  updateWorkspaceInstructions: () => ok({}),
  uploadWorkspaceFile: () => ok({}),
  getWorkspaceFileRawUrl: () => '',
  getWorkspaceFileId: () => ok({}),
  deleteWorkspaceFile: () => ok({}),
  removeFlowFromWorkspace: () => ok({}),
}));
vi.mock('../../components/stream', () => ({ useLiveRefetch: () => {} }));
vi.mock('../../components/theme', () => ({
  useTheme: () => ({ theme: 'light', resolvedMode: 'light' }),
  resolvePalette: () => Promise.resolve(),
}));
vi.mock('../../components/workspace/WorkspaceMembers', () => ({ default: () => null }));
vi.mock('../../components/workspace/WorkspaceSecrets', () => ({ default: () => <section>SECRETS CARD</section> }));
vi.mock('../../components/settings/ToolPolicySettings', () => ({ default: () => null }));
vi.mock('../../components/settings/LoopSettingsWorkspace', () => ({ default: () => null }));
vi.mock('../../components/TaskBoard', () => ({ default: () => null }));

function show() {
  localStorage.setItem('agents_hub_language', 'en');
  return render(
    <WorkspaceContext.Provider value={{ selectedWorkspace: 'jobs', liveUpdates: false }}>
      <I18nProvider>
        <MemoryRouter initialEntries={['/workspaces/jobs?tab=settings']}>
          <Routes><Route path="/workspaces/:name" element={<WorkspaceDetails />} /></Routes>
        </MemoryRouter>
      </I18nProvider>
    </WorkspaceContext.Provider>,
  );
}

async function openPalette() {
  show();
  await screen.findAllByText('Agent Execution Mode');
  const menu = document.querySelector('nav.md\\:block');
  fireEvent.click(within(menu).getByText('Palette'));
  await screen.findByText('Default palette');
  await screen.findByRole('button', { name: 'Custom' });
}

const selected = (name) => screen.getByRole('button', { name }).className.includes('bg-indigo-50');

describe('workspace palette presets', () => {
  beforeEach(() => {
    localStorage.clear();
    savedPalette = null;
    updateOverrides.mockClear();
  });

  it('shows the default preset as chosen when nothing is saved', async () => {
    await openPalette();
    await waitFor(() => expect(selected('Magenta')).toBe(true));
    expect(selected('Custom')).toBe(false);
    expect(selected('Neutral')).toBe(false);
  });

  it('brings the saved custom colors back after a preset was tried', async () => {
    savedPalette = { brand: '#166534' };
    await openPalette();
    await waitFor(() => expect(selected('Custom')).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'Neutral' }));
    expect(selected('Neutral')).toBe(true);
    expect(selected('Custom')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Custom' }));
    expect(selected('Custom')).toBe(true);
    expect(screen.getAllByDisplayValue('#166534').length).toBeGreaterThan(0);
  });

  it('saves a style preset with its style', async () => {
    await openPalette();
    fireEvent.click(screen.getByRole('button', { name: 'Neutral' }));
    fireEvent.click(screen.getByText('Save default'));
    await waitFor(() => expect(updateOverrides).toHaveBeenCalled());
    expect(updateOverrides.mock.calls[0][1].palette).toMatchObject({ style: 'neutral', brand: '#262626' });
  });

  it('turns a preset into the person\'s own colors on any edit', async () => {
    await openPalette();
    fireEvent.click(screen.getByRole('button', { name: 'Neutral' }));
    const brand = screen.getAllByDisplayValue('#262626').find((el) => el.type === 'text');
    fireEvent.change(brand, { target: { value: '#123456' } });
    expect(selected('Custom')).toBe(true);
    fireEvent.click(screen.getByText('Save default'));
    await waitFor(() => expect(updateOverrides).toHaveBeenCalled());
    const palette = updateOverrides.mock.calls[0][1].palette;
    expect(palette.brand).toBe('#123456');
    expect(palette.style).toBeUndefined();
  });
});
