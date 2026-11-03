import { render, screen, within, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import WorkspaceDetails from '../WorkspaceDetails';
import { I18nProvider } from '../../i18n';
import { WorkspaceContext } from '../../components/workspace';

// The Agents tab of a workspace: system agents are in every workspace, so the
// "add" column never offers them and the authorized list hides them until the
// "show system agents" box is ticked.

const ok = (data) => Promise.resolve({ data });

const updateOverrides = vi.fn(() => ok({}));
const getMembers = vi.fn(() => ok([]));
// Single mode unless a test switches it; the Members tab needs multi mode.
const authState = { mode: 'single', user: null, loading: false };

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
  getWorkspaceSettingsOverrides: () => ok({ overrides: { palette: { brand: '#166534' }, orch_log_level: 'DEBUG' } }),
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
  getWorkspaceMembers: (...args) => getMembers(...args),
}));
vi.mock('../../components/auth', async (importOriginal) => ({
  ...(await importOriginal()),
  useAuth: () => authState,
}));
vi.mock('../../components/stream', () => ({ useLiveRefetch: () => {} }));
vi.mock('../../components/theme', () => ({
  useTheme: () => ({ theme: 'light', resolvedMode: 'light' }),
  resolvePalette: () => Promise.resolve(),
}));
vi.mock('../../components/workspace/WorkspaceMembers', () => ({ default: () => <section>MEMBERS CARD</section> }));
vi.mock('../../components/workspace/WorkspaceSecrets', () => ({ default: () => <section>SECRETS CARD</section> }));
vi.mock('../../components/workspace/WorkspaceRoles', () => ({ default: () => <section>ROLES CARD</section> }));
vi.mock('../../components/settings/ToolPolicySettings', () => ({ default: () => null }));
vi.mock('../../components/settings/LoopSettingsWorkspace', () => ({ default: () => null }));
vi.mock('../../components/TaskBoard', () => ({ default: () => null }));

function show(lang = 'en', tab = 'agents') {
  localStorage.setItem('agents_hub_language', lang);
  return render(
    <WorkspaceContext.Provider value={{ selectedWorkspace: 'jobs', liveUpdates: false }}>
      <I18nProvider>
        <MemoryRouter initialEntries={[`/workspaces/jobs?tab=${tab}`]}>
          <Routes><Route path="/workspaces/:name" element={<WorkspaceDetails />} /></Routes>
        </MemoryRouter>
      </I18nProvider>
    </WorkspaceContext.Provider>,
  );
}

describe('WorkspaceDetails agents tab', () => {
  beforeEach(() => localStorage.clear());

  it('never offers system agents and hides them from the authorized list by default', async () => {
    show();
    const authorized = (await screen.findByText('Authorized Agents in Workspace')).closest('.grid > div');
    const market = screen.getByText('Available from Marketplace').closest('.grid > div');

    expect(within(authorized).getByText('Job Scout')).toBeTruthy();
    expect(within(authorized).queryByText('Orchestrator')).toBeNull();
    expect(within(market).getByText('Poet')).toBeTruthy();
    expect(within(market).queryByText('Orchestrator')).toBeNull();
    expect(within(market).queryByText('Visualizer')).toBeNull();

    const box = within(authorized).getByRole('checkbox');
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    expect(within(authorized).getByText('Orchestrator')).toBeTruthy();
    expect(within(market).queryByText('Orchestrator')).toBeNull();
    expect(localStorage.getItem('workspace_agents_show_system')).toBe('true');
  });

  it('translates the tab strip', async () => {
    show('ru');
    const nav = (await screen.findByText('Разрешённые агенты в пространстве')).ownerDocument.querySelector('nav');
    const text = nav.textContent;
    for (const label of ['Файлы', 'Агенты', 'Инструкции', 'Задачи', 'Проекты', 'Прогресс', 'Настройки']) {
      expect(text).toContain(label);
    }
    expect(document.body.textContent).not.toMatch(/workspaceDetails\.[a-zA-Z]/);
  });
});

describe('WorkspaceDetails settings tab', () => {
  beforeEach(() => { localStorage.clear(); updateOverrides.mockClear(); });

  it('offers only what is per workspace, one section at a time', async () => {
    show('en', 'settings');
    expect(await screen.findByText('ROLES CARD')).toBeTruthy();
    const menu = document.querySelector('nav.md\\:block');
    for (const label of ['Agent roles', 'Secrets', 'Palette']) {
      expect(within(menu).getByText(label)).toBeTruthy();
    }
    // Overrides with a global value sit beside it on the Settings page, and
    // personal memory is a tab of the Memory page.
    for (const label of ['Execution', 'Tool policy', 'Web access', 'Task assignment', 'Personal memory']) {
      expect(within(menu).queryByText(label)).toBeNull();
    }
    expect(screen.queryByText('Agent Execution Mode')).toBeNull();
    fireEvent.click(within(menu).getByText('Secrets'));
    expect(await screen.findByText('SECRETS CARD')).toBeTruthy();
    fireEvent.click(within(menu).getByText('Palette'));
    expect(await screen.findByText('Default palette')).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/(workspaceDetails|settings)\.[a-zA-Z]/);
  });
});

describe('WorkspaceDetails members tab', () => {
  beforeEach(() => {
    localStorage.clear();
    getMembers.mockReset();
    Object.assign(authState, { mode: 'single', user: null });
  });

  const tabStrip = () => document.querySelector('nav').textContent;

  it('is not there in single mode, and the Agents tab has no members card', async () => {
    show();
    await screen.findByText('Authorized Agents in Workspace');
    expect(tabStrip()).not.toContain('Members');
    expect(screen.queryByText('MEMBERS CARD')).toBeNull();
    expect(getMembers).not.toHaveBeenCalled();
  });

  it('shows for an owner the members read lets through, and holds the card', async () => {
    Object.assign(authState, { mode: 'multi', user: { id: 'u1', role: 'user' } });
    getMembers.mockReturnValue(ok([]));
    show();
    await screen.findByText('Authorized Agents in Workspace');
    expect(screen.queryByText('MEMBERS CARD')).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: /Members/ }));
    expect(await screen.findByText('MEMBERS CARD')).toBeTruthy();
    expect(getMembers).toHaveBeenCalledWith('jobs');
  });

  it('stays hidden for a member the members read refuses', async () => {
    Object.assign(authState, { mode: 'multi', user: { id: 'u2', role: 'user' } });
    getMembers.mockReturnValue(Promise.reject({ response: { status: 403 } }));
    show('en', 'members');
    // The ?tab=members link falls back to Agents once the read is refused.
    expect(await screen.findByText('Authorized Agents in Workspace')).toBeTruthy();
    expect(getMembers).toHaveBeenCalledWith('jobs');
    expect(tabStrip()).not.toContain('Members');
    expect(screen.queryByText('MEMBERS CARD')).toBeNull();
  });

  it('shows for an admin without asking', async () => {
    Object.assign(authState, { mode: 'multi', user: { id: 'a', role: 'admin' } });
    show('en', 'members');
    expect(await screen.findByText('MEMBERS CARD')).toBeTruthy();
    expect(getMembers).not.toHaveBeenCalled();
  });
});
