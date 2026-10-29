import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../../i18n';
import { AuthContext, DEFAULT_AUTH } from '../../auth';
import FirstRunGate from '../FirstRunGate';

const firstRunApi = vi.hoisted(() => ({
  getFirstRun: vi.fn(),
  firstRunAction: vi.fn(),
  getFirstRunContext: vi.fn(),
  getFirstRunOptions: vi.fn(),
  firstRunOp: vi.fn(),
  getWelcome: vi.fn(),
  welcomeAction: vi.fn(),
  getWelcomeOverview: vi.fn(),
}));
vi.mock('../../../api/firstRun', () => firstRunApi);
vi.mock('../../docs/WelcomeTour', () => ({ useWelcomeTour: () => ({ start: vi.fn() }) }));
vi.mock('../../liveMark/LiveMark', () => ({ default: () => null }));

const renderGate = (auth = {}) => render(
  <I18nProvider>
    <AuthContext.Provider value={{ ...DEFAULT_AUTH, loading: false, ...auth }}>
      <MemoryRouter>
        <FirstRunGate><div data-testid="app">the app</div></FirstRunGate>
      </MemoryRouter>
    </AuthContext.Provider>
  </I18nProvider>,
);

describe('FirstRunGate', () => {
  beforeEach(() => {
    localStorage.clear();
    Object.values(firstRunApi).forEach((fn) => fn.mockReset());
    firstRunApi.getFirstRunContext.mockResolvedValue({ data: {} });
  });

  it('shows the first run in place of the whole app while it is required', async () => {
    firstRunApi.getFirstRun.mockResolvedValue({ data: { applies: true, required: true, step: 'hello' } });
    renderGate();
    expect(await screen.findByTestId('first-run-hello')).toBeTruthy();
    expect(screen.queryByTestId('app')).toBeNull();
  });

  it('lets the app through once it is finished', async () => {
    firstRunApi.getFirstRun.mockResolvedValue({ data: { applies: true, required: false } });
    renderGate();
    expect(await screen.findByTestId('app')).toBeTruthy();
  });

  it('never keeps anyone out of a hub whose backend does not answer', async () => {
    firstRunApi.getFirstRun.mockRejectedValue(new Error('offline'));
    renderGate();
    expect(await screen.findByTestId('app')).toBeTruthy();
  });

  it('is not asked in multi mode: a new account gets the welcome instead', async () => {
    firstRunApi.getWelcome.mockResolvedValue({ data: { applies: true, required: true, step: 'hello' } });
    firstRunApi.getWelcomeOverview.mockResolvedValue({ data: { person: { name: 'Anna' } } });
    renderGate({ mode: 'multi', user: { id: 'u1', display_name: 'Anna' } });
    expect(await screen.findByTestId('welcome-hello')).toBeTruthy();
    expect(screen.getByTestId('mark-reveal')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1 }).getAttribute('aria-label')).toContain('Anna');
    expect(screen.queryByTestId('app')).toBeNull();
    expect(firstRunApi.getFirstRun).not.toHaveBeenCalled();
  });

  it('shows what the administrator set up, read only, and finishes into the app', async () => {
    firstRunApi.getWelcome.mockResolvedValue({ data: { applies: true, required: true, step: 'overview' } });
    firstRunApi.getWelcomeOverview.mockResolvedValue({ data: {
      person: { name: 'Anna', role: 'member' },
      workspaces: [{ name: 'u-anna', display_name: 'Anna', role: 'owner', personal: true },
        { name: 'sales', display_name: 'Sales', role: 'viewer', personal: false }],
      workspace: 'u-anna',
      model: { provider: 'openai', model: 'gpt-5', effort: 'low', policy: { models: [{ provider: 'openai', model: 'gpt-5' }] } },
      voice: { speech: '', transcription: '' },
      search: { provider: 'brave', on: true },
      memory: { enabled: true },
      budget: { limit_usd: 25, source: 'default' },
    } });
    firstRunApi.welcomeAction.mockResolvedValue({ data: { applies: true, required: false } });
    renderGate({ mode: 'multi', user: { id: 'u1' } });
    const settings = await screen.findByTestId('welcome-settings');
    expect(settings.textContent).toContain('OpenAI · gpt-5');
    expect(screen.getByTestId('welcome-workspaces').textContent).toContain('Sales');
    // Nothing the administrator set is a control here: only language and look are.
    expect(settings.querySelectorAll('button, input, select').length).toBe(0);
    fireEvent.click(screen.getByTestId('welcome-finish'));
    expect(await screen.findByTestId('app')).toBeTruthy();
    expect(firstRunApi.welcomeAction).toHaveBeenCalledWith('finish');
  });

  it('opens the welcome in the language the administrator gave the account', async () => {
    firstRunApi.getWelcome.mockResolvedValue({ data: { applies: true, required: true, step: 'hello' } });
    firstRunApi.getWelcomeOverview.mockResolvedValue({ data: { person: { name: 'Anna' } } });
    renderGate({ mode: 'multi', user: { id: 'u1', display_name: 'Anna', language: 'de' } });
    await screen.findByTestId('welcome-hello');
    await waitFor(() => expect(localStorage.getItem('agents_hub_language')).toBe('de'));
    expect(screen.getByRole('heading', { level: 1 }).getAttribute('aria-label')).toBe('Willkommen bei Agents Hub, Anna');
  });

  it('lets an older account in multi mode straight through', async () => {
    firstRunApi.getWelcome.mockResolvedValue({ data: { applies: true, required: false, via: 'upgrade' } });
    renderGate({ mode: 'multi', user: { id: 'u1' } });
    expect(await screen.findByTestId('app')).toBeTruthy();
  });

  it('hands a new browser the language chosen in the first run', async () => {
    firstRunApi.getFirstRun.mockResolvedValue({ data: { applies: true, required: false, language: 'de' } });
    renderGate();
    await screen.findByTestId('app');
    await waitFor(() => expect(localStorage.getItem('agents_hub_language')).toBe('de'));
  });

  it('keeps the language a browser already chose', async () => {
    localStorage.setItem('agents_hub_language', 'ru');
    firstRunApi.getFirstRun.mockResolvedValue({ data: { applies: true, required: false, language: 'de' } });
    renderGate();
    await screen.findByTestId('app');
    expect(localStorage.getItem('agents_hub_language')).toBe('ru');
  });
});
