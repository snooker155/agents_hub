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
});

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
});
