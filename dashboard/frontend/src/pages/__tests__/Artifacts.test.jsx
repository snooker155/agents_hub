import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The Artifacts page (docs/files.md, docs/views.md): the workspace files of
// the selected workspace and the views its agents built, as cards, a tree or
// a list, with upload, search, preview, where used and delete. The API is
// mocked against the shapes routes/files.py and routes/views.py return.

const ok = (data) => Promise.resolve({ data });

const REC = {
  file_id: 'file_0123456789abcdef', workspace: 'acme', name: 'handbook.md',
  mime_type: 'text/markdown', size: 2048, sha256: 'a'.repeat(64), source: 'upload',
  created_by: 'u1', created_at: '2026-09-24T10:00:00Z', meta: {},
};

const listWorkspaceFiles = vi.fn(() => ok({ workspace: 'acme', files: [REC], usage_bytes: 2048,
  limits: { max_file_bytes: 26214400, max_workspace_bytes: 1073741824 } }));
const uploadWorkspaceFileObject = vi.fn(() => ok({ ...REC, file_id: 'file_fedcba9876543210', name: 'new.txt', deduplicated: false }));
const getWorkspaceFileRecord = vi.fn(() => ok(REC));
const getWorkspaceFileText = vi.fn(() => ok({ file_id: REC.file_id, name: REC.name, mime_type: REC.mime_type,
  kind: 'text', text: '# Leave\n30 days a year', truncated: false }));
const getWorkspaceFileBlob = vi.fn(() => ok(new Blob(['x'])));
const getWorkspaceFileUsage = vi.fn(() => ok({
  file_id: REC.file_id, total: 2,
  chats: [{ conversation_id: 'conv-1', title: 'Leave question', at: '2026-09-24T11:00:00Z' }],
  memory_pools: [{ pool_id: 'p1', name: 'HR knowledge', filename: 'handbook.md', status: 'indexed' }],
  tasks: [], eval_cases: [],
}));
const deleteWorkspaceFileObject = vi.fn(() => ok({ deleted: true }));

vi.mock('../../api/files', () => ({
  listWorkspaceFiles: (...a) => listWorkspaceFiles(...a),
  uploadWorkspaceFileObject: (...a) => uploadWorkspaceFileObject(...a),
  getWorkspaceFileRecord: (...a) => getWorkspaceFileRecord(...a),
  getWorkspaceFileText: (...a) => getWorkspaceFileText(...a),
  getWorkspaceFileBlob: (...a) => getWorkspaceFileBlob(...a),
  getWorkspaceFileUsage: (...a) => getWorkspaceFileUsage(...a),
  deleteWorkspaceFileObject: (...a) => deleteWorkspaceFileObject(...a),
  saveBlobAs: vi.fn(),
  formatBytes: (n) => `${n} B`,
  indexWorkspaceFiles: vi.fn(() => ok({ workspace: 'acme', added: 0, updated: 0, unchanged: 0, removed: 0, skipped: [] })),
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'acme' }),
}));

const VIEW = { view_id: 'vw_1', kind: 'chart', title: 'Sales by category', summary: 'bars', size_bytes: 1234, created_at: '2026-09-24T12:00:00Z' };
const listViews = vi.fn(() => ok({ views: [VIEW] }));
const deleteView = vi.fn(() => ok({ deleted: true }));
const getView = vi.fn(() => ok({ ...VIEW, owner: { kind: 'run', id: 'run-9' }, spec: { vega_lite: {} } }));
vi.mock('../../api', () => ({
  listViews: (...a) => listViews(...a),
  deleteView: (...a) => deleteView(...a),
  getView: (...a) => getView(...a),
}));
// The live card fetches and renders the view itself; here a stub names it.
vi.mock('../../views/ViewCard', () => ({
  default: ({ viewRef, actions }) => <div data-testid="view-card">{viewRef.title}{actions}</div>,
}));

import Artifacts from '../Artifacts';

const show = (path = '/artifacts') => render(
  <I18nProvider><MemoryRouter initialEntries={[path]}><Artifacts /></MemoryRouter></I18nProvider>,
);

/** The page remembers its mode per browser; the tests that want a mode pick it. */
const pick = (label) => fireEvent.click(screen.getByRole('button', { name: label }));

beforeEach(() => {
  vi.clearAllMocks();
  try { localStorage.removeItem('files.view'); } catch { /* storage unavailable */ }
});

const IN_FOLDER = [
  { ...REC, file_id: 'file_1111111111111111', name: 'plan.md', source: 'agent', size: 10,
    meta: { path: 'proj/docs/plan.md' } },
  { ...REC, file_id: 'file_2222222222222222', name: 'main.py', source: 'agent', size: 20,
    meta: { path: 'proj/main.py' } },
];

describe('Artifacts page', () => {
  it('lists the workspace files with their source and size', async () => {
    show();
    await waitFor(() => expect(screen.getByText('handbook.md')).toBeInTheDocument());
    expect(listWorkspaceFiles).toHaveBeenCalledWith('acme', expect.objectContaining({ q: '', source: '' }));
    expect(screen.getByText('2048 B')).toBeInTheDocument();
  });

  it('searches and filters by source', async () => {
    show();
    await waitFor(() => expect(screen.getByText('handbook.md')).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText('Search by name or id'), { target: { value: 'hand' } });
    fireEvent.change(screen.getByLabelText('Source'), { target: { value: 'agent' } });
    await waitFor(() => expect(listWorkspaceFiles).toHaveBeenLastCalledWith(
      'acme', expect.objectContaining({ q: 'hand', source: 'agent' })));
  });

  it('says so when the workspace has neither files nor views', async () => {
    listWorkspaceFiles.mockImplementationOnce(() => ok({ files: [], usage_bytes: 0, limits: {} }));
    listViews.mockImplementationOnce(() => ok({ views: [] }));
    show();
    await waitFor(() => expect(screen.getByText(/Nothing here yet/)).toBeInTheDocument());
  });

  it('uploads a picked file into the workspace', async () => {
    show();
    await waitFor(() => expect(screen.getByText('handbook.md')).toBeInTheDocument());
    const file = new File(['hello'], 'new.txt', { type: 'text/plain' });
    fireEvent.change(screen.getByTestId('files-upload-input'), { target: { files: [file] } });
    // A loose file at the root goes to the file store, with no path.
    await waitFor(() => expect(uploadWorkspaceFileObject).toHaveBeenCalledWith('acme', file, {}));
  });

  it('opens a file from ?file=: preview and where it is used', async () => {
    show(`/files?file=${REC.file_id}`);
    await waitFor(() => expect(screen.getByTestId('file-preview')).toHaveTextContent('30 days a year'));
    await waitFor(() => expect(screen.getByText('Leave question')).toBeInTheDocument());
    expect(screen.getByText('Leave question').closest('a')).toHaveAttribute('href', '/chat/conv-1');
    expect(screen.getByText('HR knowledge')).toBeInTheDocument();
  });

  it('deletes after a confirmation that names what still uses the file', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    show(`/files?file=${REC.file_id}`);
    await waitFor(() => expect(screen.getByText('HR knowledge')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Delete/ }));
    await waitFor(() => expect(deleteWorkspaceFileObject).toHaveBeenCalledWith(REC.file_id));
    expect(confirm.mock.calls[0][0]).toContain('used in 2 places');
    confirm.mockRestore();
  });

  it('does not delete when the confirmation is declined', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    show(`/files?file=${REC.file_id}`);
    await waitFor(() => expect(screen.getByText('HR knowledge')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Delete/ }));
    expect(deleteWorkspaceFileObject).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('shows folder-backed files as a tree with folders collapsed until opened', async () => {
    listWorkspaceFiles.mockImplementationOnce(() => ok({ workspace: 'acme', files: [REC, ...IN_FOLDER],
      usage_bytes: 2078, limits: {} }));
    show();
    await waitFor(() => expect(screen.getByText('handbook.md')).toBeInTheDocument());
    pick('Tree');
    expect(screen.getByTestId('files-tree')).toBeInTheDocument();
    expect(screen.getByText('proj')).toBeInTheDocument();
    expect(screen.getByText('2 files · 30 B')).toBeInTheDocument();
    expect(screen.queryByText('main.py')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('proj'));
    expect(screen.getByText('main.py')).toBeInTheDocument();
    expect(screen.getByText('docs')).toBeInTheDocument();
    expect(screen.queryByText('plan.md')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('docs'));
    expect(screen.getByText('plan.md')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Collapse all'));
    expect(screen.queryByText('main.py')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Expand all'));
    expect(screen.getByText('plan.md')).toBeInTheDocument();
  });

  it('switches to the flat list and remembers the choice', async () => {
    listWorkspaceFiles.mockImplementation(() => ok({ workspace: 'acme', files: IN_FOLDER, usage_bytes: 30, limits: {} }));
    show();
    await waitFor(() => expect(screen.getByText('proj')).toBeInTheDocument());
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('main.py')).toBeInTheDocument();
    expect(screen.getByText('proj/docs/plan.md')).toBeInTheDocument();
    expect(localStorage.getItem('files.view')).toBe('list');
  });

  it('opens the panel from a list row and the rendered file from the preview button', async () => {
    localStorage.setItem('files.view', 'list');
    show();
    await waitFor(() => expect(screen.getByText('handbook.md')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('list-row'));
    await waitFor(() => expect(screen.getByTestId('file-panel')).toBeInTheDocument());
    expect(getWorkspaceFileRecord).toHaveBeenCalledWith(REC.file_id);

    fireEvent.click(screen.getAllByRole('button', { name: 'Preview' })[0]);
    const modal = await screen.findByTestId('file-preview-modal');
    await waitFor(() => expect(modal).toHaveTextContent('30 days a year'));
    // Markdown is rendered, not shown as source.
    expect(modal.querySelector('h1')).toHaveTextContent('Leave');
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTestId('file-preview-modal')).not.toBeInTheDocument());
  });

  it('opens the rendered file from a tree row without opening the panel', async () => {
    show();
    await waitFor(() => expect(screen.getByText('handbook.md')).toBeInTheDocument());
    pick('Tree');
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await waitFor(() => expect(screen.getByTestId('file-preview-modal')).toHaveTextContent('30 days a year'));
    expect(screen.queryByTestId('file-panel')).not.toBeInTheDocument();
  });

  it('shows a code file as a code block with its language and a copy button', async () => {
    const PY = { ...REC, file_id: 'file_3333333333333333', name: 'main.py', mime_type: 'text/x-python', meta: { path: 'proj/main.py' } };
    listWorkspaceFiles.mockImplementation(() => ok({ workspace: 'acme', files: [PY], usage_bytes: 20, limits: {} }));
    getWorkspaceFileRecord.mockImplementation(() => ok(PY));
    getWorkspaceFileText.mockImplementation(() => ok({ file_id: PY.file_id, name: PY.name, mime_type: PY.mime_type,
      kind: 'text', text: 'print(1)', truncated: false }));
    show();
    await waitFor(() => expect(screen.getByText('proj')).toBeInTheDocument());
    fireEvent.click(screen.getByText('proj'));
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    const modal = await screen.findByTestId('file-preview-modal');
    await waitFor(() => expect(modal.querySelector('[data-testid="code-block"]')).toHaveTextContent('print(1)'));
    expect(modal.querySelector('.hl-code-block__lang')).toHaveTextContent('python');
    expect(modal.querySelector('.hl-code-block__action')).toBeInTheDocument();
  });

  it('shows the views in a folder of their own, as cards, in the tree and in the list', async () => {
    listWorkspaceFiles.mockImplementation(() => ok({ workspace: 'acme', files: [REC, ...IN_FOLDER], usage_bytes: 2078, limits: {} }));
    show();
    await waitFor(() => expect(screen.getByText('handbook.md')).toBeInTheDocument());
    expect(listViews).toHaveBeenCalledWith({ workspace: 'acme' });
    // Cards at the root: the Views folder, the proj folder, the loose file.
    const folders = screen.getAllByTestId('folder-card');
    expect(folders.map((f) => f.textContent)).toEqual([
      expect.stringContaining('Views'), expect.stringContaining('proj'),
    ]);
    expect(folders[0]).toHaveTextContent('1 view');
    expect(screen.getByTestId('file-card')).toHaveTextContent('handbook.md');
    expect(screen.queryByTestId('view-card')).not.toBeInTheDocument();

    fireEvent.click(folders[0]);
    expect(screen.getByTestId('view-card')).toHaveTextContent('Sales by category');
    expect(screen.queryByTestId('file-card')).not.toBeInTheDocument();
    // back up through the breadcrumb
    fireEvent.click(screen.getByRole('button', { name: 'Artifacts' }));
    expect(screen.getAllByTestId('folder-card')).toHaveLength(2);

    pick('Tree');
    // "Views" is also an option of the source filter; the folder is the tree row.
    fireEvent.click(screen.getAllByTestId('tree-dir').find((d) => d.textContent.includes('Views')));
    expect(screen.getByTestId('tree-view')).toHaveTextContent('Sales by category');

    pick('List');
    expect(screen.getByTestId('list-view-row')).toHaveTextContent('View · chart');
    expect(screen.getByTestId('list-view-row')).toHaveTextContent('1234 B');
  });

  it('walks into a folder from a card and keeps the folder in the URL', async () => {
    listWorkspaceFiles.mockImplementation(() => ok({ workspace: 'acme', files: IN_FOLDER, usage_bytes: 30, limits: {} }));
    show('/artifacts?folder=proj');
    await waitFor(() => expect(screen.getByText('main.py')).toBeInTheDocument());
    expect(screen.getByTestId('folder-card')).toHaveTextContent('docs');
    fireEvent.click(screen.getByTestId('folder-card'));
    expect(screen.getByText('plan.md')).toBeInTheDocument();
    expect(screen.queryByText('main.py')).not.toBeInTheDocument();
  });

  it('filters to the views alone with the Views source', async () => {
    show();
    await waitFor(() => expect(screen.getByText('handbook.md')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Source'), { target: { value: 'view' } });
    await waitFor(() => expect(listWorkspaceFiles).toHaveBeenLastCalledWith('acme', expect.objectContaining({ source: '' })));
    pick('List');
    expect(screen.getByTestId('list-view-row')).toBeInTheDocument();
    expect(screen.queryByTestId('list-row')).not.toBeInTheDocument();
  });

  it('deletes a view from its card after a confirmation', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    show('/artifacts?folder=__views__');
    await waitFor(() => expect(screen.getByTestId('view-card')).toBeInTheDocument());
    fireEvent.click(screen.getByTitle('Delete view'));
    await waitFor(() => expect(deleteView).toHaveBeenCalledWith('vw_1'));
    expect(screen.queryByTestId('view-card')).not.toBeInTheDocument();
    confirm.mockRestore();
  });

  it('opens a view in a panel from a list row, with what made it', async () => {
    localStorage.setItem('files.view', 'list');
    show();
    await waitFor(() => expect(screen.getByTestId('list-view-row')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('list-view-row'));
    const panel = await screen.findByTestId('view-panel');
    await waitFor(() => expect(getView).toHaveBeenCalledWith('vw_1'));
    expect(panel).toHaveTextContent('Sales by category');
    expect(panel.querySelector('[data-testid="view-card"]')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('run · run-9')).toHaveAttribute('href', '/messages/run-9'));
    // closing it clears the query key
    fireEvent.click(screen.getByLabelText('Close'));
    await waitFor(() => expect(screen.queryByTestId('view-panel')).not.toBeInTheDocument());
  });

  it('opens the panel from the details button of a view card', async () => {
    show('/artifacts?folder=__views__');
    await waitFor(() => expect(screen.getByTestId('view-card')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('view-details'));
    expect(await screen.findByTestId('view-panel')).toBeInTheDocument();
  });

  it('turns into a drop field over the list and uploads into the open folder with the path', async () => {
    listWorkspaceFiles.mockImplementation(() => ok({ workspace: 'acme', files: IN_FOLDER, usage_bytes: 30, limits: {} }));
    show('/artifacts?folder=proj');
    await waitFor(() => expect(screen.getByText('main.py')).toBeInTheDocument());
    const zone = screen.getByTestId('files-dropzone');
    const file = new File(['hello'], 'new.txt', { type: 'text/plain' });
    const dataTransfer = { types: ['Files'], files: [file], items: [] };
    expect(screen.queryByTestId('drop-field')).not.toBeInTheDocument();
    fireEvent.dragEnter(zone, { dataTransfer });
    expect(screen.getByTestId('drop-field')).toHaveTextContent('Drop to upload into proj');
    fireEvent.drop(zone, { dataTransfer });
    await waitFor(() => expect(uploadWorkspaceFileObject).toHaveBeenCalledWith('acme', file, { path: 'proj/new.txt' }));
    await waitFor(() => expect(screen.queryByTestId('drop-field')).not.toBeInTheDocument());
  });

  it('refuses a drop into the Views folder', async () => {
    show('/artifacts?folder=__views__');
    await waitFor(() => expect(screen.getByTestId('view-card')).toBeInTheDocument());
    const zone = screen.getByTestId('files-dropzone');
    const file = new File(['hello'], 'new.txt', { type: 'text/plain' });
    const dataTransfer = { types: ['Files'], files: [file], items: [] };
    fireEvent.dragEnter(zone, { dataTransfer });
    expect(screen.getByTestId('drop-field')).toHaveTextContent('Views are built by agents');
    fireEvent.drop(zone, { dataTransfer });
    await new Promise((r) => setTimeout(r, 20));
    expect(uploadWorkspaceFileObject).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /Upload/ })).toBeDisabled();
  });

  it('ignores a drag that carries no files', async () => {
    show();
    await waitFor(() => expect(screen.getByText('handbook.md')).toBeInTheDocument());
    fireEvent.dragEnter(screen.getByTestId('files-dropzone'), { dataTransfer: { types: ['text/plain'], files: [], items: [] } });
    expect(screen.queryByTestId('drop-field')).not.toBeInTheDocument();
  });
});
