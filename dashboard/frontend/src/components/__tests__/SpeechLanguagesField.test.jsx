import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import { I18nProvider } from '../../i18n';

// The speech model's voice per language survives the form: shown, edited,
// sent back on save, and dropped when the provider changes under it.

const ok = (data) => Promise.resolve({ data });
const getWorkspaceSpecialModels = vi.fn();
const updateWorkspaceSpecialModels = vi.fn();

vi.mock('../../api', () => ({
  getWorkspaceSpecialModels: (...a) => getWorkspaceSpecialModels(...a),
  updateWorkspaceSpecialModels: (...a) => updateWorkspaceSpecialModels(...a),
  discoverWorkspaceSpecialModels: vi.fn(() => new Promise(() => {})),
  checkWorkspaceSpecialModel: vi.fn(),
  getWorkspaceSpecialModelVoices: vi.fn(() => new Promise(() => {})),
  sampleWorkspaceSpecialModel: vi.fn(),
}));

import WorkspaceSpecialModels from '../workspace/WorkspaceSpecialModels';

const options = {
  purposes: [
    { id: 'speech', tool: 'synthesize_speech', unit: '1k_chars', summary: '', kinds: ['openai_compat'],
      suggestions: {}, options: { voice: 'a voice' }, voices: { openai_compat: ['alloy', 'nova'] } },
  ],
  custom: { tool: 'ask_special_model', kinds: ['chat', 'http'] },
  providers: [
    { id: 'openai', label: 'OpenAI', kind: 'openai_compat' },
    { id: 'my-llm', label: 'My server', kind: 'openai_compat' },
  ],
};
const own = {
  speech: { provider: 'openai', model: 'gpt-4o-mini-tts', options: {},
    languages: { ru: { model: 'tts-1-hd' } } },
};

const renderForm = () => render(
  <MemoryRouter><I18nProvider><WorkspaceSpecialModels workspace="default" /></I18nProvider></MemoryRouter>,
);

describe('a voice per language in the special models form', () => {
  it('is shown, edited and sent back on save', async () => {
    getWorkspaceSpecialModels.mockReturnValue(ok({ workspace: 'default', own, effective: own, options }));
    updateWorkspaceSpecialModels.mockReturnValue(ok({ workspace: 'default', own, effective: own, options }));
    renderForm();
    expect(await screen.findByTestId('special-speech-language-ru-model')).toHaveValue('tts-1-hd');
    fireEvent.change(screen.getByTestId('special-speech-language-de-model'), { target: { value: 'tts-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save models' }));
    await waitFor(() => expect(updateWorkspaceSpecialModels).toHaveBeenCalled());
    expect(updateWorkspaceSpecialModels.mock.calls[0][1].speech.languages).toEqual({
      ru: { model: 'tts-1-hd' }, de: { model: 'tts-1' },
    });
  });

  it('is dropped when the provider changes under it', async () => {
    updateWorkspaceSpecialModels.mockReset();
    getWorkspaceSpecialModels.mockReturnValue(ok({ workspace: 'default', own, effective: own, options }));
    updateWorkspaceSpecialModels.mockReturnValue(ok({ workspace: 'default', own, effective: own, options }));
    renderForm();
    await screen.findByTestId('special-speech-language-ru-model');
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'my-llm' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save models' }));
    await waitFor(() => expect(updateWorkspaceSpecialModels).toHaveBeenCalled());
    expect(updateWorkspaceSpecialModels.mock.calls[0][1].speech.languages).toBeUndefined();
  });
});
