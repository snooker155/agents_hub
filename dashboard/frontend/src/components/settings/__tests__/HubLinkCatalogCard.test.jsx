import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const cat = vi.hoisted(() => ({ getCatalogSettings: vi.fn(), saveCatalogSettings: vi.fn() }));
vi.mock('../../../api/catalog', () => cat);
const i18n = vi.hoisted(() => ({ t: (k) => k, language: 'en' }));
vi.mock('../../../i18n', () => ({ useI18n: () => i18n }));

import HubLinkCatalogCard from '../HubLinkCatalogCard';

const renderCard = () => render(<MemoryRouter><HubLinkCatalogCard /></MemoryRouter>);

describe('HubLinkCatalogCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    cat.getCatalogSettings.mockResolvedValue({ data: { linked: true, enabled: false, url: 'https://hub.test' } });
    cat.saveCatalogSettings.mockResolvedValue({ data: { enabled: true } });
  });

  it('explains the mode and saves the switch on its own', async () => {
    renderCard();
    await waitFor(() => expect(screen.getByTestId('catalog-card')).toBeInTheDocument());
    expect(screen.getByText('catalog.settingsCard.intro')).toBeInTheDocument();
    expect(screen.queryByTestId('catalog-open')).toBeNull();
    fireEvent.click(screen.getByTestId('catalog-toggle'));
    await waitFor(() => expect(cat.saveCatalogSettings).toHaveBeenCalledWith(true));
    await waitFor(() => expect(screen.getByTestId('catalog-open')).toHaveAttribute('href', '/catalog'));
  });

  it('is off while no hub is linked', async () => {
    cat.getCatalogSettings.mockResolvedValue({ data: { linked: false, enabled: false } });
    renderCard();
    await waitFor(() => expect(screen.getByTestId('catalog-toggle')).toBeDisabled());
    expect(screen.getByText('catalog.settingsCard.notLinked')).toBeInTheDocument();
  });

  it('shows the server message when saving is refused', async () => {
    cat.saveCatalogSettings.mockRejectedValue({ response: { data: { detail: 'Administrator access required' } } });
    renderCard();
    await waitFor(() => expect(screen.getByTestId('catalog-toggle')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('catalog-toggle'));
    expect(await screen.findByText('Administrator access required')).toBeInTheDocument();
  });
});
