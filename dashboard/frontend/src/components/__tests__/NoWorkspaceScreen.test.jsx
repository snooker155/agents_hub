import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import NoWorkspaceScreen from '../NoWorkspaceScreen';
import { I18nProvider } from '../../i18n';
import { AuthContext, DEFAULT_AUTH, MULTI } from '../auth';

// A person whose personal workspace was deleted and who belongs to no other
// workspace sees why the app cannot open, and can make the workspace again.

const ok = (data) => Promise.resolve({ data });
const createPersonalWorkspace = vi.fn(() => ok({ name: 'personal-u1' }));

vi.mock('../../api', () => ({
  getMe: () => ok({ personal_workspace_deleted: true }),
  createPersonalWorkspace: (...args) => createPersonalWorkspace(...args),
}));

const renderScreen = (onCreated) => render(
  <I18nProvider>
    <AuthContext.Provider value={{ ...DEFAULT_AUTH, mode: MULTI, loading: false, user: { id: 'u1' } }}>
      <NoWorkspaceScreen onCreated={onCreated} />
    </AuthContext.Provider>
  </I18nProvider>,
);

describe('NoWorkspaceScreen', () => {
  it('says the service needs a workspace and that a new one comes at the next sign-in', async () => {
    renderScreen();
    expect(screen.getByText('No workspaces available')).toBeInTheDocument();
    expect(screen.getByText(/cannot work without at least one workspace/)).toBeInTheDocument();
    expect(await screen.findByText(/created after your next sign-in/)).toBeInTheDocument();
  });

  it('creates the personal workspace on the button', async () => {
    const onCreated = vi.fn();
    renderScreen(onCreated);
    fireEvent.click(screen.getByRole('button', { name: /Create personal workspace/ }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('personal-u1'));
    expect(createPersonalWorkspace).toHaveBeenCalledTimes(1);
  });
});
