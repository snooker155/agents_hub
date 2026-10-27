import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../../i18n';
import { buildStreamRequestBody } from '../../chat/send/buildRequest';

// The pieces that reuse a workspace file by id: the picker every surface
// opens, the id list a task or an eval case stores, and the chat request
// that sends an attachment as its id.

const ok = (data) => Promise.resolve({ data });
const A = { file_id: 'file_aaaaaaaaaaaaaaaa', name: 'a.md', size: 10, source: 'upload' };
const B = { file_id: 'file_bbbbbbbbbbbbbbbb', name: 'b.pdf', size: 20, source: 'agent' };

const listWorkspaceFiles = vi.fn(() => ok({ files: [A, B] }));
const getWorkspaceFileRecord = vi.fn((id) => (id === A.file_id ? ok(A) : Promise.reject(new Error('gone'))));

vi.mock('../../../api/files', () => ({
  listWorkspaceFiles: (...a) => listWorkspaceFiles(...a),
  getWorkspaceFileRecord: (...a) => getWorkspaceFileRecord(...a),
  uploadWorkspaceFileObject: vi.fn(),
  formatBytes: (n) => `${n} B`,
}));

import WorkspaceFilePicker from '../WorkspaceFilePicker';
import FileIdsField from '../FileIdsField';

const wrap = (ui) => render(<I18nProvider><MemoryRouter>{ui}</MemoryRouter></I18nProvider>);

beforeEach(() => {
  vi.clearAllMocks();
  listWorkspaceFiles.mockImplementation(() => ok({ files: [A, B] }));
  try { localStorage.removeItem('files.picker.view'); } catch { /* storage unavailable */ }
});

describe('WorkspaceFilePicker', () => {
  it('picks several files and skips the ones already added', async () => {
    const onPick = vi.fn();
    wrap(<WorkspaceFilePicker workspace="acme" excludeIds={[B.file_id]} onPick={onPick} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText('a.md')).toBeInTheDocument());
    expect(listWorkspaceFiles).toHaveBeenCalledWith('acme', expect.objectContaining({ q: '' }));
    expect(screen.getByText('b.pdf').closest('button')).toBeDisabled();
    fireEvent.click(screen.getByText('a.md'));
    fireEvent.click(screen.getByRole('button', { name: 'Add 1 file' }));
    expect(onPick).toHaveBeenCalledWith([A]);
  });

  it('shows the files as a tree of their folders, a folder picked at once', async () => {
    const C = { file_id: 'file_cccccccccccccccc', name: 'plan.md', size: 5, source: 'upload', meta: { path: 'docs/plan.md' } };
    const D = { file_id: 'file_dddddddddddddddd', name: 'spec.md', size: 7, source: 'upload', meta: { path: 'docs/specs/spec.md' } };
    listWorkspaceFiles.mockImplementation(() => ok({ files: [A, C, D] }));
    const onPick = vi.fn();
    wrap(<WorkspaceFilePicker workspace="acme" onPick={onPick} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText('a.md')).toBeInTheDocument());
    // The list names a folder file's folder.
    expect(screen.getByText(/docs\/specs\//)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Tree' }));
    expect(localStorage.getItem('files.picker.view')).toBe('tree');
    // The tree asks for more files at once, so its folders are whole.
    await waitFor(() => expect(listWorkspaceFiles).toHaveBeenLastCalledWith('acme', { q: '', limit: 1000 }));
    const tree = await screen.findByTestId('picker-tree');
    // Closed folders: only the root file and the top folder show.
    expect(tree).toHaveTextContent('docs');
    expect(screen.queryByText('plan.md')).toBeNull();
    fireEvent.click(screen.getByText('docs'));
    expect(screen.getByText('plan.md')).toBeInTheDocument();
    expect(screen.getByText('specs')).toBeInTheDocument();

    // The folder's box takes every file under it, its subfolder's too.
    fireEvent.click(screen.getByRole('button', { name: 'Pick every file in docs' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add 2 files' }));
    expect(onPick.mock.calls[0][0].map((r) => r.file_id)).toEqual([C.file_id, D.file_id]);
  });

  it('opens on the view picked last time, and a search opens every folder', async () => {
    localStorage.setItem('files.picker.view', 'tree');
    const C = { file_id: 'file_cccccccccccccccc', name: 'plan.md', size: 5, source: 'upload', meta: { path: 'docs/plan.md' } };
    listWorkspaceFiles.mockImplementation(() => ok({ files: [C] }));
    wrap(<WorkspaceFilePicker workspace="acme" onPick={() => {}} onClose={() => {}} />);
    await screen.findByTestId('picker-tree');
    expect(screen.queryByText('plan.md')).toBeNull();
    fireEvent.change(screen.getByPlaceholderText('Search files'), { target: { value: 'plan' } });
    await waitFor(() => expect(screen.getByText('plan.md')).toBeInTheDocument());
  });

  it('asks for a workspace when there is none', () => {
    wrap(<WorkspaceFilePicker workspace="" onPick={() => {}} onClose={() => {}} />);
    expect(screen.getByText(/Pick a workspace first/)).toBeInTheDocument();
  });
});

describe('FileIdsField', () => {
  it('names each id, marks a deleted one and removes on click', async () => {
    const onChange = vi.fn();
    wrap(<FileIdsField workspace="acme" value={[A.file_id, 'file_dddddddddddddddd']} onChange={onChange} />);
    await waitFor(() => expect(screen.getByText('a.md')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/deleted file/)).toBeInTheDocument());
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[0]);
    expect(onChange).toHaveBeenCalledWith(['file_dddddddddddddddd']);
  });

  it('adds picked files to the list', async () => {
    const onChange = vi.fn();
    wrap(<FileIdsField workspace="acme" value={[]} onChange={onChange} />);
    fireEvent.click(screen.getByText('Add from workspace files'));
    await waitFor(() => expect(screen.getByText('b.pdf')).toBeInTheDocument());
    fireEvent.click(screen.getByText('b.pdf'));
    fireEvent.click(screen.getByRole('button', { name: 'Add 1 file' }));
    expect(onChange).toHaveBeenCalledWith([B.file_id]);
  });
});

describe('chat request with a workspace file', () => {
  it('sends the id instead of the content', () => {
    const body = buildStreamRequestBody({
      targetMode: 'agent', isFlowMode: false, isTeamMode: false, selectedAgent: 'agent-1',
      text: 'read it', effectiveWorkspace: 'acme', selectedWorkspace: 'acme', historyPayload: [],
      pendingAttachments: [
        { filename: 'a.md', content: '', file_id: A.file_id, from_workspace: true },
        { filename: 'notes.txt', content: 'saved text', file_id: B.file_id, store_to_workspace: true },
        { filename: 'plain.txt', content: 'inline' },
      ],
    });
    expect(body.attachments).toEqual([
      { filename: 'a.md', content: '', store_to_workspace: false, file_id: A.file_id },
      { filename: 'notes.txt', content: '', store_to_workspace: true, file_id: B.file_id },
      { filename: 'plain.txt', content: 'inline', store_to_workspace: false },
    ]);
  });
});
