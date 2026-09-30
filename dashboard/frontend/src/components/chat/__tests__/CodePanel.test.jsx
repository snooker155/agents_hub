import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatPageContext } from '../context';
import CodePanel from '../CodePanel';
import { useConversationCode } from '../useConversationCode';
import { listViews, getView } from '../../../api';
import { createCodeFromReply, getSnippetVersions, runCode, runSnippet, saveSnippetToProject, saveSnippetVersion } from '../../../api/code';

vi.mock('../../../api', () => ({
  listViews: vi.fn(),
  getView: vi.fn(),
}));

// CodeMirror does not take typing under jsdom; a plain textarea stands in.
vi.mock('../CodeEditor', () => ({
  default: ({ value, onChange }) => (
    <textarea data-testid="code-editor-textarea" value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

vi.mock('../../../api/code', () => ({
  getCodeVersions: vi.fn().mockResolvedValue({ data: { versions: [] } }),
  saveCodeVersion: vi.fn(),
  getCodeDiff: vi.fn(),
  runCode: vi.fn(),
  getCodeRuns: vi.fn(),
  saveCodeToProject: vi.fn(),
  createCodeFromReply: vi.fn(),
  saveSnippetToProject: vi.fn(),
  runSnippet: vi.fn(),
  getSnippetVersions: vi.fn().mockResolvedValue({ data: { versions: [] } }),
  saveSnippetVersion: vi.fn(),
  getSnippetDiff: vi.fn(),
}));

const VIEW_ROW = {
  view_id: 'v1',
  kind: 'code',
  title: 'hello.py',
  run_id: 'r1',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const VIEW_ENVELOPE = {
  view_id: 'v1',
  kind: 'code',
  title: 'hello.py',
  spec: {
    filename: 'hello.py',
    language: 'python',
    body: 'print(1)',
    version: 1,
    dependencies: [],
    description: '',
  },
};

// The page holds the snippet list (useConversationCode) and the panel reads
// it from the context; this stands in for the page.
function Harness({ page }) {
  const code = useConversationCode(page);
  return (
    <ChatPageContext.Provider value={{ ...page, ...code }}>
      <CodePanel />
    </ChatPageContext.Provider>
  );
}

function renderPanel(overrides = {}) {
  const page = {
    conversationRunIds: new Set(['r1']),
    projects: [],
    setInput: vi.fn(),
    textareaRef: { current: { focus: vi.fn() } },
    t: (key) => key,
    ...overrides,
  };
  return { page, ...render(<Harness page={page} />) };
}

describe('CodePanel', () => {
  // restoreMocks clears the factory defaults between tests.
  beforeEach(() => { getSnippetVersions.mockResolvedValue({ data: { versions: [] } }); });

  it('lists snippets returned by the mocked views API', async () => {
    listViews.mockResolvedValue({ data: { views: [VIEW_ROW] } });
    getView.mockResolvedValue({ data: VIEW_ENVELOPE });

    renderPanel();

    await waitFor(() => {
      expect(listViews).toHaveBeenCalledWith({ run_id: 'r1' });
    });
    await waitFor(() => {
      expect(screen.getAllByText('hello.py').length).toBeGreaterThan(0);
    });
  });

  it('shows stdout after a run', async () => {
    listViews.mockResolvedValue({ data: { views: [VIEW_ROW] } });
    getView.mockResolvedValue({ data: VIEW_ENVELOPE });
    runCode.mockResolvedValue({
      data: { ok: true, exit_code: 0, stdout: 'hello output', stderr: '', duration_ms: 12, sandbox: 'docker' },
    });

    renderPanel();

    const runButton = await screen.findByText('chat.code.run');
    await userEvent.click(runButton);

    await waitFor(() => {
      expect(screen.getByText('hello output')).toBeInTheDocument();
    });
    expect(runCode).toHaveBeenCalledWith('v1', expect.objectContaining({ body: 'print(1)' }));
  });

  it('shows why a run could not start, not only its exit code', async () => {
    listViews.mockResolvedValue({ data: { views: [VIEW_ROW] } });
    getView.mockResolvedValue({ data: VIEW_ENVELOPE });
    runCode.mockResolvedValue({ data: {
      ok: false, exit_code: -1, stdout: '', stderr: '', duration_ms: 0, sandbox: 'unavailable',
      error: 'the docker sandbox provider is not available (no docker daemon answers on this host)',
    } });
    renderPanel();
    await userEvent.click(await screen.findByText('chat.code.run'));
    await waitFor(() => expect(screen.getByTestId('run-error')).toHaveTextContent('no docker daemon'));
  });

  it('discuss prefills the composer through setInput', async () => {
    listViews.mockResolvedValue({ data: { views: [VIEW_ROW] } });
    getView.mockResolvedValue({ data: VIEW_ENVELOPE });

    const { page } = renderPanel();

    const discussButton = await screen.findByText('chat.code.discuss');
    await userEvent.click(discussButton);

    expect(page.setInput).toHaveBeenCalled();
    expect(page.textareaRef.current.focus).toHaveBeenCalled();
  });
  it('drops the old snippet when another conversation is opened', async () => {
    const OTHER_ROW = { ...VIEW_ROW, view_id: 'v2', title: 'other.js', run_id: 'r2' };
    const OTHER_ENVELOPE = {
      ...VIEW_ENVELOPE, view_id: 'v2', title: 'other.js',
      spec: { ...VIEW_ENVELOPE.spec, filename: 'other.js', language: 'javascript', body: 'x' },
    };
    listViews.mockImplementation(({ run_id: runId }) => Promise.resolve(
      { data: { views: runId === 'r1' ? [VIEW_ROW] : runId === 'r2' ? [OTHER_ROW] : [] } }));
    getView.mockImplementation((id) => Promise.resolve({ data: id === 'v1' ? VIEW_ENVELOPE : OTHER_ENVELOPE }));

    const { page, rerender } = renderPanel({ currentConvId: 'c1' });
    await waitFor(() => expect(screen.getAllByText('hello.py').length).toBeGreaterThan(0));

    const show = (value) => rerender(<Harness page={{ ...page, ...value }} />);
    // A new, empty chat: nothing of the old one stays on screen.
    show({ currentConvId: 'c2', conversationRunIds: new Set() });
    await waitFor(() => expect(screen.queryAllByText('hello.py')).toHaveLength(0));

    // A chat with a snippet of its own: that one is selected, not the old one.
    show({ currentConvId: 'c3', conversationRunIds: new Set(['r2']) });
    await waitFor(() => expect(screen.getAllByText('other.js').length).toBeGreaterThan(0));
    expect(screen.queryAllByText('hello.py')).toHaveLength(0);
  });

  it('lists a reply\'s code block under "From replies" and saves it as a view only on request', async () => {
    listViews.mockResolvedValue({ data: { views: [] } });
    createCodeFromReply.mockResolvedValue({ data: { ...VIEW_ENVELOPE, view_id: 'v9', title: 'from-reply.py',
      spec: { ...VIEW_ENVELOPE.spec, filename: 'from-reply.py', body: 'print(2)' } } });
    const setCodeFocus = vi.fn();
    const markReplySaved = vi.fn();
    const block = { id: 'reply:a1:0', message_id: 'a1', run_id: 'r1', index: 0, language: 'python', filename: '', name: 'from-reply.py', body: 'print(2)' };
    renderPanel({
      conversationRunIds: new Set(), replyBlocks: [block], savedReplyIds: new Set(),
      markReplySaved, setCodeFocus, currentConv: { workspace: 'ws' },
    });
    // Listed and selected, nothing created.
    await waitFor(() => expect(screen.getByTestId('reply-snippet')).toBeInTheDocument());
    expect(screen.getByText('chat.code.fromReplies')).toBeInTheDocument();
    expect(createCodeFromReply).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /chat.code.saveAsView/ })).toBeInTheDocument();
    // A block has every action a saved snippet has, and none of them makes a view.
    for (const name of ['chat.code.run', 'chat.code.saveVersion', 'chat.code.saveToProject', 'chat.code.versions']) {
      expect(screen.getByRole('button', { name: new RegExp(`${name}$`) })).toBeInTheDocument();
    }
    // Listed and headed by its name, not its first line.
    expect(screen.getAllByText('from-reply.py').length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole('button', { name: /chat.code.saveAsView/ }));
    await waitFor(() => expect(createCodeFromReply).toHaveBeenCalledWith(
      expect.objectContaining({ body: 'print(2)', language: 'python', filename: 'from-reply.py', runId: 'r1', workspace: 'ws' })));
    expect(markReplySaved).toHaveBeenCalledWith('reply:a1:0');
    expect(setCodeFocus).toHaveBeenCalledWith(expect.objectContaining({ view: expect.objectContaining({ view_id: 'v9' }) }));
  });

  it('"Save to project" on a reply\'s block writes the file straight into the project, no view made', async () => {
    listViews.mockResolvedValue({ data: { views: [] } });
    saveSnippetToProject.mockResolvedValue({ data: { ok: true, path: 'src/from-reply.py' } });
    const block = { id: 'reply:a1:0', message_id: 'a1', run_id: 'r1', index: 0, language: 'python', filename: '', name: 'from-reply.py', body: 'print(2)' };
    renderPanel({
      conversationRunIds: new Set(), replyBlocks: [block], savedReplyIds: new Set(),
      projects: [{ id: 'p1', name: 'Proj' }], currentConv: { workspace: 'ws' },
    });
    await screen.findByTestId('reply-snippet');

    await userEvent.click(screen.getByRole('button', { name: /chat.code.saveToProject$/ }));
    // The form opens at once, path prefilled with the block's name.
    await waitFor(() => expect(screen.getByDisplayValue('from-reply.py')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByRole('combobox'), 'p1');
    const pathInput = screen.getByDisplayValue('from-reply.py');
    await userEvent.clear(pathInput);
    await userEvent.type(pathInput, 'src/from-reply.py');
    await userEvent.click(screen.getByRole('button', { name: 'chat.code.save' }));

    await waitFor(() => expect(saveSnippetToProject).toHaveBeenCalledWith(
      { projectId: 'p1', path: 'src/from-reply.py', body: 'print(2)', overwrite: false }));
    expect(createCodeFromReply).not.toHaveBeenCalled();
    await screen.findByText('chat.code.savedToProject');
    // Still a reply's block, still listed as one.
    expect(screen.getByTestId('reply-snippet')).toBeInTheDocument();
  });

  it('runs a reply\'s block as it is, through the body route, no view made', async () => {
    listViews.mockResolvedValue({ data: { views: [] } });
    runSnippet.mockResolvedValue({ data: { ok: true, exit_code: 0, stdout: 'two', stderr: '', duration_ms: 4, sandbox: 'docker' } });
    const block = { id: 'reply:a1:0', message_id: 'a1', run_id: 'r1', index: 0, language: 'python', filename: '', name: 'from-reply.py', body: 'print(2)' };
    renderPanel({ conversationRunIds: new Set(), replyBlocks: [block], savedReplyIds: new Set(), currentConv: { workspace: 'ws' }, currentConvId: 'c1' });
    await screen.findByTestId('reply-snippet');

    await userEvent.click(screen.getByRole('button', { name: /chat.code.run$/ }));
    await waitFor(() => expect(runSnippet).toHaveBeenCalledWith(
      { language: 'python', body: 'print(2)', mountWorkspace: false, workspace: 'ws' }));
    await screen.findByText('two');
    expect(runCode).not.toHaveBeenCalled();
    expect(createCodeFromReply).not.toHaveBeenCalled();
    expect(screen.getByTestId('reply-snippet')).toBeInTheDocument();
  });

  it('keeps a reply\'s block\'s versions under its own key, and opens on the latest', async () => {
    listViews.mockResolvedValue({ data: { views: [] } });
    const block = { id: 'reply:a1:0', message_id: 'a1', run_id: 'r1', index: 0, language: 'python', filename: '', name: 'from-reply.py', body: 'print(2)' };
    const history = [
      { version: 1, body: 'print(2)', author: 'agent', note: 'from the reply' },
      { version: 2, body: 'print(3)', author: 'user', note: '' },
    ];
    getSnippetVersions.mockResolvedValue({ data: { versions: history } });
    saveSnippetVersion.mockResolvedValue({ data: { versions: [...history, { version: 3, body: 'print(4)', author: 'user', note: '' }] } });
    renderPanel({ conversationRunIds: new Set(), replyBlocks: [block], savedReplyIds: new Set(), currentConv: { workspace: 'ws' }, currentConvId: 'c1' });
    await screen.findByTestId('reply-snippet');

    // Its history is read by the conversation and block key, and the editor shows the latest.
    await waitFor(() => expect(getSnippetVersions).toHaveBeenCalledWith('c1:reply:a1:0', 'ws'));
    await screen.findByText('chat.code.versionShort');
    await waitFor(() => expect(screen.getByTestId('code-editor-textarea').value).toBe('print(3)'));

    await userEvent.type(screen.getByTestId('code-editor-textarea'), '4');
    await userEvent.click(screen.getByRole('button', { name: /chat.code.saveVersion$/ }));
    await waitFor(() => expect(saveSnippetVersion).toHaveBeenCalledWith(
      expect.objectContaining({ key: 'c1:reply:a1:0', workspace: 'ws', base: 'print(2)', body: 'print(3)4' })));
    expect(createCodeFromReply).not.toHaveBeenCalled();
  });
});
