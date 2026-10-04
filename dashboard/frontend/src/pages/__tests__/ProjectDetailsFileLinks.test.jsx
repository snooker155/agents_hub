import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import ProjectDetails from '../ProjectDetails';
import { I18nProvider } from '../../i18n';
import { WorkspaceContext } from '../../components/workspace';

// The Files tab of a project names a file by its registry id in the address
// (?file=<id>) and asks for it by id; a link with a path still opens and is
// rewritten to the id.

const ok = (data) => Promise.resolve({ data });

const content = vi.fn((id, ref) => ok({ content: `body ${JSON.stringify(ref)}`, kind: 'text', mime_type: 'text/plain', size: 3 }));
const fileId = vi.fn((id, path) => ok({ file_id: path === 'loose.txt' ? 'file_00000000000000cc' : null }));

vi.mock('../../api', () => ({
  getProject: () => ok({ id: 'p1', name: 'Site', workspace: 'jobs', status: 'active', type: 'code', tags: [] }),
  updateProject: () => ok({}),
  deleteProject: () => ok({}),
  getProjectTasks: () => ok([]),
  cloneProjectRepo: () => ok({}),
  getProjectGitStatus: () => ok({}),
  pullProjectRepo: () => ok({}),
  getProjectFiles: () => ok({
    files: ['README.md', 'src/app.py', 'loose.txt'],
    ids: { 'README.md': 'file_00000000000000aa', 'src/app.py': 'file_00000000000000bb' },
  }),
  getProjectFileContent: (...a) => content(...a),
  getProjectFileBlob: () => ok(new Blob(['x'])),
  getProjectFileId: (...a) => fileId(...a),
  syncProjectIssues: () => ok({}),
  publishProjectBranch: () => ok({}),
}));
vi.mock('../../components/ImportRepoModal', () => ({ default: () => null }));
vi.mock('../../components/projects/DeployPanel', () => ({ default: () => null }));
vi.mock('../../components/projects/TrackerCard', () => ({ default: () => null }));
vi.mock('../../components/flow/ProjectGraph', () => ({ default: () => null }));
vi.mock('../../components/flow/PlannerChat', () => ({ default: () => null }));
vi.mock('../../components/TaskBoard', () => ({ default: () => null }));
vi.mock('../../components/stream', () => ({ useLiveRefetch: () => {} }));

function Where() {
  return <output data-testid="where">{useLocation().search}</output>;
}

function show(search) {
  localStorage.setItem('agents_hub_language', 'en');
  return render(
    <WorkspaceContext.Provider value={{ selectedWorkspace: 'jobs', liveUpdates: false }}>
      <I18nProvider>
        <MemoryRouter initialEntries={[`/projects/p1${search}`]}>
          <Routes><Route path="/projects/:id" element={<><ProjectDetails /><Where /></>} /></Routes>
        </MemoryRouter>
      </I18nProvider>
    </WorkspaceContext.Provider>,
  );
}

const where = () => new URLSearchParams(screen.getByTestId('where').textContent);

describe('ProjectDetails file links', () => {
  beforeEach(() => { localStorage.clear(); content.mockClear(); fileId.mockClear(); });

  it('opens the Files tab on the file an id link names and reads it by id', async () => {
    show('?file=file_00000000000000bb');
    await waitFor(() => expect(content).toHaveBeenCalledWith('p1', { fileId: 'file_00000000000000bb' }));
    expect(content).not.toHaveBeenCalledWith('p1', { fileId: 'file_00000000000000aa' });
  });

  it('puts the id of a picked file in the address, never its name', async () => {
    show('?file=file_00000000000000aa');
    fireEvent.click(await screen.findByText('loose.txt'));
    await waitFor(() => expect(where().get('file')).toBe('file_00000000000000cc'));
    expect(fileId).toHaveBeenCalledWith('p1', 'loose.txt');
    expect(screen.getByTestId('where').textContent).not.toContain('loose');
  });

  it('rewrites an old link with a path to the id', async () => {
    show('?file=src%2Fapp.py');
    await waitFor(() => expect(where().get('file')).toBe('file_00000000000000bb'));
    expect(content).toHaveBeenCalledWith('p1', { fileId: 'file_00000000000000bb' });
  });
});
