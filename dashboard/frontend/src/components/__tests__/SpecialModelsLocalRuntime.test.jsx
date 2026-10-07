import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

import { I18nProvider } from '../../i18n';

// A speech model in the hub's own runtime (provider hub-local, marked
// local_runtime): the form offers the runtime's models and each model's own
// voices, never the cloud provider's suggestions or voice names.

const ok = (data) => Promise.resolve({ data });

const getWorkspaceSpecialModels = vi.fn();
const discoverWorkspaceSpecialModels = vi.fn();
const getWorkspaceSpecialModelVoices = vi.fn();
const sampleWorkspaceSpecialModel = vi.fn();

vi.mock('../../api', () => ({
  getWorkspaceSpecialModels: (...a) => getWorkspaceSpecialModels(...a),
  updateWorkspaceSpecialModels: vi.fn(),
  discoverWorkspaceSpecialModels: (...a) => discoverWorkspaceSpecialModels(...a),
  checkWorkspaceSpecialModel: vi.fn(),
  getWorkspaceSpecialModelVoices: (...a) => getWorkspaceSpecialModelVoices(...a),
  sampleWorkspaceSpecialModel: (...a) => sampleWorkspaceSpecialModel(...a),
}));

import WorkspaceSpecialModels from '../workspace/WorkspaceSpecialModels';

const options = {
  purposes: [
    { id: 'speech', tool: 'synthesize_speech', unit: '1k_chars', summary: '', kinds: ['openai_compat'],
      suggestions: { openai_compat: ['gpt-4o-mini-tts'] }, options: { voice: 'a voice' },
      voices: { openai_compat: ['alloy', 'nova'] } },
  ],
  custom: { tool: 'ask_special_model', kinds: ['chat', 'http'] },
  providers: [
    { id: 'openai', label: 'OpenAI', kind: 'openai_compat' },
    { id: 'hub-local', label: 'Hub runtime', kind: 'openai_compat', local_runtime: true },
  ],
};

// The voices the picker offers, without its "model default" entry.
const voicesOf = (container) => [...(container.querySelector('select#special-speech-voice')?.querySelectorAll('option') || [])]
  .map((o) => o.value).filter(Boolean);

const renderForm = () => render(
  <MemoryRouter><I18nProvider><WorkspaceSpecialModels workspace="alpha" /></I18nProvider></MemoryRouter>,
);

describe('WorkspaceSpecialModels with the hub runtime', () => {
  it('offers the runtime model voices instead of the cloud ones', async () => {
    getWorkspaceSpecialModels.mockReturnValue(ok({ workspace: 'alpha', own: {}, effective: {}, options }));
    // The model's own answer is held back: the search's voices show meanwhile.
    getWorkspaceSpecialModelVoices.mockReturnValue(new Promise(() => {}));
    discoverWorkspaceSpecialModels.mockReturnValue(ok({
      purpose: 'speech', provider: 'hub-local', models: ['kokoro-v1.0', 'piper-ru_RU-irina-medium'], total: 4,
      voices: { 'kokoro-v1.0': ['af_heart', 'bf_emma'], 'piper-ru_RU-irina-medium': [] },
    }));
    const { container } = render(
      <MemoryRouter><I18nProvider><WorkspaceSpecialModels workspace="alpha" /></I18nProvider></MemoryRouter>,
    );
    await screen.findByTestId('special-speech-missing');

    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } });
    expect(voicesOf(container)).toEqual(['alloy', 'nova']);

    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'hub-local' } });
    expect(voicesOf(container)).toEqual([]);
    expect(container.textContent).not.toContain('gpt-4o-mini-tts');

    fireEvent.click(screen.getByTestId('special-speech-discover'));
    await waitFor(() => expect(discoverWorkspaceSpecialModels).toHaveBeenCalledWith('alpha', 'speech', 'hub-local'));
    const found = await screen.findByTestId('special-speech-found');
    fireEvent.click(within(found).getByRole('button', { name: 'kokoro-v1.0' }));
    expect(voicesOf(container)).toEqual(['af_heart', 'bf_emma']);
    fireEvent.click(within(found).getByRole('button', { name: 'piper-ru_RU-irina-medium' }));
    expect(voicesOf(container)).toEqual([]);
  });

  it('loads the voices of a model as soon as it is picked, with their languages', async () => {
    getWorkspaceSpecialModels.mockReturnValue(ok({ workspace: 'alpha', own: {}, effective: {}, options }));
    getWorkspaceSpecialModelVoices.mockImplementation((ws, provider, model) => ok(model === 'kokoro-v1.0'
      ? { voices: ['af_heart', 'ff_siwis'], own: true, language: null, languages: { af_heart: 'en', ff_siwis: 'fr' } }
      : { voices: [], own: true, language: 'ru', languages: {} }));
    const { container } = renderForm();
    await screen.findByTestId('special-speech-missing');

    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'hub-local' } });
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'kokoro-v1.0' } });
    await waitFor(() => expect(voicesOf(container)).toEqual(['af_heart', 'ff_siwis']));
    expect(getWorkspaceSpecialModelVoices).toHaveBeenCalledWith('alpha', 'hub-local', 'kokoro-v1.0');
    expect(discoverWorkspaceSpecialModels).not.toHaveBeenCalled();
    expect(screen.getByRole('option', { name: 'ff_siwis (fr)' })).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'piper-ru_RU-irina-medium' } });
    await waitFor(() => expect(screen.getByTestId('special-speech-voice').placeholder).toBe('This model has one voice'));
  });

  it('drops the voice picked for one model when another model is picked', async () => {
    getWorkspaceSpecialModels.mockReturnValue(ok({ workspace: 'alpha', own: {}, effective: {}, options }));
    getWorkspaceSpecialModelVoices.mockImplementation((ws, provider, model) => ok(model === 'kokoro-v1.0'
      ? { voices: ['af_heart', 'ff_siwis'], own: true, language: null, languages: {} }
      : { voices: ['expr-voice-2-f', 'expr-voice-3-m'], own: true, language: 'en', languages: {} }));
    const { container } = renderForm();
    await screen.findByTestId('special-speech-missing');

    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'hub-local' } });
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'kokoro-v1.0' } });
    await waitFor(() => expect(voicesOf(container)).toEqual(['af_heart', 'ff_siwis']));
    fireEvent.change(screen.getByTestId('special-speech-voice'), { target: { value: 'ff_siwis' } });
    expect(screen.getByTestId('special-speech-voice').value).toBe('ff_siwis');

    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'kitten-tts-nano' } });
    await waitFor(() => expect(voicesOf(container)).toEqual(['expr-voice-2-f', 'expr-voice-3-m']));
    expect(screen.getByTestId('special-speech-voice').value).toBe('');
  });

  it('does not offer a saved voice the model no longer has', async () => {
    getWorkspaceSpecialModels.mockReturnValue(ok({
      workspace: 'alpha', effective: {}, options,
      own: { speech: { provider: 'hub-local', model: 'kokoro-v1.0', options: { voice: 'gone_voice' } } },
    }));
    getWorkspaceSpecialModelVoices.mockReturnValue(ok({ voices: ['af_heart'], own: true, language: null, languages: {} }));
    const { container } = renderForm();
    await waitFor(() => expect(getWorkspaceSpecialModelVoices).toHaveBeenCalled());
    await waitFor(() => expect(voicesOf(container)).toEqual(['af_heart']));
    expect(screen.getByTestId('special-speech-voice').value).toBe('');
  });

  it('plays a sample of the chosen voice and shows the line it read', async () => {
    getWorkspaceSpecialModels.mockReturnValue(ok({ workspace: 'alpha', own: {}, effective: {}, options }));
    getWorkspaceSpecialModelVoices.mockReturnValue(ok({
      voices: ['af_heart', 'ff_siwis'], own: true, language: null, languages: { af_heart: 'en', ff_siwis: 'fr' } }));
    sampleWorkspaceSpecialModel.mockResolvedValue({ blob: new Blob(['x']), language: 'fr', text: 'Bonjour !' });
    const play = vi.fn(() => Promise.resolve());
    const pause = vi.fn();
    vi.stubGlobal('Audio', vi.fn(function Audio() { this.play = play; this.pause = pause; }));
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();

    renderForm();
    await screen.findByTestId('special-speech-missing');
    expect(screen.queryByTestId('special-speech-sample')).toBeNull();
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'hub-local' } });
    expect(screen.getByTestId('special-speech-sample').disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'kokoro-v1.0' } });
    await waitFor(() => expect(screen.getByRole('option', { name: 'ff_siwis (fr)' })).toBeTruthy());
    fireEvent.change(screen.getByTestId('special-speech-voice'), { target: { value: 'ff_siwis' } });

    fireEvent.click(screen.getByTestId('special-speech-sample'));
    await waitFor(() => expect(play).toHaveBeenCalled());
    expect(sampleWorkspaceSpecialModel).toHaveBeenCalledWith('alpha', {
      provider: 'hub-local', model: 'kokoro-v1.0', options: {}, voice: 'ff_siwis', language: 'en',
    }, expect.objectContaining({ signal: expect.anything() }));
    expect(screen.getByTestId('special-speech-sample-text').textContent).toContain('Bonjour !');
    expect(screen.getByTestId('special-speech-sample').getAttribute('aria-label')).toBe('Stop');

    fireEvent.click(screen.getByTestId('special-speech-sample'));
    expect(pause).toHaveBeenCalled();
    expect(screen.getByTestId('special-speech-sample').getAttribute('aria-label')).toBe('Listen');
    vi.unstubAllGlobals();
  });
});
