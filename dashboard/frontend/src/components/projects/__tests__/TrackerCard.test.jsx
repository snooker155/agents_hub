import { render, waitFor } from '@testing-library/react';
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
}));
vi.mock('../../../api/trackers', () => api);
vi.mock('../../../i18n', () => ({ useI18n: () => ({ t: (k) => k }) }));

describe('TrackerCard: uses the project\'s own workspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listTrackerProjects.mockResolvedValue({ data: [] });
  });

  it('passes the project\'s workspace when listing a provider\'s projects', async () => {
    api.getProjectTracker.mockResolvedValue({ data: { provider: 'jira', remote_id: '' } });
    render(<MemoryRouter><TrackerCard projectId="p1" workspace="acme" /></MemoryRouter>);
    await waitFor(() => expect(api.listTrackerProjects).toHaveBeenCalledWith('jira', 'acme'));
  });
});
