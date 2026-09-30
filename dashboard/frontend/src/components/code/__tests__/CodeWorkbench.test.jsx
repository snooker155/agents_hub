import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { I18nProvider } from '../../../i18n';
import CodeWorkbench from '../CodeWorkbench';
import { getCodeRuns, runCode, saveCodeBody, saveCodeVersion } from '../../../api/code';

// CodeMirror does not take typing under jsdom; a plain textarea stands in.
vi.mock('../../chat/CodeEditor', () => ({
  default: ({ value, onChange }) => (
    <textarea data-testid="code-editor-textarea" value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

vi.mock('../../../api/code', () => ({
  getCodeVersions: vi.fn(),
  saveCodeVersion: vi.fn(),
  saveCodeBody: vi.fn(),
  getCodeDiff: vi.fn(),
  runCode: vi.fn(),
  getCodeRuns: vi.fn(),
  saveCodeToProject: vi.fn(),
  runSnippet: vi.fn(),
  getSnippetVersions: vi.fn(),
  saveSnippetVersion: vi.fn(),
  getSnippetDiff: vi.fn(),
  saveSnippetToProject: vi.fn(),
}));

const VIEW = {
  view_id: 'v1', kind: 'code', title: 'hello.py', workspace: 'ws',
  spec: { filename: 'hello.py', language: 'python', body: 'print(1)', version: 2 },
};

const show = (props = {}) => render(
  <I18nProvider><CodeWorkbench envelope={VIEW} workspace="ws" projects={[]} {...props} /></I18nProvider>,
);

describe('CodeWorkbench on a code view', () => {
  beforeEach(() => { getCodeRuns.mockResolvedValue({ data: { runs: [] } }); });

  it('runs the draft against the view and shows the output', async () => {
    runCode.mockResolvedValue({ data: { ok: true, exit_code: 0, stdout: 'one', stderr: '', duration_ms: 3, sandbox: 'docker' } });
    show();
    await userEvent.click(screen.getByRole('button', { name: /^Run$/ }));
    await waitFor(() => expect(runCode).toHaveBeenCalledWith('v1', { body: 'print(1)', mountWorkspace: false }));
    await screen.findByText('one');
  });

  it('lists the recorded runs and shows a picked one as history', async () => {
    getCodeRuns.mockResolvedValue({ data: { runs: [
      { version: 2, exit_code: 1, stdout: '', stderr: 'boom', duration_ms: 9, created_at: '2026-09-29T10:00:00Z' },
      { version: 1, exit_code: 0, stdout: 'fine', stderr: '', duration_ms: 4, created_at: '2026-09-28T10:00:00Z' },
    ] } });
    show({ runHistory: true });
    await userEvent.click(screen.getByRole('button', { name: /Runs/ }));
    await waitFor(() => expect(getCodeRuns).toHaveBeenCalledWith('v1'));
    const history = await screen.findByTestId('run-history');
    const rows = history.querySelectorAll('button');
    expect(rows).toHaveLength(2);
    await userEvent.click(rows[1]);
    await screen.findByText('fine');
    expect(screen.getByTestId('recorded-run')).toHaveTextContent('v1');
    expect(runCode).not.toHaveBeenCalled();
  });

  it('offers Discuss and Edit only with a chat to prefill, and prefills it with the draft', async () => {
    const { unmount } = show();
    expect(screen.queryByRole('button', { name: /Discuss/ })).toBeNull();
    unmount();
    const setInput = vi.fn();
    show({ chat: { setInput } });
    await userEvent.click(screen.getByRole('button', { name: /Discuss/ }));
    expect(setInput).toHaveBeenCalledWith(expect.stringContaining('print(1)'));
    await userEvent.click(screen.getByRole('button', { name: /Edit/ }));
    expect(setInput).toHaveBeenLastCalledWith(expect.stringContaining('hello.py'));
  });

  it('"Save" overwrites the current version, "Save version" adds one; both wait for an edit', async () => {
    const onVersionSaved = vi.fn();
    saveCodeBody.mockResolvedValue({ data: { ...VIEW, spec: { ...VIEW.spec, body: 'print(1)!' } } });
    show({ onVersionSaved });
    const save = screen.getByRole('button', { name: /^Save$/ });
    const saveVersion = screen.getByRole('button', { name: /^Save version$/ });
    expect(save).toBeDisabled();
    expect(saveVersion).toBeDisabled();

    await userEvent.type(screen.getByTestId('code-editor-textarea'), '!');
    expect(save).toBeEnabled();
    await userEvent.click(save);
    await waitFor(() => expect(saveCodeBody).toHaveBeenCalledWith('v1', 'print(1)!'));
    expect(saveCodeVersion).not.toHaveBeenCalled();
    expect(onVersionSaved).toHaveBeenCalledWith(expect.objectContaining({ spec: expect.objectContaining({ body: 'print(1)!' }) }));
  });

  it('a reply\'s block has no plain Save, only Save version', () => {
    show({ envelope: { view_id: 'reply:a1:0', kind: 'code', reply: true, spec: { filename: 'x.py', language: 'python', body: 'a' } }, isReply: true, replyKey: 'k' });
    expect(screen.queryByRole('button', { name: /^Save$/ })).toBeNull();
    expect(screen.getByRole('button', { name: /^Save version$/ })).toBeInTheDocument();
  });
});
