import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../../i18n';

const ok = (data) => Promise.resolve({ data });
const getProjectTasksChat = vi.fn();
const clearProjectTasksChat = vi.fn(() => ok({ cleared: true }));
const streamProjectTasksGenerate = vi.fn(async ({ onEvent }) => { onEvent({ type: 'message', content: 'Made 3 tasks.' }); onEvent({ type: 'done', created: 3 }); });
const sessions = vi.fn();
const getWorkspacePlannerChat = vi.fn(() => ok({ messages: [], trace: [], chat_ref: { kind: 'wstasks', id: 'ws1' } }));
const streamWorkspacePlanner = vi.fn(async ({ onEvent }) => { onEvent({ type: 'done', created: 0 }); });

vi.mock('../../../api', async (importOriginal) => ({
  ...(await importOriginal()),
  getProjectTasksChat: (...a) => getProjectTasksChat(...a),
  clearProjectTasksChat: (...a) => clearProjectTasksChat(...a),
  streamProjectTasksGenerate: (...a) => streamProjectTasksGenerate(...a),
  stopProjectGraphChat: vi.fn(),
  getWorkspacePlannerChat: (...a) => getWorkspacePlannerChat(...a),
  clearWorkspacePlannerChat: vi.fn(() => ok({ cleared: true })),
  streamWorkspacePlanner: (...a) => streamWorkspacePlanner(...a),
  stopWorkspacePlanner: vi.fn(),
  getEntityChatSessions: (...a) => sessions(...a),
}));

import PlannerChat from '../PlannerChat';

const show = (props = {}) => render(<I18nProvider><PlannerChat projectId="p1" {...props} /></I18nProvider>);

beforeEach(() => {
  getProjectTasksChat.mockImplementation(() => ok({
    messages: [{ role: 'user', content: 'plan it' }, { role: 'assistant', content: 'Done.' }],
    trace: [], chat_ref: { kind: 'projtasks', id: 'p1' },
  }));
  sessions.mockImplementation(() => ok({ has_history: true, sessions: [
    { id: 's1', active: true, title: 'plan it', messages: 2 },
    { id: 's0', active: false, title: 'older', messages: 4 },
  ] }));
  streamProjectTasksGenerate.mockClear();
  clearProjectTasksChat.mockClear();
  getProjectTasksChat.mockClear();
  localStorage.removeItem('agent_hub_chat_view_mode');
});

describe('PlannerChat', () => {
  it('runs a requested generation once the transcript is loaded, in the interface language', async () => {
    const onGenerateStarted = vi.fn();
    const onGenerated = vi.fn();
    show({ generateRequested: true, onGenerateStarted, onGenerated });
    await waitFor(() => expect(streamProjectTasksGenerate).toHaveBeenCalledTimes(1));
    expect(onGenerateStarted).toHaveBeenCalled();
    expect(streamProjectTasksGenerate.mock.calls[0][0].message).toBe('Generate tasks from the architecture & process graphs.');
    expect(await screen.findByText('Made 3 tasks.')).toBeInTheDocument();
    expect(screen.getByText('Done.')).toBeInTheDocument();
    await waitFor(() => expect(onGenerated).toHaveBeenCalled());
  });

  it('starts a new chat instead of clearing, and offers the history', async () => {
    show();
    expect(await screen.findByText('Done.')).toBeInTheDocument();
    expect(screen.queryByText(/clear chat/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /new chat/i }));
    await waitFor(() => expect(clearProjectTasksChat).toHaveBeenCalledWith('p1'));
    await waitFor(() => expect(screen.queryByText('Done.')).not.toBeInTheDocument());
    fireEvent.click(await screen.findByRole('button', { name: /history/i }));
    expect(await screen.findByText('older')).toBeInTheDocument();
  });

  it('marks a tool done when it ends, while the run goes on', async () => {
    // The Build view keeps every step as a card.
    localStorage.setItem('agent_hub_chat_view_mode', 'build');
    let finish;
    streamProjectTasksGenerate.mockImplementationOnce(async ({ onEvent }) => {
      onEvent({ type: 'tool_start', tool: 'list_tasks', input: '{}' });
      onEvent({ type: 'tool_end', tool: 'list_tasks', status: 'ok' });
      onEvent({ type: 'tool_start', tool: 'create_task', input: '{}' });
      onEvent({ type: 'tool_error', tool: 'create_task', error: 'bad title' });
      onEvent({ type: 'tool_start', tool: 'add_subtask', input: '{}' });
      await new Promise((r) => { finish = r; });
      onEvent({ type: 'done', created: 0 });
    });
    show({ generateRequested: true, onGenerateStarted: vi.fn() });
    await screen.findByText(/add_subtask/);
    const marks = () => screen.getAllByTestId('tool-status').map((m) => m.dataset.status);
    expect(marks()).toEqual(['ok', 'error', 'running']);
    // Once the run is over nothing is left spinning, even a step whose end
    // never came.
    finish();
    await waitFor(() => expect(marks()).toEqual(['ok', 'error', 'unknown']));
  });

  it('plans over a workspace with the workspace calls', async () => {
    render(<I18nProvider><PlannerChat workspace="ws1" /></I18nProvider>);
    await waitFor(() => expect(getWorkspacePlannerChat).toHaveBeenCalledWith('ws1'));
    expect(getProjectTasksChat).not.toHaveBeenCalled();
    expect(await screen.findByText(/task tree/i)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'split the menu task' } });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(streamWorkspacePlanner).toHaveBeenCalledTimes(1));
    expect(streamWorkspacePlanner.mock.calls[0][0]).toMatchObject({ workspace: 'ws1', message: 'split the menu task' });
  });
});
