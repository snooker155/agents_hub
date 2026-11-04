import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import TrackerCard from '../TrackerCard';

// Jira and Linear credentials live per workspace on the Connectors page
// (connectors/channels/store.py: the default workspace's credentials work
// everywhere, another workspace's own work only there), so listing a
// provider's projects for a tracker link needs that project's own workspace.

const api = vi.hoisted(() => ({
  getProjectTracker: vi.fn(),
  setProjectTracker: vi.fn(),
  syncProjectTracker: vi.fn(),
  listTrackerProjects: vi.fn(),
  getTrackerStatus: vi.fn(),
}));
vi.mock('../../../api/trackers', () => api);
vi.mock('../../../i18n', () => ({ useI18n: () => ({ t: (k) => k }) }));

describe('TrackerCard: uses the project\'s own workspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listTrackerProjects.mockResolvedValue({ data: [] });
    api.getTrackerStatus.mockResolvedValue({ data: { jira: { configured: true }, linear: { configured: false } } });
  });

  it('passes the project\'s workspace when listing a provider\'s projects', async () => {
    api.getProjectTracker.mockResolvedValue({ data: { provider: 'jira', remote_id: '' } });
    render(<MemoryRouter><TrackerCard projectId="p1" workspace="acme" /></MemoryRouter>);
    await waitFor(() => expect(api.listTrackerProjects).toHaveBeenCalledWith('jira', 'acme'));
  });

  it('asks the project\'s workspace which trackers are connected', async () => {
    api.getProjectTracker.mockResolvedValue({ data: { provider: 'none' } });
    render(<MemoryRouter><TrackerCard projectId="p1" workspace="acme" /></MemoryRouter>);
    await waitFor(() => expect(api.getTrackerStatus).toHaveBeenCalledWith('acme'));
  });
});

describe('TrackerCard: the tracker list', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listTrackerProjects.mockResolvedValue({ data: [] });
    api.getTrackerStatus.mockResolvedValue({ data: { jira: { configured: true }, linear: { configured: false } } });
    api.getProjectTracker.mockResolvedValue({ data: { provider: 'none' } });
  });

  it('marks each tracker connected or not, and a tracker not connected cannot be picked', async () => {
    render(<MemoryRouter><TrackerCard projectId="p1" workspace="acme" /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: /projectDetails.tracker.providers.none/ }));
    await waitFor(() => expect(screen.getByRole('option', { name: /providers.jira.*tracker.connected/ })).not.toBeDisabled());
    expect(screen.getByRole('option', { name: /providers.linear.*tracker.notConnected/ })).toBeDisabled();
    fireEvent.click(screen.getByRole('option', { name: /providers.jira/ }));
    // Picked: the list closes and the remote key field appears.
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('projectDetails.tracker.remotePlaceholder.jira')).toBeInTheDocument();
  });
});
