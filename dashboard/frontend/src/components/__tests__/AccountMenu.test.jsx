import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../i18n';

// The account block at the foot of the sidebar: the person's name with the
// live status beside it, and a menu with Profile, Personalization (theme and
// language), Help and Sign out.

const setTheme = vi.fn();
const logout = vi.fn();
let auth;

vi.mock('../theme', () => ({ useTheme: () => ({ theme: 'light', setTheme }) }));
vi.mock('../auth', async (importOriginal) => ({
  ...(await importOriginal()), useAuth: () => auth,
}));

const { default: AccountMenu } = await import('../AccountMenu');
const { accountName } = await import('../accountName');

function renderMenu(props = {}) {
  render(
    <I18nProvider>
      <MemoryRouter><AccountMenu {...props} /></MemoryRouter>
    </I18nProvider>,
  );
}

const openMenu = () => fireEvent.click(screen.getByRole('button', { name: /account menu|меню аккаунта|kontomenü/i }));

beforeEach(() => {
  setTheme.mockClear();
  logout.mockClear();
  localStorage.setItem('agents_hub_language', 'en');
  auth = {
    mode: 'multi',
    user: { username: 'jdoe', display_name: 'jdoe', first_name: 'Jane', last_name: 'Doe' },
    logout,
  };
});

describe('accountName', () => {
  it('prefers first and last name, then the display name, then the login', () => {
    expect(accountName({ username: 'u', display_name: 'D', first_name: 'A', last_name: 'B' })).toBe('A B');
    expect(accountName({ username: 'u', display_name: 'D', first_name: '', last_name: '' })).toBe('D');
    expect(accountName({ username: 'u' })).toBe('u');
  });
});

describe('AccountMenu', () => {
  it('shows the name with the live status beside it', () => {
    renderMenu({ status: 'paused' });
    expect(screen.getByTestId('account-name')).toHaveTextContent('Jane Doe');
    expect(screen.getByTestId('account-status')).toHaveAttribute('data-status', 'paused');
  });

  it('opens Profile, Personalization, Help and Sign out', () => {
    const onHelp = vi.fn();
    renderMenu({ onHelp });
    openMenu();
    expect(screen.getByRole('menuitem', { name: /profile/i })).toHaveAttribute('href', '/account');
    fireEvent.click(screen.getByRole('menuitem', { name: /sign out|log out/i }));
    expect(logout).toHaveBeenCalled();
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: /^help$/i }));
    expect(onHelp).toHaveBeenCalled();
  });

  it('switches the theme and the language from Personalization', () => {
    renderMenu();
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: /personalization/i }));
    fireEvent.click(screen.getByRole('button', { name: /dark/i }));
    expect(setTheme).toHaveBeenCalledWith('dark');
    fireEvent.click(screen.getByRole('menuitemradio', { name: /русский/i }));
    expect(screen.getByRole('menuitemradio', { name: /русский/i })).toHaveAttribute('aria-checked', 'true');
  });

  it('switches between the simple and the full menu from Personalization', () => {
    const onMenuModeChange = vi.fn();
    renderMenu({ menuMode: 'simple', onMenuModeChange, moreCount: 12 });
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: /personalization/i }));
    expect(screen.getByRole('button', { name: /^simple$/i })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: /full/i }));
    expect(onMenuModeChange).toHaveBeenCalledWith('full');
  });

  it('has no Profile and no Sign out outside multi mode', () => {
    auth = { mode: 'single', user: null, logout };
    renderMenu();
    openMenu();
    expect(screen.queryByRole('menuitem', { name: /profile/i })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: /sign out|log out/i })).toBeNull();
    expect(screen.getByRole('menuitem', { name: /personalization/i })).toBeInTheDocument();
  });
});
