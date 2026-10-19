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
  connectFirstModel: vi.fn(),
}));
vi.mock('../../../api/firstRun', () => firstRunApi);
const settingsApi = vi.hoisted(() => ({ updateSettings: vi.fn() }));
vi.mock('../../../api/models', () => settingsApi);
const demoApi = vi.hoisted(() => ({ setDemo: vi.fn() }));
vi.mock('../../../api/demo', () => demoApi);
const wsApi = vi.hoisted(() => ({ getWorkspacePersonalMemory: vi.fn(), updateWorkspacePersonalMemory: vi.fn() }));
vi.mock('../../../api/workspaces', () => wsApi);

const EMPTY = {
  providers: [], default_model: { provider: 'openai', model: '', usable: false },
  demo: false,
};
const CONNECTED = {
  ...EMPTY,
  providers: ['openai'],
  default_model: { provider: 'openai', model: 'gpt-5.5', usable: true },
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
    Object.values({ ...firstRunApi, ...settingsApi, ...demoApi, ...wsApi })
      .forEach((fn) => fn.mockReset());
    firstRunApi.firstRunAction.mockResolvedValue({ data: {} });
    firstRunApi.getFirstRunContext.mockResolvedValue({ data: EMPTY });
    firstRunApi.getFirstRunOptions.mockResolvedValue({ data: OPTIONS });
    firstRunApi.firstRunOp.mockResolvedValue({ data: { ok: true } });
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
    firstRunApi.connectFirstModel.mockResolvedValue({ data: { ok: true } });
    firstRunApi.getFirstRunContext.mockResolvedValue({ data: CONNECTED });
    fireEvent.click(screen.getByTestId('first-run-connect'));
    await waitFor(() => expect(firstRunApi.connectFirstModel).toHaveBeenCalledWith({ provider: 'openai', api_key: 'sk-test', base_url: '' }));

    // Straight on to the choice of model, which the new provider made possible.
    await screen.findByTestId('first-run-choose');
    await screen.findByTestId('first-run-tier-balanced');
    expect(screen.getByTestId('first-run-tier-balanced')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByTestId('first-run-tier-fast'));
    cont();
    await waitFor(() => expect(firstRunApi.firstRunOp).toHaveBeenCalledWith('choose_model', { provider: 'openai', model: 'gpt-5.4-mini' }));

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
    await waitFor(() => expect(onFinish).toHaveBeenCalledWith('chat'));
    expect(firstRunApi.firstRunAction).toHaveBeenCalledWith('finish');
  });

  it('a saved step past the model screen with no model goes back to it', async () => {
    renderRun({ initial: { step: 'memory' } });
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
    firstRunApi.connectFirstModel.mockRejectedValue({ response: { status: 400, data: { detail: { code: 'rejected', message: 'nope' } } } });
    fireEvent.click(screen.getByTestId('first-run-connect'));
    expect(await screen.findByRole('alert')).toHaveTextContent('The provider did not accept it');
    expect(screen.getByTestId('first-run-model')).toBeTruthy();
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

  describe('screens already set up', () => {
    const SET = {
      ...CONNECTED,
      configured: {
        model: { provider: 'anthropic', model: 'claude-sonnet-5' },
        demo: { enabled: false },
      },
    };
    const open = async (step, ctx = SET) => {
      firstRunApi.getFirstRunContext.mockResolvedValue({ data: ctx });
      renderRun({ initial: { step } });
    };

    it('shows the chosen model with Continue and Change, and Continue moves on', async () => {
      await open('choose_model');
      expect(await screen.findByTestId('first-run-choose-configured')).toHaveTextContent('Already set up: Anthropic · claude-sonnet-5');
      expect(screen.getByTestId('first-run-change')).toBeTruthy();
      expect(screen.queryByTestId('first-run-tier-balanced')).toBeNull();
      cont();
      await screen.findByTestId('first-run-memory');
      expect(firstRunApi.firstRunOp).not.toHaveBeenCalled();
    });

    it('Change opens the normal form of the model choice', async () => {
      await open('choose_model');
      await screen.findByTestId('first-run-choose-configured');
      fireEvent.click(screen.getByTestId('first-run-change'));
      expect(await screen.findByTestId('first-run-tier-balanced')).toBeTruthy();
      expect(screen.queryByTestId('first-run-choose-configured')).toBeNull();
    });

    it('shows a demo decided off, and Change offers the choice', async () => {
      await open('demo');
      expect(await screen.findByTestId('first-run-demo-configured')).toHaveTextContent('Starting without the demo');
      fireEvent.click(screen.getByTestId('first-run-change'));
      expect(await screen.findByTestId('first-run-demo-add')).toBeTruthy();
    });

    it('asks as usual where nothing is set', async () => {
      await open('demo', { ...CONNECTED, configured: {} });
      expect(await screen.findByTestId('first-run-demo-add')).toBeTruthy();
      expect(screen.queryByTestId('first-run-change')).toBeNull();
    });

    it('does not call a key connected during this very run "already set up"', async () => {
      firstRunApi.getFirstRunContext.mockResolvedValue({ data: { ...EMPTY, configured: {} } });
      renderRun({ initial: { step: 'model' } });
      fireEvent.click(await screen.findByTestId('first-run-way-cloud'));
      fireEvent.change(screen.getByTestId('first-run-key'), { target: { value: 'sk-test' } });
      firstRunApi.connectFirstModel.mockResolvedValue({ data: { ok: true } });
      firstRunApi.getFirstRunContext.mockResolvedValue({ data: { ...SET } });
      fireEvent.click(screen.getByTestId('first-run-connect'));
      expect(await screen.findByTestId('first-run-tier-balanced')).toBeTruthy();
      expect(screen.queryByTestId('first-run-choose-configured')).toBeNull();
    });
  });
});
