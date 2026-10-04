import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import WorkspaceDetails from '../WorkspaceDetails';
import { I18nProvider } from '../../i18n';
import { WorkspaceContext } from '../../components/workspace';

// The Files tab names a file by its registry id in the address (?file=<id>)
// and asks the server for it by id; a path link from before files had ids
// still opens and is rewritten to the id.

const ok = (data) => Promise.resolve({ data });

const content = vi.fn((name, ref) => ok({ content: `body of ${JSON.stringify(ref)}`, size: 4 }));
const fileId = vi.fn((name, path) => ok({ file_id: path === 'loose.txt' ? 'file_00000000000000cc' : null }));

vi.mock('../../api', () => ({
  getWorkspace: () => ok({ name: 'jobs', path: '/ws/jobs', tasks: [], metadata: {} }),
  getWorkspaceFilesByName: () => ok({
    files: ['notes/a.md', 'notes/b.md', 'loose.txt'],
    directories: ['notes'],
    ids: { 'notes/a.md': 'file_00000000000000aa', 'notes/b.md': 'file_00000000000000bb' },
  }),
  getWorkspaceFileContent: (...a) => content(...a),
  getWorkspaceFileId: (...a) => fileId(...a),
  getAgents: () => ok([]),
  getProjects: () => ok([]),
  getWorkspaceInstructions: () => ok({ instructions: '' }),
  listFlows: () => ok([]),
  getWorkspaceSettingsOverrides: () => ok({ overrides: {} }),
  updateWorkspaceSettingsOverrides: () => ok({}),
  addAgentToWorkspace: () => ok({}),
  removeAgentFromWorkspace: () => ok({}),
  deleteWorkspace: () => ok({}),
  updateWorkspaceInstructions: () => ok({}),
  uploadWorkspaceFile: () => ok({}),
  getWorkspaceFileRawUrl: () => '',
  deleteWorkspaceFile: () => ok({}),
  removeFlowFromWorkspace: () => ok({}),
}));
vi.mock('../../components/stream', () => ({ useLiveRefetch: () => {} }));
vi.mock('../../components/theme', () => ({
  useTheme: () => ({ theme: 'light', resolvedMode: 'light' }),
  resolvePalette: () => Promise.resolve(),
}));
vi.mock('../../components/workspace/WorkspaceMembers', () => ({ default: () => null }));
vi.mock('../../components/workspace/WorkspaceSecrets', () => ({ default: () => null }));
vi.mock('../../components/settings/ToolPolicySettings', () => ({ default: () => null }));
vi.mock('../../components/settings/LoopSettingsWorkspace', () => ({ default: () => null }));
vi.mock('../../components/TaskBoard', () => ({ default: () => null }));

function Where() {
  const loc = useLocation();
  return <output data-testid="where">{loc.search}</output>;
}

function show(search) {
  localStorage.setItem('agents_hub_language', 'en');
  return render(
    <WorkspaceContext.Provider value={{ selectedWorkspace: 'jobs', liveUpdates: false }}>
      <I18nProvider>
        <MemoryRouter initialEntries={[`/workspaces/jobs${search}`]}>
          <Routes><Route path="/workspaces/:name" element={<><WorkspaceDetails /><Where /></>} /></Routes>
        </MemoryRouter>
      </I18nProvider>
    </WorkspaceContext.Provider>,
  );
}

const where = () => new URLSearchParams(screen.getByTestId('where').textContent);

describe('WorkspaceDetails file links', () => {
  beforeEach(() => { localStorage.clear(); content.mockClear(); fileId.mockClear(); });

  it('opens the file an id link names and reads it by id', async () => {
    show('?tab=files&file=file_00000000000000bb');
    await waitFor(() => expect(content).toHaveBeenCalledWith('jobs', { fileId: 'file_00000000000000bb' }));
    expect(content).not.toHaveBeenCalledWith('jobs', expect.objectContaining({ fileId: 'file_00000000000000aa' }));
  });

  it('puts the id of a picked file in the address, never its name', async () => {
    show('?tab=files');
    fireEvent.click(await screen.findByText('b.md'));
    await waitFor(() => expect(where().get('file')).toBe('file_00000000000000bb'));
    expect(screen.getByTestId('where').textContent).not.toContain('b.md');
  });

  it('asks for an id when the listing had none and rewrites an old path link', async () => {
    show('?tab=files&file=loose.txt');
    await waitFor(() => expect(where().get('file')).toBe('file_00000000000000cc'));
    expect(fileId).toHaveBeenCalledWith('jobs', 'loose.txt');
    expect(content).toHaveBeenCalledWith('jobs', { fileId: 'file_00000000000000cc' });
  });
});
