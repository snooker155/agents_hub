import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../../i18n';

const getSyncHere = vi.fn();
vi.mock('../../../api/syncVault', () => ({ getSyncHere: (...a) => getSyncHere(...a) }));
vi.mock('../../workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: 'acme' }) }));

import SyncedDataBanner from '../SyncedDataBanner';

const renderBanner = () => render(<MemoryRouter><I18nProvider><SyncedDataBanner /></I18nProvider></MemoryRouter>);
const here = (space) => Promise.resolve({ data: { synced: true, space } });

describe('SyncedDataBanner', () => {
  beforeEach(() => getSyncHere.mockReset());

  it('shows for an encrypted space with records and can be dismissed', async () => {
    getSyncHere.mockReturnValue(here({ encrypted: true, counts: { encrypted: 5 } }));
    renderBanner();
    expect(await screen.findByText(/holds 5 encrypted records/)).toBeInTheDocument();
    expect(screen.getByText('View synced data')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Dismiss'));
    expect(screen.queryByText(/holds 5/)).not.toBeInTheDocument();
  });

  it('renders nothing when not synced, plain, or empty', async () => {
    for (const reply of [
      Promise.resolve({ data: { synced: false } }),
      here({ encrypted: false, counts: { encrypted: 0 } }),
      here({ encrypted: true, counts: { encrypted: 0 } }),
    ]) {
      getSyncHere.mockReturnValue(reply);
      const { container, unmount } = renderBanner();
      await waitFor(() => expect(getSyncHere).toHaveBeenCalled());
      expect(container).toBeEmptyDOMElement();
      unmount();
    }
  });
});
