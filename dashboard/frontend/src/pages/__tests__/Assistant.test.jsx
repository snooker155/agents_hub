import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
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

// The open microphone: the tests say what it heard through its handlers.
const ear = vi.hoisted(() => ({
  opts: null, start: vi.fn(() => Promise.resolve(true)), stop: vi.fn(), tune: vi.fn(), reset: vi.fn(),
  listening: true, level: 0, speech: false, error: '', engine: 'server', micAllowed: vi.fn(() => Promise.resolve(false)),
}));
vi.mock('../../components/assistant/useHandsFree', () => ({
  default: (opts) => { ear.opts = opts; return ear; },
  micAllowed: () => ear.micAllowed(),
}));

// The workspace picked in the header; the page follows it.
const header = vi.hoisted(() => ({ selectedWorkspace: 'team' }));
vi.mock('../../components/workspace', () => ({ useWorkspace: () => header }));

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
    ['start', 'stop', 'tune', 'reset'].forEach((k) => ear[k].mockClear());
    ear.start.mockImplementation(() => Promise.resolve(true));
    ear.micAllowed.mockImplementation(() => Promise.resolve(false));
  });

  it('has no personal / service switch in single mode', async () => {
    show();
    await screen.findByTestId('assistant-answer');
    expect(screen.queryByTestId('assistant-mode-switch')).toBeNull();
  });

  it('offers the switch to an administrator in multi mode and loads the service thread', async () => {
    assistantApi.getAssistant.mockResolvedValue({ data: { ...META, service_available: true } });
    show();
    const service = await screen.findByRole('tab', { name: 'Service' });
    fireEvent.click(service);
    await waitFor(() => expect(assistantApi.getAssistant).toHaveBeenLastCalledWith('service', 'team'));
  });

  it('names no workspace and asks for the voice models of the header one', async () => {
    show();
    await screen.findByTestId('assistant-answer');
    await waitFor(() => expect(assistantApi.getAssistant).toHaveBeenCalledWith('personal', 'team'));
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByText('Workspace')).toBeNull();
  });

  it('sends a turn to the home workspace when the header one is out of reach', async () => {
    header.selectedWorkspace = 'someone-elses';
    assistantApi.streamAssistantTurn.mockImplementation(async ({ body, onEvent }) => {
      expect(body).toMatchObject({ workspace: 'default' });
      onEvent({ type: 'done', run_id: 'r1' });
    });
    try {
      show();
      await screen.findByTestId('assistant-answer');
      fireEvent.change(screen.getByPlaceholderText('Or type here'), { target: { value: 'hi' } });
      fireEvent.click(screen.getByRole('button', { name: 'Send' }));
      await waitFor(() => expect(assistantApi.streamAssistantTurn).toHaveBeenCalled());
    } finally {
      header.selectedWorkspace = 'team';
    }
  });

  it('sends a typed turn to the header workspace and shows the answer large', async () => {
    assistantApi.streamAssistantTurn.mockImplementation(async ({ body, onEvent }) => {
      expect(body).toMatchObject({ message: 'what is new?', workspace: 'team', mode: 'personal', voice: false });
      onEvent({ type: 'run', run_id: 'r1' });
      onEvent({ type: 'token', token: 'Two tasks wait. ' });
      onEvent({ type: 'message', content: 'Two tasks wait.\n\n[Open them](/tasks)', run_id: 'r1' });
      onEvent({ type: 'done', run_id: 'r1' });
    });
    show();
    await screen.findByTestId('assistant-answer');
    fireEvent.change(screen.getByPlaceholderText('Or type here'), { target: { value: 'what is new?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByTestId('assistant-answer')).toHaveTextContent('Two tasks wait.'));
    // A typed turn is not read aloud unless the page is in voice only mode.
    expect(assistantApi.speakAssistant).not.toHaveBeenCalled();
  });

  it('the voice settings close on a click outside them or on Escape', async () => {
    show();
    const toggle = await screen.findByRole('button', { name: 'Voice settings' });
    fireEvent.click(toggle);
    const panel = screen.getByTestId('assistant-settings');
    // A click inside keeps the panel open.
    fireEvent.mouseDown(panel);
    expect(screen.getByTestId('assistant-settings')).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByTestId('assistant-answer'));
    expect(screen.queryByTestId('assistant-settings')).toBeNull();
    fireEvent.click(toggle);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('assistant-settings')).toBeNull();
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
    expect(assistantApi.transcribeRecording).toHaveBeenCalledWith(expect.any(Blob), { mode: 'personal', workspace: 'team', language: 'en' });
    fireEvent.keyDown(field, { key: 'Enter' });
    await waitFor(() => expect(assistantApi.speakAssistant).toHaveBeenCalledTimes(2));
    expect(assistantApi.speakAssistant.mock.calls.map(([b]) => [b.run_id, b.text])).toEqual([
      ['r9', 'Two tasks wait for you.'], ['r9', 'Both are due today.'],
    ]);
  });

  it('the talk button turns into stop while a turn runs, and back after it', async () => {
    assistantApi.getAssistant.mockResolvedValue({
      data: { ...META, voice: { max_seconds: 120, transcription: { provider: 'openai', model: 'stt' }, speech: null } },
    });
    let releaseTurn;
    assistantApi.streamAssistantTurn.mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'run', run_id: 'r1' });
      await new Promise((r) => { releaseTurn = r; });
    });
    show();
    fireEvent.change(await screen.findByPlaceholderText('Or type here'), { target: { value: 'run the team' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    const stop = await screen.findByRole('button', { name: 'Stop' });
    expect(screen.queryByRole('button', { name: 'Hold to talk' })).toBeNull();
    fireEvent.click(stop);
    await waitFor(() => expect(assistantApi.stopAssistant).toHaveBeenCalledWith('personal'));
    releaseTurn();
    expect(await screen.findByRole('button', { name: 'Hold to talk' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull();
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

  // ── hands-free ─────────────────────────────────────────────────────────────

  const STT_META = { ...META, voice: { max_seconds: 120, transcription: { provider: 'openai', model: 'stt' }, speech: null } };
  const heard = async (text) => {
    assistantApi.transcribeRecording.mockResolvedValueOnce({ text, consent: null });
    await act(async () => { ear.opts.onSegment({ blob: new Blob(['x'], { type: 'audio/wav' }), seconds: 1 }); });
  };

  it('a conversation listens after every answer until it is ended by voice', async () => {
    assistantApi.getAssistant.mockResolvedValue({ data: STT_META });
    assistantApi.streamAssistantTurn.mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'run', run_id: 'r1' });
      onEvent({ type: 'message', content: 'Two tasks.', run_id: 'r1' });
    });
    show();
    fireEvent.click(await screen.findByRole('radio', { name: 'Conversation' }));
    expect(ear.start).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Start a conversation' }));
    await waitFor(() => expect(ear.start).toHaveBeenCalled());
    expect(JSON.parse(localStorage.getItem(PREFS_KEY)).listen).toBe('conversation');

    await heard('what is new?');
    await waitFor(() => expect(assistantApi.streamAssistantTurn).toHaveBeenCalledTimes(1));
    expect(assistantApi.streamAssistantTurn.mock.calls[0][0].body).toMatchObject({ message: 'what is new?', voice: true });
    expect(assistantApi.transcribeRecording).toHaveBeenCalledWith(expect.any(Blob), expect.objectContaining({ purpose: 'command' }));
    await screen.findByRole('button', { name: 'End the conversation' });

    // Still listening after the answer: the next thing said is the next turn.
    await heard('and tomorrow?');
    await waitFor(() => expect(assistantApi.streamAssistantTurn).toHaveBeenCalledTimes(2));

    await heard('Goodbye.');
    expect(await screen.findByRole('button', { name: 'Start a conversation' })).toBeInTheDocument();
    expect(assistantApi.streamAssistantTurn).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(ear.stop).toHaveBeenCalled());
  });

  it('"stop" said while a turn runs stops it, even in hold mode once the microphone is allowed', async () => {
    ear.micAllowed.mockImplementation(() => Promise.resolve(true));
    assistantApi.getAssistant.mockResolvedValue({ data: STT_META });
    let releaseTurn;
    assistantApi.streamAssistantTurn.mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'run', run_id: 'r1' });
      await new Promise((r) => { releaseTurn = r; });
    });
    show();
    fireEvent.change(await screen.findByPlaceholderText('Or type here'), { target: { value: 'run the team' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByText('Tap or say "stop"');
    await waitFor(() => expect(ear.start).toHaveBeenCalled());
    // Something longer is not a stop: the turn goes on.
    await heard('stop the nightly job instead');
    expect(assistantApi.stopAssistant).not.toHaveBeenCalled();
    await heard('Стоп!');
    await waitFor(() => expect(assistantApi.stopAssistant).toHaveBeenCalledWith('personal'));
    expect(screen.getByText('Stopped by voice.')).toBeInTheDocument();
    expect(assistantApi.transcribeRecording).toHaveBeenLastCalledWith(expect.any(Blob), expect.objectContaining({ purpose: 'monitor' }));
    releaseTurn();
  });

  it('the browser\'s interim words stop a turn before the sentence settles', async () => {
    ear.micAllowed.mockImplementation(() => Promise.resolve(true));
    let releaseTurn;
    assistantApi.streamAssistantTurn.mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'run', run_id: 'r1' });
      await new Promise((r) => { releaseTurn = r; });
    });
    assistantApi.getAssistant.mockResolvedValue({ data: STT_META });
    show();
    fireEvent.change(await screen.findByPlaceholderText('Or type here'), { target: { value: 'go' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(ear.start).toHaveBeenCalled());
    act(() => { ear.opts.onInterim('hör auf'); });
    await waitFor(() => expect(assistantApi.stopAssistant).toHaveBeenCalledTimes(1));
    // The same words settling a moment later are not a second stop.
    act(() => { ear.opts.onUtterance('hör auf'); });
    expect(assistantApi.stopAssistant).toHaveBeenCalledTimes(1);
    releaseTurn();
  });

  it('in wake mode only what follows the name is a turn', async () => {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ listen: 'wake' }));
    assistantApi.getAssistant.mockResolvedValue({ data: STT_META });
    assistantApi.streamAssistantTurn.mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'message', content: 'Nothing failed.' });
    });
    show();
    await screen.findByText('Say "assistant", or tap');
    await waitFor(() => expect(ear.start).toHaveBeenCalled());
    await heard('what failed today?');
    expect(assistantApi.transcribeRecording).toHaveBeenLastCalledWith(expect.any(Blob), expect.objectContaining({ purpose: 'wake' }));
    expect(assistantApi.streamAssistantTurn).not.toHaveBeenCalled();
    await heard('Assistant, what failed today?');
    await waitFor(() => expect(assistantApi.streamAssistantTurn).toHaveBeenCalledTimes(1));
    expect(assistantApi.streamAssistantTurn.mock.calls[0][0].body).toMatchObject({ message: 'what failed today?', voice: true });
  });

  it('the name alone wakes it, and the next thing said is the turn', async () => {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ listen: 'wake', wakePhrase: 'Привет хаб' }));
    assistantApi.getAssistant.mockResolvedValue({ data: STT_META });
    assistantApi.streamAssistantTurn.mockImplementation(async () => {});
    show();
    await screen.findByText('Say "Привет хаб", or tap');
    await waitFor(() => expect(ear.start).toHaveBeenCalled());
    await heard('Привет, хаб!');
    await waitFor(() => expect(screen.getByTestId('assistant-hint')).toHaveTextContent('Listening'));
    await heard('покажи расходы');
    await waitFor(() => expect(assistantApi.streamAssistantTurn).toHaveBeenCalledTimes(1));
    expect(assistantApi.streamAssistantTurn.mock.calls[0][0].body.message).toBe('покажи расходы');
  });

  it('a request said to another page arrives as this page\'s first turn', async () => {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ listen: 'wake' }));
    assistantApi.getAssistant.mockResolvedValue({ data: STT_META });
    assistantApi.streamAssistantTurn.mockImplementation(async () => {});
    render(
      <MemoryRouter initialEntries={[{ pathname: '/assistant', state: { wake: { command: 'show the costs' } } }]}>
        <I18nProvider><Assistant /></I18nProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(assistantApi.streamAssistantTurn).toHaveBeenCalledTimes(1));
    expect(assistantApi.streamAssistantTurn.mock.calls[0][0].body).toMatchObject({ message: 'show the costs', voice: true });
  });
});
