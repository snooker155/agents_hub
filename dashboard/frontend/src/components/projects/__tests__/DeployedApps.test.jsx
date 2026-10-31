import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

import { I18nProvider, useI18n } from '../../../i18n';
import { WorkspaceContext } from '../../workspace';

// The apps the hub runs for projects show on the Projects page, each
// opening on its project, a running one also at its share address.

const listDeployedApps = vi.fn(() => Promise.resolve({ data: { items: [
  { id: 'd1', project_id: 'p1', name: 'Shop', status: 'running', mode: 'local', services: [{}, {}],
    workspace: 'acme', links: { path: '/apps/shop/' } },
  { id: 'd2', project_id: 'p2', name: 'Blog', status: 'stopped', mode: 'docker', services: [{}],
    workspace: 'acme', links: {} },
] } }));
vi.mock('../../../api', () => ({ listDeployedApps: (...a) => listDeployedApps(...a) }));

import DeployedApps from '../DeployedApps';

function Shown() {
  const { t } = useI18n();
  return <DeployedApps t={t} />;
}

const show = (workspaceFilter) => render(
  <MemoryRouter>
    <I18nProvider>
      <WorkspaceContext.Provider value={{ workspaceFilter }}><Shown /></WorkspaceContext.Provider>
    </I18nProvider>
  </MemoryRouter>,
);

describe('DeployedApps', () => {
  it('lists the apps of the workspace with their projects and share links', async () => {
    show('acme');
    expect((await screen.findByText('Shop')).closest('a')).toHaveAttribute('href', '/projects/p1');
    expect(listDeployedApps).toHaveBeenCalledWith('acme');
    const open = screen.getAllByRole('link').filter((a) => a.getAttribute('href') === '/apps/shop/');
    expect(open).toHaveLength(1);
    // A stopped app has no address to open.
    expect(screen.getByText('Blog').closest('tr').querySelector('a[target]')).toBeNull();
  });

  it('shows nothing while there are no apps', async () => {
    listDeployedApps.mockImplementationOnce(() => Promise.resolve({ data: { items: [] } }));
    const { container } = show(undefined);
    await Promise.resolve();
    expect(container.querySelector('[data-testid="deployed-apps"]')).toBeNull();
  });
});
