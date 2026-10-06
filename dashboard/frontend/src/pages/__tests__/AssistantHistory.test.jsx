import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../i18n';
import Assistant from '../Assistant';
import { PREFS_KEY } from '../../components/assistant/assistantState';

// The side column of the Assistant page: the transcript with its Clear
// button, past conversations in the History tab, and the switch the assistant
// asks for with `assistant_conversations` (routes/assistant.py).

const assistantApi = vi.hoisted(() => {
  class AssistantError extends Error {
    constructor(status, detail) {
      super(detail?.message || String(status));
      this.status = status;
      this.code = detail?.code || '';
    }
  }
  return {
    AssistantError,
    getAssistant: vi.fn(),
    clearAssistant: vi.fn(),
    forgetAssistantConversation: vi.fn(),
    stopAssistant: vi.fn(),
    streamAssistantTurn: vi.fn(),
    transcribeRecording: vi.fn(),
    speakAssistant: vi.fn(),
  };
});
vi.mock('../../api/assistant', () => assistantApi);

const sessionsApi = vi.hoisted(() => ({
  getEntityChatSessions: vi.fn(),
  activateEntityChatSession: vi.fn(),
  deleteEntityChatSession: vi.fn(),
}));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal()), ...sessionsApi }));

vi.mock('../../components/assistant/useRecorder', () => ({
  default: () => ({
    start: vi.fn(), stop: vi.fn(), cancel: vi.fn(), recording: false, level: 0, error: '', supported: true,
  }),
}));
vi.mock('../../components/assistant/useHandsFree', () => ({
  default: () => ({
    start: vi.fn(() => Promise.resolve(false)), stop: vi.fn(), tune: vi.fn(), reset: vi.fn(),
    listening: false, level: 0, speech: false, error: '', engine: 'browser',
  }),
  micAllowed: () => Promise.resolve(false),
}));
const ws = vi.hoisted(() => ({ selected: 'default' }));
vi.mock('../../components/workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: ws.selected }) }));

const META = {
  messages: [{ role: 'user', content: 'about cats' }, { role: 'assistant', content: 'Cats sleep a lot.' }],
  trace: [], chat_ref: { kind: 'assistant', id: 'user-local' }, agent_id: 'assistant',
  mode: 'personal', home: 'default', workspaces: ['default'], service_available: false,
  voice: { transcription: null, speech: null, max_seconds: 120 },
};
const SESSIONS = {
  has_history: true,
  sessions: [
    { id: 's2', epoch: 2, active: true, title: 'about cats', messages: 2, updated_at: '2026-10-05T10:00:00Z' },
    { id: 's0', epoch: 0, active: false, title: 'about dogs', messages: 4, updated_at: '2026-10-04T10:00:00Z' },
  ],
};

const show = () => render(
  <MemoryRouter initialEntries={['/assistant']}>
    <I18nProvider><Assistant /></I18nProvider>
  </MemoryRouter>,
);

describe('the Assistant page side column', () => {
  beforeEach(() => {
    // A wide screen: the side column is drawn only from 1024px up.
    vi.spyOn(window, 'matchMedia').mockImplementation((query) => ({
      matches: query.includes('min-width'), media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }));
    localStorage.removeItem(PREFS_KEY);
    ws.selected = 'default';
    Object.values(assistantApi).forEach((fn) => fn?.mockReset?.());
    Object.values(sessionsApi).forEach((fn) => fn.mockReset());
    assistantApi.getAssistant.mockResolvedValue({ data: META });
    assistantApi.forgetAssistantConversation.mockResolvedValue({ data: { cleared: true } });
    sessionsApi.getEntityChatSessions.mockResolvedValue({ data: SESSIONS });
  });

  it('has a header as tall as the page header', async () => {
    show();
    const tabs = await screen.findByTestId('assistant-side-tabs');
    expect(tabs.className).toContain('h-14');
    expect(screen.getByRole('heading', { level: 1 }).closest('header').className).toContain('min-h-14');
  });

  it('clears the transcript after a confirmation, without keeping it in the history', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    show();
    await within(await screen.findByTestId('assistant-transcript')).findByText('about cats');
    fireEvent.click(screen.getByRole('button', { name: 'Clear the transcript' }));
    await waitFor(() => expect(assistantApi.forgetAssistantConversation).toHaveBeenCalledWith('personal'));
    expect(confirm).toHaveBeenCalled();
    await waitFor(() => expect(within(screen.getByTestId('assistant-transcript')).queryByText('about cats')).toBeNull());
    expect(assistantApi.clearAssistant).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('opens a past conversation from the History tab and continues it', async () => {
    sessionsApi.activateEntityChatSession.mockResolvedValue({
      data: { activated: true, messages: [{ role: 'user', content: 'about dogs' }], trace: [], ...SESSIONS },
    });
    show();
    await screen.findByTestId('assistant-transcript');
    fireEvent.click(screen.getByRole('tab', { name: 'History' }));
    const list = await screen.findByTestId('assistant-history');
    fireEvent.click(await within(list).findByText('about dogs'));
    await waitFor(() => expect(sessionsApi.activateEntityChatSession)
      .toHaveBeenCalledWith({ kind: 'assistant', id: 'user-local' }, 's0'));
    const transcript = await screen.findByTestId('assistant-transcript');
    expect(within(transcript).getByText('about dogs')).toBeTruthy();
  });

  it('lists the conversations of the workspace the page is in', async () => {
    assistantApi.getAssistant.mockResolvedValue({ data: { ...META, workspaces: ['default', 'sales'] } });
    sessionsApi.getEntityChatSessions.mockResolvedValue({ data: { has_history: true, sessions: [
      { ...SESSIONS.sessions[0], workspace: 'sales' },
      { id: 's1', epoch: 1, active: false, title: 'about sales', messages: 2, workspace: 'sales' },
      // From before turns were stamped: the home's.
      { ...SESSIONS.sessions[1], workspace: null },
    ] } });
    const history = async () => {
      await screen.findByTestId('assistant-transcript');
      fireEvent.click(screen.getByRole('tab', { name: 'History' }));
      return screen.findByTestId('assistant-history');
    };
    ws.selected = 'sales';
    const view = show();
    let list = await history();
    expect(await within(list).findByText('about sales')).toBeTruthy();
    expect(within(list).getByText('about cats')).toBeTruthy();
    expect(within(list).queryByText('about dogs')).toBeNull();
    view.unmount();

    ws.selected = 'default';
    show();
    list = await history();
    expect(await within(list).findByText('about dogs')).toBeTruthy();
    expect(within(list).queryByText('about sales')).toBeNull();
  });

  it('reloads the thread when the assistant switched to another conversation', async () => {
    assistantApi.streamAssistantTurn.mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'run', run_id: 'r1' });
      onEvent({ type: 'message', content: 'Opening it.', run_id: 'r1' });
      onEvent({ type: 'done', run_id: 'r1' });
      onEvent({ type: 'conversation', session_id: 's0' });
    });
    show();
    await screen.findByTestId('assistant-transcript');
    expect(assistantApi.getAssistant).toHaveBeenCalledTimes(1);
    assistantApi.getAssistant.mockResolvedValue({
      data: { ...META, messages: [{ role: 'user', content: 'about dogs' }] },
    });
    fireEvent.change(screen.getByPlaceholderText('Or type here'), { target: { value: 'back to the dogs' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(assistantApi.getAssistant).toHaveBeenCalledTimes(2));
    expect(await within(screen.getByTestId('assistant-transcript')).findByText('about dogs')).toBeTruthy();
  });
});
