import { render, screen, waitFor } from '@testing-library/react';
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

  it('is not asked at all in multi mode', () => {
    renderGate({ mode: 'multi', user: { id: 'u1' } });
    expect(screen.getByTestId('app')).toBeTruthy();
    expect(firstRunApi.getFirstRun).not.toHaveBeenCalled();
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
