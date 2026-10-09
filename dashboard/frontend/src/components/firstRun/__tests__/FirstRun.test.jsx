import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../../i18n';
import FirstRun from '../FirstRun';

const firstRunApi = vi.hoisted(() => ({
  firstRunAction: vi.fn(),
  getFirstRunContext: vi.fn(),
  getFirstRunOptions: vi.fn(),
  firstRunOp: vi.fn(),
}));
vi.mock('../../../api/firstRun', () => firstRunApi);
const setupApi = vi.hoisted(() => ({ connectFirstModel: vi.fn(), getSetupGuide: vi.fn(), setupGuideAction: vi.fn() }));
vi.mock('../../../api/setupGuide', () => setupApi);
const localApi = vi.hoisted(() => ({ getReadySet: vi.fn(), startReadySet: vi.fn() }));
vi.mock('../../../api/localModels', () => localApi);
const settingsApi = vi.hoisted(() => ({ updateSettings: vi.fn() }));
vi.mock('../../../api/models', () => settingsApi);
const demoApi = vi.hoisted(() => ({ setDemo: vi.fn() }));
vi.mock('../../../api/demo', () => demoApi);
const wsApi = vi.hoisted(() => ({ getWorkspacePersonalMemory: vi.fn(), updateWorkspacePersonalMemory: vi.fn() }));
vi.mock('../../../api/workspaces', () => wsApi);
vi.mock('../../liveMark/LiveMark', () => ({ default: () => null }));
const assistantApi = vi.hoisted(() => ({
  getAssistant: vi.fn(), stopAssistant: vi.fn(), streamAssistantTurn: vi.fn(), transcribeRecording: vi.fn(),
  sampleAssistantVoice: vi.fn(),
}));
vi.mock('../../../api/assistant', () => assistantApi);
const speaker = vi.hoisted(() => ({ say: vi.fn(), cancel: vi.fn(), unlock: vi.fn(), speaking: false, browser: false }));
vi.mock('../../assistant/useSpeaker', () => ({ default: () => speaker, browserSpeechAvailable: () => false }));
const recorder = vi.hoisted(() => ({
  start: vi.fn(), stop: vi.fn(), cancel: vi.fn(), recording: false, level: 0, error: '', supported: true,
}));
vi.mock('../../assistant/useRecorder', () => ({ default: () => recorder }));

const EMPTY = {
  providers: [], default_model: { provider: 'openai', model: '', usable: false }, runtime: false,
  local_set: null, search: { provider: '', source: 'none', key_set: false }, demo: false, voice: false,
};
const CONNECTED = {
  ...EMPTY,
  providers: ['openai'],
  default_model: { provider: 'openai', model: 'gpt-5.5', usable: true },
  search: { provider: 'openai', source: 'model_key', key_set: true },
};
const OPTIONS = {
  models: {
    openai: {
      listed: true,
      tiers: {
        fast: { model: 'gpt-5.4-mini' }, balanced: { model: 'gpt-5.5' }, strong: { model: 'gpt-5.5-pro' },
      },
    },
  },
  voice: {
    cloud: { openai: { connected: true, voices: ['alloy', 'nova'] }, google: { connected: false, voices: [] } },
    local: { available: false, speech: [] },
  },
};

const renderRun = (props = {}) => render(
  <I18nProvider>
    <MemoryRouter><FirstRun initial={{ step: 'hello' }} onFinish={vi.fn()} {...props} /></MemoryRouter>
  </I18nProvider>,
);

const cont = () => fireEvent.click(screen.getByTestId('first-run-continue'));

describe('FirstRun', () => {
  beforeEach(() => {
    localStorage.clear();
    Object.values({ ...firstRunApi, ...setupApi, ...localApi, ...settingsApi, ...demoApi, ...wsApi, ...assistantApi })
      .forEach((fn) => fn.mockReset());
    [speaker.say, speaker.cancel, recorder.start, recorder.stop].forEach((fn) => fn.mockReset());
    setupApi.getSetupGuide.mockResolvedValue({ data: { work: null } });
    assistantApi.getAssistant.mockResolvedValue({ data: { voice: { speech: null, transcription: null } } });
    assistantApi.stopAssistant.mockResolvedValue({});
    firstRunApi.firstRunAction.mockResolvedValue({ data: {} });
    firstRunApi.getFirstRunContext.mockResolvedValue({ data: EMPTY });
    firstRunApi.getFirstRunOptions.mockResolvedValue({ data: OPTIONS });
    firstRunApi.firstRunOp.mockResolvedValue({ data: { ok: true } });
    localApi.getReadySet.mockResolvedValue({ data: { available: false } });
    wsApi.getWorkspacePersonalMemory.mockResolvedValue({ data: { enabled: true } });
  });

  it('walks every screen from hello to the last one, connecting a key on the way', async () => {
    const onFinish = vi.fn();
    renderRun({ onFinish });

    fireEvent.click(screen.getByTestId('first-run-start'));
    expect(screen.getByTestId('first-run-language')).toBeTruthy();
    expect(screen.queryByTestId('first-run-back')).toBeTruthy();
    // Choosing a language applies at once: the next button is already in it.
    fireEvent.click(screen.getByTestId('first-run-language-ru'));
    expect(screen.getByTestId('first-run-continue')).toHaveTextContent('Продолжить');
    fireEvent.click(screen.getByTestId('first-run-language-en'));
    cont();
    expect(firstRunApi.firstRunAction).toHaveBeenCalledWith('progress', { step: 'appearance', language: 'en' });

    fireEvent.click(screen.getByTestId('first-run-look-dark'));
    cont();

    // The model screen has no way on until a model is connected.
    await screen.findByTestId('first-run-model');
    expect(screen.queryByTestId('first-run-continue')).toBeNull();
    expect(screen.queryByTestId('first-run-later')).toBeNull();
    fireEvent.click(screen.getByTestId('first-run-way-cloud'));
    expect(screen.getByTestId('first-run-connect')).toBeDisabled();
    fireEvent.change(screen.getByTestId('first-run-key'), { target: { value: 'sk-test' } });
    setupApi.connectFirstModel.mockResolvedValue({ data: { ok: true } });
    firstRunApi.getFirstRunContext.mockResolvedValue({ data: CONNECTED });
    fireEvent.click(screen.getByTestId('first-run-connect'));
    await waitFor(() => expect(setupApi.connectFirstModel).toHaveBeenCalledWith({ provider: 'openai', api_key: 'sk-test', base_url: '' }));

    // Straight on to the choice of model, which the new provider made possible.
    await screen.findByTestId('first-run-choose');
    await screen.findByTestId('first-run-tier-balanced');
    expect(screen.getByTestId('first-run-tier-balanced')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByTestId('first-run-tier-fast'));
    cont();
    await waitFor(() => expect(firstRunApi.firstRunOp).toHaveBeenCalledWith('choose_model', { provider: 'openai', model: 'gpt-5.4-mini' }));

    await screen.findByTestId('first-run-voice');
    await screen.findByTestId('first-run-voice-cloud-openai');
    expect(screen.queryByTestId('first-run-voice-cloud-google')).toBeNull();
    fireEvent.click(screen.getByTestId('first-run-later'));

    // Search already works through the model's own key.
    await screen.findByTestId('first-run-search-on');
    cont();

    await screen.findByTestId('first-run-memory');
    await waitFor(() => expect(screen.getByTestId('first-run-continue')).not.toBeDisabled());
    fireEvent.click(screen.getByTestId('first-run-memory-off'));
    cont();
    await waitFor(() => expect(wsApi.updateWorkspacePersonalMemory).toHaveBeenCalledWith('default', false));

    await screen.findByTestId('first-run-demo');
    fireEvent.click(screen.getByTestId('first-run-demo-empty'));
    cont();
    expect(demoApi.setDemo).not.toHaveBeenCalled();

    await screen.findByTestId('first-run-done');
    fireEvent.click(screen.getByTestId('first-run-finish'));
    await waitFor(() => expect(onFinish).toHaveBeenCalledWith('chat', { voice: false }));
    expect(firstRunApi.firstRunAction).toHaveBeenCalledWith('finish');
  });

  it('a saved step past the model screen with no model goes back to it', async () => {
    renderRun({ initial: { step: 'voice' } });
    expect(await screen.findByTestId('first-run-model')).toBeTruthy();
  });

  it('resumes where it stopped when a model is there', async () => {
    firstRunApi.getFirstRunContext.mockResolvedValue({ data: CONNECTED });
    renderRun({ initial: { step: 'demo' } });
    expect(await screen.findByTestId('first-run-demo')).toBeTruthy();
    fireEvent.click(screen.getByTestId('first-run-back'));
    expect(await screen.findByTestId('first-run-memory')).toBeTruthy();
  });

  it('an already connected model is shown and can be kept', async () => {
    firstRunApi.getFirstRunContext.mockResolvedValue({ data: CONNECTED });
    renderRun({ initial: { step: 'model' } });
    expect(await screen.findByTestId('first-run-model-connected')).toHaveTextContent('OpenAI');
    cont();
    expect(await screen.findByTestId('first-run-choose')).toBeTruthy();
  });

  it('a rejected key keeps the person on the model screen with the reason', async () => {
    renderRun({ initial: { step: 'model' } });
    fireEvent.click(await screen.findByTestId('first-run-way-cloud'));
    fireEvent.change(screen.getByTestId('first-run-key'), { target: { value: 'bad' } });
    setupApi.connectFirstModel.mockRejectedValue({ response: { status: 400, data: { detail: { code: 'rejected', message: 'nope' } } } });
    fireEvent.click(screen.getByTestId('first-run-connect'));
    expect(await screen.findByRole('alert')).toHaveTextContent('The provider did not accept it');
    expect(screen.getByTestId('first-run-model')).toBeTruthy();
  });

  it('the hub\'s own runtime offers the local download, which counts as the model', async () => {
    firstRunApi.getFirstRunContext.mockResolvedValue({ data: { ...EMPTY, runtime: true } });
    localApi.getReadySet.mockResolvedValue({ data: { available: true, chat: { label: 'Qwen3 8B', size_bytes: 5e9 }, job: null } });
    renderRun({ initial: { step: 'model' } });
    fireEvent.click(await screen.findByTestId('first-run-way-download'));
    localApi.startReadySet.mockResolvedValue({ data: { job: { id: 'j1', status: 'running' } } });
    localApi.getReadySet.mockResolvedValue({ data: { available: true, chat: { label: 'Qwen3 8B', size_bytes: 5e9 }, job: { status: 'running', percent: 10 } } });
    firstRunApi.getFirstRunContext.mockResolvedValue({ data: { ...EMPTY, runtime: true, local_set: { status: 'running' } } });
    fireEvent.click(screen.getByTestId('first-run-download'));
    await waitFor(() => expect(localApi.startReadySet).toHaveBeenCalledWith('default'));
    // The way on appears once the download is seen running.
    fireEvent.click(await screen.findByTestId('first-run-continue'));
    // No provider with tiers: the model choice is skipped; the voice comes with the set,
    // so the voice screen opens on its check, which waits for the download.
    expect(await screen.findByTestId('first-run-voice-check-step')).toBeTruthy();
    expect(screen.getByTestId('first-run-voice-downloading')).toBeTruthy();
    expect(screen.getByTestId('first-run-greet')).toBeDisabled();
  });

  it('a model server on this computer offers each model it serves', async () => {
    firstRunApi.getFirstRunContext.mockResolvedValue({ data: {
      ...EMPTY, providers: ['ollama'], default_model: { provider: 'ollama', model: 'llama3.2:1b', usable: true },
    } });
    firstRunApi.getFirstRunOptions.mockResolvedValue({ data: {
      models: { ollama: { listed: true, tiers: { balanced: { model: 'llama3.2:1b' } }, served: ['llama3.2:1b', 'qwen3:14b'] } },
    } });
    renderRun({ initial: { step: 'choose_model' } });
    expect(await screen.findByTestId('first-run-tier-llama3.2:1b')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByText('Recommended')).toBeNull();
    fireEvent.click(screen.getByTestId('first-run-tier-qwen3:14b'));
    cont();
    await waitFor(() => expect(firstRunApi.firstRunOp).toHaveBeenCalledWith('choose_model', { provider: 'ollama', model: 'qwen3:14b' }));
  });

  it('a cloud voice gets a voice for each language, then the check plays each one', async () => {
    firstRunApi.getFirstRunContext.mockResolvedValue({ data: CONNECTED });
    renderRun({ initial: { step: 'voice' } });
    fireEvent.click(await screen.findByTestId('first-run-voice-cloud-openai'));
    // A distinct voice per language by default; the person changes Russian.
    expect(screen.getByTestId('first-run-voice-openai-ru')).toHaveValue('alloy');
    fireEvent.change(screen.getByTestId('first-run-voice-openai-de'), { target: { value: 'nova' } });
    assistantApi.getAssistant.mockResolvedValue({ data: { voice: {
      speech: { provider: 'openai', model: 'gpt-4o-mini-tts', voice: 'nova', by_language: { ru: { voice: 'alloy' } } },
      transcription: { provider: 'openai', model: 'gpt-4o-mini-transcribe' },
    } } });
    firstRunApi.getFirstRunContext.mockResolvedValue({ data: { ...CONNECTED, voice: true } });
    cont();
    await waitFor(() => expect(firstRunApi.firstRunOp).toHaveBeenCalledWith('voice_cloud', {
      provider: 'openai', voice: 'nova', voices: { en: 'nova', ru: 'alloy', de: 'nova' },
    }));
    expect(await screen.findByTestId('first-run-voice-check-step')).toBeTruthy();
    expect(await screen.findByTestId('first-run-voice-of-ru')).toHaveTextContent('alloy');
    expect(screen.getByTestId('first-run-voice-of-en')).toHaveTextContent('nova');
  });

  it('the check: hello out loud, a yes, an answer by voice, then the assistant leads on', async () => {
    const onFinish = vi.fn();
    firstRunApi.getFirstRunContext.mockResolvedValue({ data: { ...CONNECTED, voice: true, demo: true } });
    assistantApi.getAssistant.mockResolvedValue({ data: { voice: {
      speech: { provider: 'openai', model: 'gpt-4o-mini-tts', voice: 'nova', by_language: {} },
      transcription: { provider: 'openai', model: 'gpt-4o-mini-transcribe' },
    } } });
    assistantApi.streamAssistantTurn.mockImplementation(async ({ body, onEvent }) => {
      onEvent({ type: 'run', run_id: 'r1' });
      onEvent({ type: 'token', token: 'Hi! I am your assistant. ' });
      onEvent({ type: 'message', content: `Hi! I am your assistant. You said: ${body.message}` });
    });
    renderRun({ initial: { step: 'voice' }, onFinish });
    fireEvent.click(await screen.findByTestId('first-run-greet'));
    await waitFor(() => expect(assistantApi.streamAssistantTurn).toHaveBeenCalled());
    expect(assistantApi.streamAssistantTurn.mock.calls[0][0].body).toMatchObject({ voice: false, first_run_screen: 'voice' });
    expect(speaker.say).toHaveBeenCalledWith({ run_id: 'r1', text: 'Hi! I am your assistant.' });
    expect(await screen.findByTestId('first-run-check-model-ok')).toBeTruthy();

    fireEvent.click(await screen.findByTestId('first-run-heard-yes'));
    expect(screen.getByTestId('first-run-check-speech-ok')).toBeTruthy();
    fireEvent.click(screen.getByTestId('first-run-talk'));
    await waitFor(() => expect(recorder.start).toHaveBeenCalled());
    recorder.stop.mockResolvedValue(new Blob(['x'], { type: 'audio/wav' }));
    assistantApi.transcribeRecording.mockResolvedValue({ text: 'What can you do?' });
    fireEvent.click(screen.getByTestId('first-run-talk'));
    expect(await screen.findByTestId('first-run-voice-ready')).toBeTruthy();
    expect(screen.getByTestId('first-run-check-hearing-ok')).toBeTruthy();
    expect(assistantApi.streamAssistantTurn.mock.calls[1][0].body).toMatchObject({ message: 'What can you do?', voice: true });

    // From here on the assistant is on every screen, asked about that screen.
    cont();
    await screen.findByTestId('first-run-search');
    fireEvent.change(screen.getByTestId('first-run-assist-input'), { target: { value: 'Do I need this?' } });
    fireEvent.click(screen.getByTestId('first-run-assist-send'));
    await waitFor(() => expect(assistantApi.streamAssistantTurn.mock.calls[2][0].body)
      .toMatchObject({ message: 'Do I need this?', first_run_screen: 'search' }));
    expect(await screen.findByTestId('first-run-assist-answer')).toHaveTextContent('You said: Do I need this?');

    cont();
    await screen.findByTestId('first-run-memory');
    await waitFor(() => expect(screen.getByTestId('first-run-continue')).not.toBeDisabled());
    cont();
    await screen.findByTestId('first-run-demo');
    cont();
    await screen.findByTestId('first-run-done');
    fireEvent.click(screen.getByTestId('first-run-finish-assistant'));
    await waitFor(() => expect(onFinish).toHaveBeenCalledWith('assistant', { voice: true }));
  });
});
