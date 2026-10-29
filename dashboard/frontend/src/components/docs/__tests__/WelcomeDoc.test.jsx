import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../../i18n';
import { AuthContext, DEFAULT_AUTH } from '../../auth';
import WelcomeDoc from '../WelcomeDoc';

// Docs, Welcome: the way back to the welcome, for members too (they do not see Settings).
const api = vi.hoisted(() => ({ getWelcome: vi.fn(), welcomeAction: vi.fn() }));
vi.mock('../../../api/firstRun', () => api);

const show = (auth) => render(
  <I18nProvider>
    <AuthContext.Provider value={{ ...DEFAULT_AUTH, loading: false, ...auth }}>
      <WelcomeDoc />
    </AuthContext.Provider>
  </I18nProvider>,
);

describe('WelcomeDoc', () => {
  beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); });

  it('offers the welcome again to a person on a multi mode hub', async () => {
    api.getWelcome.mockResolvedValue({ data: { applies: true, required: false } });
    show({ mode: 'multi', user: { id: 'u1', role: 'member' } });
    expect(await screen.findByTestId('replay-welcome')).toBeTruthy();
  });

  it('offers no button outside multi mode', async () => {
    show({ mode: 'single' });
    await waitFor(() => expect(api.getWelcome).not.toHaveBeenCalled());
    expect(screen.queryByTestId('replay-welcome')).toBeNull();
  });
});
