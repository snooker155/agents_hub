import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const cat = vi.hoisted(() => ({
  CATALOG_KINDS: ['agents', 'skills', 'flows', 'teams', 'loops'],
  getCatalogSettings: vi.fn(), browseCatalog: vi.fn(), getCatalogItem: vi.fn(),
  planCatalogInstall: vi.fn(), installCatalogItem: vi.fn(), getCatalogUpdates: vi.fn(),
  getCatalogDiff: vi.fn(), updateCatalogItem: vi.fn(),
  catalogError: (e, fb) => e?.response?.data?.detail || fb,
}));
vi.mock('../../api/catalog', () => cat);
vi.mock('../../api/workspaces', () => ({ getWorkspaces: () => Promise.resolve({ data: [{ name: 'default' }, { name: 'work' }] }) }));
// One stable value, like the real hook: a new `t` every render would refire the plan effect.
const i18n = vi.hoisted(() => ({ t: (k, v) => (v ? `${k} ${JSON.stringify(v)}` : k), language: 'en' }));
vi.mock('../../i18n', () => ({ useI18n: () => i18n }));
const authState = vi.hoisted(() => ({ value: { mode: 'single', user: null } }));
vi.mock('../../components/auth', async (importOriginal) => ({
  ...(await importOriginal()), useAuth: () => authState.value,
}));

import Catalog from '../Catalog';
import enCatalog from '../../i18n/locales/en/catalog';
import ruCatalog from '../../i18n/locales/ru/catalog';
import deCatalog from '../../i18n/locales/de/catalog';
import enSettings from '../../i18n/locales/en/settings';
import ruSettings from '../../i18n/locales/ru/settings';
import deSettings from '../../i18n/locales/de/settings';

const CARD = { kind: 'agents', id: 'writer', name: 'Writer', description: 'Writes things', author: 'team', updated_at: null, agents: [], meta: {}, installed_as: [] };
const PLAN = {
  can_install: true, blocked: [], conflicts: [],
  steps: [{ kind: 'agents', remote_id: 'writer', name: 'Writer', action: 'install', local_id: 'writer', reason: '', root: true, tools_dropped: [] }],
};
const renderPage = () => render(<MemoryRouter><Catalog /></MemoryRouter>);

describe('Catalog page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.value = { mode: 'single', user: null };
    cat.getCatalogSettings.mockResolvedValue({ data: { linked: true, enabled: true } });
    cat.browseCatalog.mockResolvedValue({ data: { items: [CARD] } });
    cat.getCatalogItem.mockResolvedValue({ data: { ...CARD, text: 'name: Writer\ninstructions: Be brief' } });
    cat.planCatalogInstall.mockResolvedValue({ data: PLAN });
    cat.installCatalogItem.mockResolvedValue({ data: { ok: true } });
    cat.getCatalogUpdates.mockResolvedValue({ data: { items: [] } });
  });

  it('points to Settings while the mode is off', async () => {
    cat.getCatalogSettings.mockResolvedValue({ data: { linked: true, enabled: false } });
    renderPage();
    expect(await screen.findByTestId('catalog-gate')).toHaveTextContent('catalog.off');
    expect(cat.browseCatalog).not.toHaveBeenCalled();
  });

  it('lists cards, searches, and installs after showing the plan', async () => {
    renderPage();
    const card = await screen.findByTestId('catalog-card-item');
    expect(card).toHaveTextContent('Writer');
    fireEvent.change(screen.getByTestId('catalog-search'), { target: { value: 'wri' } });
    await waitFor(() => expect(cat.browseCatalog).toHaveBeenCalledWith('agents', 'wri'));
    fireEvent.click(await screen.findByTestId('catalog-card-item'));
    expect(await screen.findByTestId('catalog-definition')).toHaveTextContent('Be brief');
    expect(await screen.findByTestId('catalog-plan-step')).toHaveTextContent('catalog.action.install');
    fireEvent.change(screen.getByTestId('catalog-workspace'), { target: { value: 'work' } });
    await waitFor(() => expect(cat.planCatalogInstall).toHaveBeenLastCalledWith('agents', 'writer', 'work', ''));
    fireEvent.click(screen.getByTestId('catalog-install'));
    await waitFor(() => expect(cat.installCatalogItem).toHaveBeenCalledWith('agents', 'writer', 'work', null));
    expect(await screen.findByTestId('catalog-done')).toBeInTheDocument();
  });

  it('asks for a new id on a conflict and sends it', async () => {
    const conflict = { kind: 'agents', remote_id: 'writer', name: 'Writer', action: 'conflict', local_id: null, suggested_id: 'writer-hub', reason: 'taken', root: true, tools_dropped: [] };
    cat.planCatalogInstall.mockResolvedValue({ data: { can_install: false, blocked: [], conflicts: [conflict], steps: [conflict] } });
    renderPage();
    fireEvent.click(await screen.findByTestId('catalog-card-item'));
    const input = await screen.findByTestId('catalog-new-id');
    expect(input.value).toBe('writer-hub');
    fireEvent.change(input, { target: { value: 'writer-mine' } });
    fireEvent.click(screen.getByTestId('catalog-install'));
    await waitFor(() => expect(cat.installCatalogItem).toHaveBeenCalledWith('agents', 'writer', 'default', 'writer-mine'));
  });

  it('shows the diff before an update is applied', async () => {
    cat.getCatalogUpdates.mockResolvedValue({ data: { items: [
      { kind: 'agents', local_id: 'writer-hub', remote_id: 'writer', name: 'Writer', workspace: 'default', update_available: true, locally_modified: true },
    ] } });
    cat.getCatalogDiff.mockResolvedValue({ data: { diff: '--- a\n+++ b\n-instructions: one\n+instructions: two' } });
    cat.updateCatalogItem.mockResolvedValue({ data: { ok: true } });
    renderPage();
    fireEvent.click(await screen.findByTestId('catalog-tab-updates'));
    const row = await screen.findByTestId('catalog-update-row');
    expect(row).toHaveTextContent('catalog.updates.edited');
    fireEvent.click(screen.getByTestId('catalog-show-diff'));
    expect(await screen.findByTestId('catalog-diff')).toHaveTextContent('+instructions: two');
    expect(cat.updateCatalogItem).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('catalog-update'));
    await waitFor(() => expect(cat.updateCatalogItem).toHaveBeenCalledWith('agents', 'writer-hub'));
  });

  it('offers teams and loops to an administrator', async () => {
    authState.value = { mode: 'multi', user: { role: 'admin' } };
    renderPage();
    expect(await screen.findByTestId('catalog-tab-teams')).toBeInTheDocument();
    expect(screen.getByTestId('catalog-tab-loops')).toBeInTheDocument();
  });

  it('hides teams and loops from a non-administrator, who would get 403 on them', async () => {
    authState.value = { mode: 'multi', user: { role: 'member' } };
    renderPage();
    expect(await screen.findByTestId('catalog-tab-agents')).toBeInTheDocument();
    expect(screen.getByTestId('catalog-tab-updates')).toBeInTheDocument();
    expect(screen.queryByTestId('catalog-tab-teams')).toBeNull();
    expect(screen.queryByTestId('catalog-tab-loops')).toBeNull();
  });

  it('shows the server message on a 403', async () => {
    cat.browseCatalog.mockRejectedValue({ response: { status: 403, data: { detail: 'Only an administrator can do this.' } } });
    renderPage();
    expect(await screen.findByText('Only an administrator can do this.')).toBeInTheDocument();
  });

  it('shows the message of an object detail on a 403, not the generic failure', async () => {
    const plan = { steps: [{ action: 'conflict', reason: 'a step reason' }] };
    cat.browseCatalog.mockRejectedValue({ response: { status: 403, data: { detail: { message: 'not your workspace', plan } } } });
    renderPage();
    expect(await screen.findByText('not your workspace')).toBeInTheDocument();
    expect(screen.queryByText('a step reason')).toBeNull();
  });

  it('falls back to a readable refusal on a 403 without a message', async () => {
    cat.browseCatalog.mockRejectedValue({ response: { status: 403, data: {} } });
    renderPage();
    expect(await screen.findByText('catalog.forbidden')).toBeInTheDocument();
  });

  it('names the Remote hub tab and links to it while not linked', async () => {
    cat.getCatalogSettings.mockResolvedValue({ data: { linked: false, enabled: false } });
    renderPage();
    const gate = await screen.findByTestId('catalog-gate');
    expect(gate).toHaveTextContent('catalog.notLinked');
    expect(screen.getByRole('link', { name: 'catalog.openSettings' })).toHaveAttribute('href', '/settings/hubLink');
  });

  // The gate sends people to a Settings tab by name, so the name must be the
  // one that tab carries in each language.
  it.each([['en', enCatalog, enSettings], ['ru', ruCatalog, ruSettings], ['de', deCatalog, deSettings]])(
    'names the Settings tab as it is labelled (%s)', (_lang, catalog, settings) => {
      const label = settings.nav.hubLink;
      expect(catalog.notLinked).toContain(label);
      expect(catalog.off).toContain(label);
    },
  );
});
