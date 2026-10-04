import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../i18n';
import Assistant from '../Assistant';
import { PREFS_KEY } from '../../components/assistant/assistantState';

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
    clearAssistant: vi.fn(() => Promise.resolve({ data: {} })),
    stopAssistant: vi.fn(() => Promise.resolve({ data: {} })),
    streamAssistantTurn: vi.fn(),
    transcribeRecording: vi.fn(),
    speakAssistant: vi.fn(() => Promise.resolve(null)),
  };
});
vi.mock('../../api/assistant', () => assistantApi);

const recorder = vi.hoisted(() => ({
  start: vi.fn(() => Promise.resolve()),
  stop: vi.fn(() => Promise.resolve({ blob: new Blob(['x'], { type: 'audio/webm' }), type: 'audio/webm', seconds: 2 })),
  cancel: vi.fn(), recording: false, level: 0, error: '', supported: true,
}));
vi.mock('../../components/assistant/useRecorder', () => ({ default: () => recorder }));

const META = {
  messages: [], trace: [], chat_ref: { kind: 'assistant', id: 'user-local' }, agent_id: 'assistant',
  mode: 'personal', home: 'default', workspaces: ['default', 'team'], service_available: false,
  voice: { transcription: null, speech: null, max_seconds: 120 },
};

const show = () => render(
  <MemoryRouter initialEntries={['/assistant']}>
    <I18nProvider><Assistant /></I18nProvider>
  </MemoryRouter>,
);

describe('the Assistant page', () => {
  beforeEach(() => {
    // jsdom has no audio; the page only needs play() to resolve.
    vi.spyOn(window.HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve());
    vi.spyOn(window.HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    localStorage.removeItem(PREFS_KEY);
    Object.values(assistantApi).forEach((fn) => fn?.mockReset?.());
    assistantApi.getAssistant.mockResolvedValue({ data: META });
    assistantApi.speakAssistant.mockResolvedValue(null);
  });

  it('has no personal / service switch in single mode', async () => {
    show();
    await screen.findByRole('combobox');
    expect(screen.queryByTestId('assistant-mode-switch')).toBeNull();
  });

  it('offers the switch to an administrator in multi mode and loads the service thread', async () => {
    assistantApi.getAssistant.mockResolvedValue({ data: { ...META, service_available: true } });
    show();
    const service = await screen.findByRole('tab', { name: 'Service' });
    fireEvent.click(service);
    await waitFor(() => expect(assistantApi.getAssistant).toHaveBeenLastCalledWith('service'));
  });

  it('sends a typed turn to the chosen workspace and shows the answer large', async () => {
    assistantApi.streamAssistantTurn.mockImplementation(async ({ body, onEvent }) => {
      expect(body).toMatchObject({ message: 'what is new?', workspace: 'team', mode: 'personal', voice: false });
      onEvent({ type: 'run', run_id: 'r1' });
      onEvent({ type: 'token', token: 'Two tasks wait. ' });
      onEvent({ type: 'message', content: 'Two tasks wait.\n\n[Open them](/tasks)', run_id: 'r1' });
      onEvent({ type: 'done', run_id: 'r1' });
    });
    show();
    fireEvent.change(await screen.findByRole('combobox'), { target: { value: 'team' } });
    fireEvent.change(screen.getByPlaceholderText('Or type here'), { target: { value: 'what is new?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByTestId('assistant-answer')).toHaveTextContent('Two tasks wait.'));
    // A typed turn is not read aloud unless the page is in voice only mode.
    expect(assistantApi.speakAssistant).not.toHaveBeenCalled();
  });

  it('in voice only mode there is no text field', async () => {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ voiceOnly: true }));
    assistantApi.getAssistant.mockResolvedValue({
      data: { ...META, voice: { ...META.voice, speech: { provider: 'openai', model: 'tts', voice: '', voices: ['nova'] } } },
    });
    show();
    await screen.findByTestId('assistant-page');
    expect(screen.queryByPlaceholderText('Or type here')).toBeNull();
  });

  it('says a refusal for the budget in words, not as a raw error', async () => {
    assistantApi.streamAssistantTurn.mockRejectedValue(
      new assistantApi.AssistantError(402, { code: 'budget', message: 'limit' }));
    show();
    fireEvent.change(await screen.findByPlaceholderText('Or type here'), { target: { value: 'run the team' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText(/spending limit is used up/)).toBeInTheDocument();
  });

  it('a spoken question is shown for correction, then its answer is read aloud from its run', async () => {
    assistantApi.getAssistant.mockResolvedValue({
      data: { ...META, voice: { max_seconds: 120, transcription: { provider: 'openai', model: 'stt' },
        speech: { provider: 'openai', model: 'tts', voice: '', voices: [] } } },
    });
    assistantApi.transcribeRecording.mockResolvedValue({ text: 'what is new?', consent: null });
    assistantApi.streamAssistantTurn.mockImplementation(async ({ body, onEvent }) => {
      expect(body.voice).toBe(true);
      onEvent({ type: 'run', run_id: 'r9' });
      onEvent({ type: 'token', token: 'Two tasks wait for you. ' });
      onEvent({ type: 'token', token: 'Both are due today.\n\n- one' });
      onEvent({ type: 'message', content: 'Two tasks wait for you. Both are due today.\n\n- one', run_id: 'r9' });
    });
    show();
    const talk = await screen.findByRole('button', { name: 'Hold to talk' });
    await waitFor(() => expect(talk).not.toBeDisabled());
    fireEvent.pointerDown(talk);
    await waitFor(() => expect(recorder.start).toHaveBeenCalled());
    fireEvent.pointerUp(talk);
    const field = await screen.findByDisplayValue('what is new?');
    expect(assistantApi.transcribeRecording).toHaveBeenCalledWith(expect.any(Blob), { mode: 'personal', language: 'en' });
    fireEvent.keyDown(field, { key: 'Enter' });
    await waitFor(() => expect(assistantApi.speakAssistant).toHaveBeenCalledTimes(2));
    expect(assistantApi.speakAssistant.mock.calls.map(([b]) => [b.run_id, b.text])).toEqual([
      ['r9', 'Two tasks wait for you.'], ['r9', 'Both are due today.'],
    ]);
  });

  it('a spoken yes while a card waits answers the card instead of starting a turn', async () => {
    assistantApi.getAssistant.mockResolvedValue({
      data: { ...META, voice: { max_seconds: 120, transcription: { provider: 'openai', model: 'stt' }, speech: null } },
    });
    let releaseTurn;
    assistantApi.streamAssistantTurn
      .mockImplementationOnce(async ({ onEvent }) => {
        onEvent({ type: 'run', run_id: 'r1' });
        onEvent({ type: 'tool_approval', approval_id: 'a1', run_id: 'r1', tool: 'run_team_tool', status: 'pending', input: {} });
        await new Promise((r) => { releaseTurn = r; });
      })
      .mockImplementationOnce(async ({ body, onEvent }) => {
        expect(body).toMatchObject({ message: 'yes', voice: true });
        onEvent({ type: 'voice_answer', approval_id: 'a1', decision: 'approve', status: 'approved' });
      });
    assistantApi.transcribeRecording.mockResolvedValue({ text: 'yes', consent: 'approve' });
    show();
    fireEvent.change(await screen.findByPlaceholderText('Or type here'), { target: { value: 'run the team' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByText('run_team_tool', { exact: false });
    const talk = screen.getByRole('button', { name: 'Hold to talk' });
    fireEvent.pointerDown(talk);
    await waitFor(() => expect(recorder.start).toHaveBeenCalled());
    fireEvent.pointerUp(talk);
    await waitFor(() => expect(assistantApi.streamAssistantTurn).toHaveBeenCalledTimes(2));
    releaseTurn();
  });
});
