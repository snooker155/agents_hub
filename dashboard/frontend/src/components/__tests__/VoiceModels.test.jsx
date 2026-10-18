import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../i18n';

// The Voice tab of the Models page (providers/voice_models.py): one speech
// and one transcription model for the assistant. A purpose left empty says
// the browser's own is used; the voice per language survives the form; the
// provider can be asked for its models; a model can be checked unsaved.

const ok = (data) => Promise.resolve({ data });
const getVoiceModels = vi.fn();
const updateVoiceModels = vi.fn();
const discoverVoiceModels = vi.fn();
const checkVoiceModel = vi.fn();

vi.mock('../../api', () => ({
  getVoiceModels: (...a) => getVoiceModels(...a),
  updateVoiceModels: (...a) => updateVoiceModels(...a),
  discoverVoiceModels: (...a) => discoverVoiceModels(...a),
  checkVoiceModel: (...a) => checkVoiceModel(...a),
  getVoiceModelVoices: vi.fn(() => new Promise(() => {})),
  sampleVoiceModel: vi.fn(),
}));

import VoiceModels from '../models/VoiceModels';

const options = {
  purposes: [
    { id: 'speech', unit: '1k_chars', kinds: ['openai_compat'],
      suggestions: { openai_compat: ['gpt-4o-mini-tts'] }, options: { voice: 'a voice' },
      voices: { openai_compat: ['alloy', 'nova'] } },
    { id: 'transcription', unit: 'call', kinds: ['openai_compat'],
      suggestions: { openai_compat: ['whisper-1'] }, options: { language: 'ISO code' }, voices: {} },
  ],
  providers: [
    { id: 'openai', label: 'OpenAI', kind: 'openai_compat' },
    { id: 'my-llm', label: 'My server', kind: 'openai_compat' },
  ],
};
const own = {
  speech: { provider: 'openai', model: 'gpt-4o-mini-tts', options: {},
    languages: { ru: { model: 'tts-1-hd' } } },
};

const renderForm = () => render(<MemoryRouter><I18nProvider><VoiceModels /></I18nProvider></MemoryRouter>);

describe('VoiceModels', () => {
  beforeEach(() => vi.clearAllMocks());

  it('says an empty purpose falls back to the browser and saves a model for it', async () => {
    getVoiceModels.mockReturnValue(ok({ own: {}, options }));
    updateVoiceModels.mockImplementation((body) => ok({ own: body, options }));
    renderForm();
    expect((await screen.findByTestId('voice-transcription-missing')).textContent)
      .toMatch(/browser's own speech recognition/);
    fireEvent.change(screen.getByTestId('voice-transcription-provider'), { target: { value: 'openai' } });
    const [, model] = screen.getAllByLabelText('Model');
    fireEvent.change(model, { target: { value: 'whisper-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save voice models' }));
    await waitFor(() => expect(updateVoiceModels).toHaveBeenCalled());
    const body = updateVoiceModels.mock.calls[0][0];
    expect(body.speech).toBeUndefined();
    expect(body.transcription).toMatchObject({ provider: 'openai', model: 'whisper-1' });
  });

  it('keeps a voice per language, sends it back on save', async () => {
    getVoiceModels.mockReturnValue(ok({ own, options }));
    updateVoiceModels.mockReturnValue(ok({ own, options }));
    renderForm();
    expect(await screen.findByTestId('voice-speech-language-ru-model')).toHaveValue('tts-1-hd');
    fireEvent.change(screen.getByTestId('voice-speech-language-de-model'), { target: { value: 'tts-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save voice models' }));
    await waitFor(() => expect(updateVoiceModels).toHaveBeenCalled());
    expect(updateVoiceModels.mock.calls[0][0].speech.languages).toEqual({
      ru: { model: 'tts-1-hd' }, de: { model: 'tts-1' },
    });
  });

  it('drops the voice per language when the provider changes under it', async () => {
    getVoiceModels.mockReturnValue(ok({ own, options }));
    updateVoiceModels.mockReturnValue(ok({ own, options }));
    renderForm();
    await screen.findByTestId('voice-speech-language-ru-model');
    fireEvent.change(screen.getByTestId('voice-speech-provider'), { target: { value: 'my-llm' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save voice models' }));
    await waitFor(() => expect(updateVoiceModels).toHaveBeenCalled());
    expect(updateVoiceModels.mock.calls[0][0].speech.languages).toBeUndefined();
  });

  it('finds the models of the provider that fit the purpose and picks one', async () => {
    getVoiceModels.mockReturnValue(ok({ own: {}, options }));
    discoverVoiceModels.mockReturnValue(ok({
      purpose: 'speech', provider: 'openai', models: ['gpt-4o-mini-tts', 'tts-1'], total: 90,
    }));
    renderForm();
    await screen.findByTestId('voice-speech-missing');
    expect(screen.queryByTestId('voice-speech-discover')).toBeNull();
    fireEvent.change(screen.getByTestId('voice-speech-provider'), { target: { value: 'openai' } });
    fireEvent.click(screen.getByTestId('voice-speech-discover'));
    await waitFor(() => expect(discoverVoiceModels).toHaveBeenCalledWith('speech', 'openai'));
    const found = await screen.findByTestId('voice-speech-found');
    expect(found.textContent).toMatch(/2 of the provider's 90 models fit/);
    fireEvent.click(within(found).getByRole('button', { name: 'tts-1' }));
    const [speechModel] = screen.getAllByLabelText('Model');
    expect(speechModel).toHaveValue('tts-1');
  });

  it('checks the connection of the model the form holds, saved or not', async () => {
    getVoiceModels.mockReturnValue(ok({ own: {}, options }));
    checkVoiceModel.mockReturnValueOnce(ok({ status: 'warn', message: 'not among them' }));
    renderForm();
    await screen.findByTestId('voice-speech-missing');
    fireEvent.change(screen.getByTestId('voice-speech-provider'), { target: { value: 'openai' } });
    expect(screen.queryByTestId('voice-speech-check')).toBeNull();
    const [speechModel] = screen.getAllByLabelText('Model');
    fireEvent.change(speechModel, { target: { value: 'tts-9' } });
    fireEvent.click(screen.getByTestId('voice-speech-check'));
    await waitFor(() => expect(checkVoiceModel).toHaveBeenCalledWith({ purpose: 'speech', provider: 'openai', model: 'tts-9' }));
    expect((await screen.findByTestId('voice-speech-check-result')).textContent).toMatch(/not among them/);
    // Another model: the result was for the first one.
    fireEvent.change(speechModel, { target: { value: 'tts-1' } });
    expect(screen.queryByTestId('voice-speech-check-result')).toBeNull();
  });
});
