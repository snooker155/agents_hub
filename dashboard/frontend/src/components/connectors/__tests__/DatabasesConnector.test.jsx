import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { WorkspaceContext } from '../../workspace';
import { I18nProvider } from '../../../i18n';
import DatabasesConnector from '../DatabasesConnector';

const api = vi.hoisted(() => ({
  listDbConnections: vi.fn(),
  createDbConnection: vi.fn(),
  deleteDbConnection: vi.fn(),
  testDbConnection: vi.fn(),
}));
vi.mock('../../../api/databases', () => api);

const ROWS = [
  { id: 'own', name: 'crm', kind: 'postgres', dsn_hint: 'postgresql://db', row_limit: 200, workspace: 'team-a' },
  { id: 'shared', name: 'warehouse', kind: 'postgres', dsn_hint: 'postgresql://wh', row_limit: 200, workspace: 'default' },
];

const show = (workspace) => render(
  <WorkspaceContext.Provider value={{ selectedWorkspace: workspace }}>
    <I18nProvider><DatabasesConnector /></I18nProvider>
  </WorkspaceContext.Provider>,
);

describe('DatabasesConnector: the default workspace\'s connections', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('lists them in another workspace, marked and without a remove button', async () => {
    api.listDbConnections.mockResolvedValue({ data: ROWS });
    show('team-a');
    await waitFor(() => expect(screen.getByTestId('db-row-shared')).toBeTruthy());
    expect(api.listDbConnections).toHaveBeenCalledWith('team-a', { includeDefault: true });
    expect(screen.getByTestId('db-inherited-shared')).toBeTruthy();
    expect(screen.queryByTestId('db-inherited-own')).toBeNull();
    const removeButtons = (id) => screen.getByTestId(`db-row-${id}`).querySelectorAll('button');
    expect(removeButtons('own').length).toBe(2);
    expect(removeButtons('shared').length).toBe(1);
  });

  it('in the default workspace they are its own', async () => {
    api.listDbConnections.mockResolvedValue({ data: [ROWS[1]] });
    show('default');
    await waitFor(() => expect(screen.getByTestId('db-row-shared')).toBeTruthy());
    expect(api.listDbConnections).toHaveBeenCalledWith('default', { includeDefault: false });
    expect(screen.queryByTestId('db-inherited-shared')).toBeNull();
  });
});
