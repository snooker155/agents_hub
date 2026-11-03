import { render, screen, within, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import WorkspaceManager from '../WorkspaceManager';
import { I18nProvider } from '../../i18n';

// The delete button follows the backend's can_delete (readers and editors get
// none), and deleting a personal workspace says a new one comes at sign-in.

const ok = (data) => Promise.resolve({ data });
const deleteWorkspace = vi.fn(() => ok({ deleted: true }));

vi.mock('../../api', () => ({
  getWorkspaces: () => ok([
    { name: 'personal-u1', personal: true, own_personal: true, can_delete: true, tasks_count: 0 },
    { name: 'default', can_delete: false, tasks_count: 0 },
    { name: 'shared', can_delete: false, tasks_count: 0 },
    { name: 'mine', can_delete: true, tasks_count: 0 },
  ]),
  createWorkspace: () => ok({}),
  deleteWorkspace: (...args) => deleteWorkspace(...args),
}));

const renderPage = () => render(
  <I18nProvider>
    <MemoryRouter><WorkspaceManager /></MemoryRouter>
  </I18nProvider>,
);

const row = (name) => screen.getByText(name).closest('tr');

describe('WorkspaceManager delete', () => {
  beforeEach(() => { deleteWorkspace.mockClear(); });

  it('offers delete only where the backend allows it', async () => {
    renderPage();
    await screen.findByText('shared');
    expect(within(row('shared')).queryByTitle('Delete workspace')).toBeNull();
    expect(within(row('mine')).getByTitle('Delete workspace')).toBeInTheDocument();
    expect(within(row('personal-u1')).getByTitle('Delete workspace')).toBeInTheDocument();
  });

  it('warns that a new personal workspace comes at the next sign-in', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderPage();
    await screen.findByText('personal-u1');
    fireEvent.click(within(row('personal-u1')).getByTitle('Delete workspace'));
    expect(confirm.mock.calls[0][0]).toMatch(/new personal workspace will be created after your next sign-in/);
    await waitFor(() => expect(deleteWorkspace).toHaveBeenCalledWith('personal-u1'));
    confirm.mockRestore();
  });
});
