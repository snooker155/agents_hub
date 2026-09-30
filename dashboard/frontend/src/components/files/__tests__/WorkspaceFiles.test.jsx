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

beforeEach(() => vi.clearAllMocks());

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
