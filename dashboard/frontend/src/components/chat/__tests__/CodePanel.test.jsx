import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatPageContext } from '../context';
import CodePanel from '../CodePanel';
import { listViews, getView } from '../../../api';
import { runCode } from '../../../api/code';

vi.mock('../../../api', () => ({
  listViews: vi.fn(),
  getView: vi.fn(),
}));

vi.mock('../../../api/code', () => ({
  getCodeVersions: vi.fn().mockResolvedValue({ data: { versions: [] } }),
  saveCodeVersion: vi.fn(),
  getCodeDiff: vi.fn(),
  runCode: vi.fn(),
  getCodeRuns: vi.fn(),
  saveCodeToProject: vi.fn(),
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

function renderPanel(overrides = {}) {
  const page = {
    conversationRunIds: new Set(['r1']),
    projects: [],
    setInput: vi.fn(),
    textareaRef: { current: { focus: vi.fn() } },
    t: (key) => key,
    ...overrides,
  };
  return { page, ...render(
    <ChatPageContext.Provider value={page}>
      <CodePanel />
    </ChatPageContext.Provider>,
  ) };
}

describe('CodePanel', () => {
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

  it('discuss prefills the composer through setInput', async () => {
    listViews.mockResolvedValue({ data: { views: [VIEW_ROW] } });
    getView.mockResolvedValue({ data: VIEW_ENVELOPE });

    const { page } = renderPanel();

    const discussButton = await screen.findByText('chat.code.discuss');
    await userEvent.click(discussButton);

    expect(page.setInput).toHaveBeenCalled();
    expect(page.textareaRef.current.focus).toHaveBeenCalled();
  });
});
