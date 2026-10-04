import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// Plan (scheduled jobs + notifications). memory_consolidate (fifth-cycle
// stage 2, "dreams") is one more job kind on top of notification/agent_task/
// flow/heartbeat: a pool and a session limit instead of an agent or a flow.

const ok = (data) => Promise.resolve({ data });

const getPlanJobs = vi.fn(() => ok([]));
const createPlanJob = vi.fn(() => ok({ id: 'job-new' }));
const updatePlanJob = vi.fn(() => ok({}));
const deletePlanJob = vi.fn(() => ok({}));
const pausePlanJob = vi.fn(() => ok({}));
const resumePlanJob = vi.fn(() => ok({}));
const cancelPlanJob = vi.fn(() => ok({}));
const runPlanJobNow = vi.fn(() => ok({}));
const getNotifications = vi.fn(() => ok([]));
const getAgents = vi.fn(() => ok([{ id: 'agent-1', name: 'Scout' }]));
const listFlows = vi.fn(() => ok([{ id: 'flow-1', name: 'Onboarding' }]));
const getSharedMemories = vi.fn(() => ok([{ id: 'pool-1', name: 'Team pool' }, { id: 'pool-2', name: 'Other pool' }]));
const getTelegramConfig = vi.fn(() => ok({ enabled: false, has_token: false }));
// CronHint (under the recurrence field once it is not 'none') debounces a
// call to this; never resolving keeps every existing assertion exactly as
// it was before the hint existed, since nothing here asserts on it.
const previewCron = vi.fn(() => new Promise(() => {}));

vi.mock('../../api', () => ({
  getPlanJobs: (...args) => getPlanJobs(...args),
  createPlanJob: (...args) => createPlanJob(...args),
  updatePlanJob: (...args) => updatePlanJob(...args),
  deletePlanJob: (...args) => deletePlanJob(...args),
  pausePlanJob: (...args) => pausePlanJob(...args),
  resumePlanJob: (...args) => resumePlanJob(...args),
  cancelPlanJob: (...args) => cancelPlanJob(...args),
  runPlanJobNow: (...args) => runPlanJobNow(...args),
  getNotifications: (...args) => getNotifications(...args),
  markNotificationRead: vi.fn(() => ok({})),
  markAllNotificationsRead: vi.fn(() => ok({})),
  deleteNotification: vi.fn(() => ok({})),
  getAgents: (...args) => getAgents(...args),
  getTelegramConfig: (...args) => getTelegramConfig(...args),
  listFlows: (...args) => listFlows(...args),
  getSharedMemories: (...args) => getSharedMemories(...args),
  previewCron: (...args) => previewCron(...args),
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ workspaceFilter: undefined, liveUpdates: false }),
}));

vi.mock('../../components/stream', () => ({
  useLiveRefetch: () => {},
}));

import Plan from '../Plan';

const show = () => render(
  <I18nProvider><MemoryRouter><Plan /></MemoryRouter></I18nProvider>,
);

const CONSOLIDATE_JOB = {
  id: 'job-1',
  kind: 'memory_consolidate',
  title: 'Nightly consolidation',
  message: '',
  run_at: '2026-10-05T09:00:00.000Z',
  recurrence: 'daily',
  cron: null,
  timezone: 'UTC',
  catch_up: false,
  status: 'scheduled',
  created_by: 'user',
  workspace: null,
  agent_id: null,
  flow_id: null,
  consolidate_pool_id: 'pool-1',
  consolidate_session_limit: 10,
  paused_reason: null,
  fire_count: 2,
  created_task_ids: [],
  last_error: null,
};

beforeEach(() => {
  getPlanJobs.mockClear();
  createPlanJob.mockClear();
  updatePlanJob.mockClear();
  getPlanJobs.mockImplementation(() => ok([]));
});

describe('Plan — scheduling a memory consolidation job', () => {
  it('shows a pool picker and a session limit once the kind is picked', async () => {
    show();
    fireEvent.click(screen.getByRole('button', { name: /^schedule$/i }));
    await waitFor(() => expect(screen.getByText('Memory consolidation')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Memory consolidation'));
    expect(await screen.findByText('Select a pool…')).toBeInTheDocument();
    expect(screen.getByDisplayValue('10')).toBeInTheDocument();
    expect(screen.getByText('Team pool')).toBeInTheDocument();
    expect(screen.getByText('Other pool')).toBeInTheDocument();
  });
});

describe('Plan — the job list', () => {
  it('shows the memory consolidation badge and the pool name as the target', async () => {
    getPlanJobs.mockImplementation(() => ok([CONSOLIDATE_JOB]));
    show();
    await waitFor(() => expect(screen.getByText('Nightly consolidation')).toBeInTheDocument());
    expect(screen.getByText('Memory consolidation')).toBeInTheDocument();
    expect(screen.getByText('Team pool')).toBeInTheDocument();
  });
});

describe('Plan — editing a memory consolidation job', () => {
  it('saves a changed pool and session limit', async () => {
    getPlanJobs.mockImplementation(() => ok([CONSOLIDATE_JOB]));
    show();
    await waitFor(() => expect(screen.getByText('Nightly consolidation')).toBeInTheDocument());

    fireEvent.click(screen.getByTitle('Edit'));
    await waitFor(() => expect(screen.getByText('Edit Scheduled Job')).toBeInTheDocument());

    const poolSelect = screen.getByDisplayValue('Team pool');
    fireEvent.change(poolSelect, { target: { value: 'pool-2' } });
    const limitInput = screen.getByDisplayValue('10');
    fireEvent.change(limitInput, { target: { value: '5' } });

    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(updatePlanJob).toHaveBeenCalled());
    expect(updatePlanJob.mock.calls[0][0]).toBe('job-1');
    expect(updatePlanJob.mock.calls[0][1].consolidate_pool_id).toBe('pool-2');
    expect(updatePlanJob.mock.calls[0][1].consolidate_session_limit).toBe(5);
  });

  it('refuses to save without a pool picked', async () => {
    getPlanJobs.mockImplementation(() => ok([CONSOLIDATE_JOB]));
    show();
    await waitFor(() => expect(screen.getByText('Nightly consolidation')).toBeInTheDocument());

    fireEvent.click(screen.getByTitle('Edit'));
    await waitFor(() => expect(screen.getByText('Edit Scheduled Job')).toBeInTheDocument());

    const poolSelect = screen.getByDisplayValue('Team pool');
    fireEvent.change(poolSelect, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    expect(await screen.findByText('Pick a memory pool to consolidate')).toBeInTheDocument();
    expect(updatePlanJob).not.toHaveBeenCalled();
  });
});
