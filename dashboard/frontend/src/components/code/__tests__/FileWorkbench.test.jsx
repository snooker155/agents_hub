import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { I18nProvider } from '../../../i18n';
import FileWorkbench from '../FileWorkbench';
import { runWorkspaceFile } from '../../../api/code';
import { getWorkspaceFileContent, saveWorkspaceFileContent } from '../../../api/workspaces';

// CodeMirror does not take typing under jsdom; a plain textarea stands in.
vi.mock('../../chat/CodeEditor', () => ({
  default: ({ value, onChange }) => (
    <textarea data-testid="code-editor-textarea" value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

vi.mock('../../../api/code', () => ({
  getCodeVersions: vi.fn(), saveCodeVersion: vi.fn(), saveCodeBody: vi.fn(), getCodeDiff: vi.fn(),
  runCode: vi.fn(), getCodeRuns: vi.fn(), saveCodeToProject: vi.fn(), runSnippet: vi.fn(),
  getSnippetVersions: vi.fn(), saveSnippetVersion: vi.fn(), getSnippetDiff: vi.fn(),
  saveSnippetToProject: vi.fn(), runWorkspaceFile: vi.fn(),
}));

vi.mock('../../../api/workspaces', () => ({
  getWorkspaceFileContent: vi.fn(),
  saveWorkspaceFileContent: vi.fn(),
}));

const show = (props = {}) => render(
  <I18nProvider><FileWorkbench workspace="ws" path="pkg/main.py" {...props} /></I18nProvider>,
);

describe('FileWorkbench', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getWorkspaceFileContent.mockResolvedValue({ data: { content: 'print(1)' } });
    runWorkspaceFile.mockResolvedValue({ data: {
      ok: true, exit_code: 0, stdout: 'one', stderr: '', duration_ms: 3, sandbox: 'container', mode: 'container',
    } });
  });

  it('runs the file as saved from its folder and says it ran in its own container', async () => {
    show();
    await screen.findByDisplayValue('print(1)');
    // A file has no versions, no project save and no mount switch: it runs with its folder.
    expect(screen.queryByRole('button', { name: /Versions/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Save version/ })).toBeNull();
    expect(screen.queryByText(/Mount workspace/i)).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /^Run$/ }));
    await waitFor(() => expect(runWorkspaceFile).toHaveBeenCalledWith({ workspace: 'ws', path: 'pkg/main.py', body: undefined }));
    await screen.findByText('one');
    expect(screen.getByTestId('run-sandbox')).toHaveTextContent('own container');
  });

  it('runs the unsaved text in the file\'s place, and Save writes it into the file', async () => {
    saveWorkspaceFileContent.mockResolvedValue({ data: { path: 'pkg/main.py', content: 'print(2)' } });
    const onSaved = vi.fn();
    show({ onSaved });
    const editor = await screen.findByDisplayValue('print(1)');
    await userEvent.clear(editor);
    await userEvent.type(editor, 'print(2)');
    await userEvent.click(screen.getByRole('button', { name: /^Run$/ }));
    await waitFor(() => expect(runWorkspaceFile).toHaveBeenCalledWith({ workspace: 'ws', path: 'pkg/main.py', body: 'print(2)' }));
    await userEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => expect(saveWorkspaceFileContent).toHaveBeenCalledWith('ws', 'pkg/main.py', 'print(2)'));
    expect(onSaved).toHaveBeenCalledWith('pkg/main.py');
    // Saved: nothing unsaved is left.
    await waitFor(() => expect(screen.getByRole('button', { name: /^Save$/ })).toBeDisabled());
  });

  it('runs once on its own when opened by Run', async () => {
    const { rerender } = show({ autoRun: 1 });
    await waitFor(() => expect(runWorkspaceFile).toHaveBeenCalledTimes(1));
    rerender(<I18nProvider><FileWorkbench workspace="ws" path="pkg/main.py" autoRun={1} /></I18nProvider>);
    await screen.findByText('one');
    expect(runWorkspaceFile).toHaveBeenCalledTimes(1);
  });

  it('opens a file it cannot run without Run enabled', async () => {
    getWorkspaceFileContent.mockResolvedValue({ data: { content: 'export const a = 1' } });
    render(<I18nProvider><FileWorkbench workspace="ws" path="src/a.ts" autoRun={1} /></I18nProvider>);
    await screen.findByDisplayValue('export const a = 1');
    expect(screen.getByRole('button', { name: /^Run$/ })).toBeDisabled();
    expect(runWorkspaceFile).not.toHaveBeenCalled();
  });

  it('shows why the file could not be read', async () => {
    getWorkspaceFileContent.mockRejectedValue({ response: { data: { detail: 'File not found' } } });
    show();
    expect(await screen.findByTestId('file-workbench-error')).toHaveTextContent('File not found');
  });
});
