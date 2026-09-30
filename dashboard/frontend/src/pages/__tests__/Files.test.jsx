import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The Files page (docs/files.md): the workspace files of the selected
// workspace, with upload, search, preview, where used and delete. The API is
// mocked against the shapes routes/files.py returns.

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

import Files from '../Files';

const show = (path = '/files') => render(
  <I18nProvider><MemoryRouter initialEntries={[path]}><Files /></MemoryRouter></I18nProvider>,
);

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

describe('Files page', () => {
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

  it('says so when the workspace has no files', async () => {
    listWorkspaceFiles.mockImplementationOnce(() => ok({ files: [], usage_bytes: 0, limits: {} }));
    show();
    await waitFor(() => expect(screen.getByText('No files in this workspace yet.')).toBeInTheDocument());
  });

  it('uploads a picked file into the workspace', async () => {
    show();
    await waitFor(() => expect(screen.getByText('handbook.md')).toBeInTheDocument());
    const file = new File(['hello'], 'new.txt', { type: 'text/plain' });
    fireEvent.change(screen.getByTestId('files-upload-input'), { target: { files: [file] } });
    await waitFor(() => expect(uploadWorkspaceFileObject).toHaveBeenCalledWith('acme', file));
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
});
