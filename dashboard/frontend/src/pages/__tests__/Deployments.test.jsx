import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// Deployments (Part D of the run-budget / firing-journal / environments /
// deployments contract): agent_task/flow/loop scheduled jobs with a firing
// journal, an environment and a per-run budget. The backend (Parts A/B) is
// written in parallel, so every call here is mocked against the contract's
// documented shapes.

const ok = (data) => Promise.resolve({ data });

const getPlanJobs = vi.fn(() => ok([]));
const createPlanJob = vi.fn(() => ok({ id: 'job-new' }));
const updatePlanJob = vi.fn(() => ok({}));
const deletePlanJob = vi.fn(() => ok({}));
const pausePlanJob = vi.fn(() => ok({}));
const resumePlanJob = vi.fn(() => ok({}));
const cancelPlanJob = vi.fn(() => ok({}));
const runPlanJobNow = vi.fn(() => ok({}));
const getJobFires = vi.fn(() => ok([]));
const getAgents = vi.fn(() => ok([{ id: 'agent-1', name: 'Scout' }]));
const listFlows = vi.fn(() => ok([{ id: 'flow-1', name: 'Onboarding' }]));
const getLoops = vi.fn(() => ok([{ id: 'loop-1', name: 'Retry loop' }]));
const getEnvironments = vi.fn(() => ok([{ id: 'env-1', name: 'sandboxed-python' }]));

vi.mock('../../api', () => ({
  // Project deployments (the apps table on top of the page): none in these tests.
  listDeployedApps: () => Promise.resolve({ data: { items: [] } }),
  getPlanJobs: (...args) => getPlanJobs(...args),
  createPlanJob: (...args) => createPlanJob(...args),
  updatePlanJob: (...args) => updatePlanJob(...args),
  deletePlanJob: (...args) => deletePlanJob(...args),
  pausePlanJob: (...args) => pausePlanJob(...args),
  resumePlanJob: (...args) => resumePlanJob(...args),
  cancelPlanJob: (...args) => cancelPlanJob(...args),
  runPlanJobNow: (...args) => runPlanJobNow(...args),
  getJobFires: (...args) => getJobFires(...args),
  getAgents: (...args) => getAgents(...args),
  listFlows: (...args) => listFlows(...args),
  getLoops: (...args) => getLoops(...args),
  getEnvironments: (...args) => getEnvironments(...args),
  getProjects: vi.fn(() => Promise.resolve({ data: [] })),
  getSharedMemories: vi.fn(() => Promise.resolve({ data: [] })),
  getWorkspaceSecrets: vi.fn(() => Promise.resolve({ data: [] })),
}));

// Own module (src/api/agentVersions.js), not part of the ../../api contract
// above: the version-pin select on an agent_task job only appears once this
// resolves, so an empty list here keeps every existing case exactly as it
// was before the field existed.
const getAgentVersions = vi.fn(() => ok({ agent_id: 'agent-1', versions: [] }));
vi.mock('../../api/files', () => ({
  listWorkspaceFiles: vi.fn(() => Promise.resolve({ data: { files: [] } })),
  uploadWorkspaceFileObject: vi.fn(() => Promise.resolve({ data: { id: 'file_new', filename: 'new.txt' } })),
}));
vi.mock('../../api/agentVersions', () => ({
  getAgentVersions: (...args) => getAgentVersions(...args),
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ workspaceFilter: undefined, selectedWorkspace: 'default' }),
}));

import Deployments from '../Deployments';

const show = () => render(
  <I18nProvider><MemoryRouter><Deployments /></MemoryRouter></I18nProvider>,
);

const JOB = {
  id: 'job-1',
  kind: 'agent_task',
  title: 'Nightly scout run',
  message: 'Scout new leads',
  run_at: '2026-09-25T09:00:00.000Z',
  recurrence: 'daily',
  cron: null,
  timezone: 'UTC',
  catch_up: false,
  status: 'paused',
  workspace: null,
  agent_id: 'agent-1',
  flow_id: null,
  loop_id: null,
  environment_id: 'env-1',
  budget_usd: 5,
  auto_pause_after: 3,
  consecutive_errors: 3,
  paused_reason: 'errors',
  fire_count: 4,
  last_fired_at: '2026-09-24T09:00:00.000Z',
  last_error: 'boom',
  created_task_ids: ['task-9'],
  upcoming_runs_at: ['2026-09-26T09:00:00.000Z'],
  last_fire: { at: '2026-09-24T09:00:00.000Z', ok: false, error_type: 'other' },
};

beforeEach(() => {
  getPlanJobs.mockClear();
  createPlanJob.mockClear();
  updatePlanJob.mockClear();
  getPlanJobs.mockImplementation(() => ok([]));
});

describe('Deployments — empty state', () => {
  it('says nothing is deployed yet', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/nothing scheduled yet/i)).toBeInTheDocument());
  });

  it('asks the jobs API for the deployment kinds only (agent_task, flow, loop, heartbeat)', async () => {
    show();
    await waitFor(() => expect(getPlanJobs).toHaveBeenCalled());
    expect(getPlanJobs.mock.calls[0][2]).toEqual(['agent_task', 'flow', 'loop', 'heartbeat']);
  });
});

describe('Deployments — the list', () => {
  it('shows the target, the paused reason and the last fire result', async () => {
    getPlanJobs.mockImplementation(() => ok([JOB]));
    show();

    await waitFor(() => expect(screen.getByText('Nightly scout run')).toBeInTheDocument());
    expect(screen.getByText('Scout')).toBeInTheDocument();
    expect(screen.getByText(/errors/i)).toBeInTheDocument();
    expect(screen.getByText(/3 consecutive failures/i)).toBeInTheDocument();
    expect(screen.getByText('sandboxed-python')).toBeInTheDocument();
    expect(screen.getByText('$5')).toBeInTheDocument();
  });
});

describe('Deployments — editing one', () => {
  it('saves a title change without touching the target', async () => {
    getPlanJobs.mockImplementation(() => ok([JOB]));
    show();
    await waitFor(() => expect(screen.getByText('Nightly scout run')).toBeInTheDocument());

    fireEvent.click(screen.getByTitle('Edit'));
    const titleInput = screen.getByDisplayValue('Nightly scout run');
    fireEvent.change(titleInput, { target: { value: 'Nightly scout run v2' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(updatePlanJob).toHaveBeenCalled());
    expect(updatePlanJob.mock.calls[0][0]).toBe('job-1');
    expect(updatePlanJob.mock.calls[0][1].title).toBe('Nightly scout run v2');
  });

  it('pins the job to a stored agent version', async () => {
    getAgentVersions.mockImplementation(() => ok({
      agent_id: 'agent-1',
      versions: [{ version: 1, hash: 'h1' }, { version: 2, hash: 'h2' }],
    }));
    getPlanJobs.mockImplementation(() => ok([JOB]));
    show();
    await waitFor(() => expect(screen.getByText('Nightly scout run')).toBeInTheDocument());

    fireEvent.click(screen.getByTitle('Edit'));
    // Real I18nProvider is in play here (unlike the component-level tests,
    // which stub useI18n), so the aria-label is the actual English string.
    const select = await screen.findByLabelText('Agent version');
    fireEvent.change(select, { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(updatePlanJob).toHaveBeenCalled());
    expect(updatePlanJob.mock.calls[0][1].agent_version).toBe(2);
  });
});

describe('Deployments — actions', () => {
  it('runs a paused job now', async () => {
    getPlanJobs.mockImplementation(() => ok([JOB]));
    show();
    await waitFor(() => expect(screen.getByText('Nightly scout run')).toBeInTheDocument());

    fireEvent.click(screen.getByTitle('Run now'));
    await waitFor(() => expect(runPlanJobNow).toHaveBeenCalledWith('job-1'));
  });
});
