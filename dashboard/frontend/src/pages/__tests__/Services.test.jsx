import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';
import { WorkspaceContext } from '../../components/workspace';

// The Services page lists the desired state behind the running copies: each
// service with its agent, replicas (live over min–max), status, and the
// actions that pause, resume and delete it. Deploy opens the form.

const ok = (data) => Promise.resolve({ data });

const SERVICES = [
  { service_id: 'svc_reviewers', name: 'reviewers', kind: 'agent', agent_id: 'swe_agent', workspace: 'ws',
    environment_name: 'prod', status: 'active', replicas_min: 1, replicas_max: 3, concurrency: 2,
    is_exposed: true, replicas: { live: 2, active: 1, standby: 1 } },
  { service_id: 'svc_runner', name: 'ws runner', kind: 'runner', agent_id: null, workspace: 'ws',
    environment_name: null, status: 'paused', paused_reason: 'crash loop', replicas_min: 1, replicas_max: 4,
    concurrency: 8, is_exposed: false, replicas: { live: 0 } },
];

const getServices = vi.fn(() => ok({ items: SERVICES, total: 2 }));
const pauseService = vi.fn(() => ok({}));
const resumeService = vi.fn(() => ok({}));
const deleteService = vi.fn(() => ok({ ok: true }));

vi.mock('../../api', () => ({
  getServices: (...a) => getServices(...a),
  pauseService: (...a) => pauseService(...a),
  resumeService: (...a) => resumeService(...a),
  deleteService: (...a) => deleteService(...a),
  createService: vi.fn(),
  getChatRoute: () => ok({ mode: 'instances', service: null, available: true, reason: null }),
  getAgents: () => ok([]),
  getWorkspaces: () => ok([]),
  getEnvironments: () => ok([]),
}));

vi.mock('../../components/stream', () => ({
  useLiveRefetch: () => {},
  useStream: () => ({ on: () => () => {} }),
}));

import Services from '../Services';

const show = () => render(
  <MemoryRouter>
    <WorkspaceContext.Provider value={{ workspaceFilter: 'ws', selectedWorkspace: 'ws', liveUpdates: false }}>
      <I18nProvider><Services /></I18nProvider>
    </WorkspaceContext.Provider>
  </MemoryRouter>,
);

beforeEach(() => {
  getServices.mockClear();
  pauseService.mockClear();
  resumeService.mockClear();
  deleteService.mockClear();
});

describe('Services page', () => {
  it('lists services with their agent, replicas and status', async () => {
    show();
    await waitFor(() => expect(screen.getByText('reviewers')).toBeInTheDocument());
    expect(getServices).toHaveBeenCalledWith({ workspace: 'ws' });
    expect(screen.getByText('swe_agent')).toBeInTheDocument();
    expect(screen.getByText('any agent')).toBeInTheDocument();
    expect(screen.getByText('/ 1–3')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByText('paused')).toBeInTheDocument();
    expect(screen.getByText('crash loop')).toBeInTheDocument();
    expect(screen.getByText('runner: answers chat turns of any agent')).toBeInTheDocument();
  });

  it('pauses an active service and resumes a paused one', async () => {
    show();
    await waitFor(() => expect(screen.getByText('reviewers')).toBeInTheDocument());
    fireEvent.click(screen.getByTitle('Pause'));
    await waitFor(() => expect(pauseService).toHaveBeenCalledWith('svc_reviewers'));
    fireEvent.click(screen.getByTitle('Resume'));
    await waitFor(() => expect(resumeService).toHaveBeenCalledWith('svc_runner'));
  });

  it('asks before deleting and opens the deploy form', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    show();
    await waitFor(() => expect(screen.getByText('reviewers')).toBeInTheDocument());
    fireEvent.click(screen.getAllByTitle('Delete')[0]);
    await waitFor(() => expect(deleteService).toHaveBeenCalledWith('svc_reviewers'));
    expect(confirm).toHaveBeenCalled();
    fireEvent.click(screen.getByText('Deploy'));
    expect(await screen.findByText('Deploy as a service')).toBeInTheDocument();
    expect(screen.getByText('Runner (any agent)')).toBeInTheDocument();
    confirm.mockRestore();
  });
});
