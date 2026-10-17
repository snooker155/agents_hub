import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// One workspace's row of the sync settings, from its own Settings tab: it
// reads the row from the shared status and saves only this workspace.

const sync = vi.hoisted(() => ({
  getHubSync: vi.fn(), setHubSyncWorkspace: vi.fn(), notifySyncChanged: () => {},
  saveHubSync: vi.fn(), setHubSyncKey: vi.fn(), forgetHubSyncKey: vi.fn(), runHubSync: vi.fn(),
  listHubSyncConflicts: vi.fn(), getHubSyncConflict: vi.fn(), resolveHubSyncConflict: vi.fn(), relativeTime: () => '',
}));
vi.mock('../../../api/hubSync', () => sync);
vi.mock('../../../api/workspaces', () => ({ getWorkspaces: () => Promise.resolve({ data: [] }) }));
vi.mock('../../../i18n', () => ({ useI18n: () => ({ t: (k) => k, language: 'en' }) }));

import WorkspaceHubSync from '../WorkspaceHubSync';

const linked = {
  linked: true, enabled: true, remote_name: 'office', key_set: true, key_unlocked: true,
  mappings: [{ workspace: 'other', space: 'other', encrypted: false }],
};
const show = () => render(<MemoryRouter><WorkspaceHubSync workspace="team" /></MemoryRouter>);

describe('WorkspaceHubSync', () => {
  beforeEach(() => vi.clearAllMocks());

  it('points at the link form while the hub is not linked', async () => {
    sync.getHubSync.mockResolvedValue({ data: { linked: false } });
    show();
    await waitFor(() => expect(screen.getByTestId('ws-sync-unlinked')).toBeInTheDocument());
    expect(screen.getByText('hubSync.workspace.openSettings').closest('a')).toHaveAttribute('href', '/settings/hubLink');
  });

  it('turns sync and encryption on for this workspace only', async () => {
    sync.getHubSync.mockResolvedValue({ data: linked });
    sync.setHubSyncWorkspace.mockResolvedValue({ data: { ...linked, mappings: [
      ...linked.mappings, { workspace: 'team', space: 'team-there', encrypted: true }] } });
    show();
    await waitFor(() => expect(screen.getByTestId('ws-sync-on')).not.toBeChecked());
    fireEvent.click(screen.getByTestId('ws-sync-on'));
    fireEvent.change(screen.getByTestId('ws-sync-space'), { target: { value: 'team-there' } });
    fireEvent.click(screen.getByTestId('ws-sync-encrypted'));
    fireEvent.click(screen.getByTestId('ws-sync-save'));
    await waitFor(() => expect(sync.setHubSyncWorkspace).toHaveBeenCalledWith('team', {
      synced: true, space: 'team-there', encrypted: true }));
    await waitFor(() => expect(screen.getByText('hubSync.card.saved')).toBeInTheDocument());
    expect(screen.getByTestId('ws-sync-encrypted')).toBeChecked();
  });

  it('asks for a key before ticking encryption when there is none', async () => {
    sync.getHubSync.mockResolvedValue({ data: { ...linked, key_set: false,
      mappings: [{ workspace: 'team', space: 'team', encrypted: false }] } });
    show();
    await waitFor(() => expect(screen.getByTestId('ws-sync-encrypted')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('ws-sync-encrypted'));
    expect(screen.getByTestId('sync-key-dialog')).toBeInTheDocument();
    expect(screen.getByTestId('ws-sync-encrypted')).not.toBeChecked();
  });

  it('shows encryption on and locked when every workspace is encrypted', async () => {
    sync.getHubSync.mockResolvedValue({ data: { ...linked, encrypt_all: true,
      mappings: [{ workspace: 'team', space: 'team', encrypted: true }] } });
    show();
    await waitFor(() => expect(screen.getByTestId('ws-sync-encrypted')).toBeChecked());
    expect(screen.getByTestId('ws-sync-encrypted')).toBeDisabled();
    expect(screen.getByText('hubSync.workspace.allOn')).toBeInTheDocument();
  });
});
