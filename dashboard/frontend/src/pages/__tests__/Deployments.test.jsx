import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// Deployments: agent_run/agent_task/flow/loop scheduled jobs with a firing
// journal and the resources a job hands to its runs. Every call is mocked against the
// documented shapes of routes/plan.py.

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
// The loops API answers { loops: [...] } and names a loop by loop_id.
const getLoops = vi.fn(() => ok({ loops: [{ loop_id: 'loop-1', name: 'Retry loop' }] }));
const getEnvironments = vi.fn(() => ok([{ id: 'env-1', name: 'sandboxed-python' }]));
// CronHint (under the recurrence field once it is not 'none') debounces a
// call to this; never resolving keeps every existing assertion exactly as
// it was before the hint existed, since nothing here asserts on it.
const previewCron = vi.fn(() => new Promise(() => {}));

vi.mock('../../api', () => ({
  // Project deployments (the apps table on top of the page): none in these tests.
  listDeployedApps: () => Promise.resolve({ data: { items: [] } }),
  previewCron: (...args) => previewCron(...args),
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
// The files API names a file by file_id (files/service.py).
const listWorkspaceFiles = vi.fn(() => ok({ files: [] }));
vi.mock('../../api/files', () => ({
  listWorkspaceFiles: (...args) => listWorkspaceFiles(...args),
  getWorkspaceFileRecord: vi.fn(() => Promise.resolve({ data: null })),
  formatBytes: (n) => `${n} B`,
  uploadWorkspaceFileObject: vi.fn(() => Promise.resolve({ data: { file_id: 'file_new', name: 'new.txt' } })),
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

  it('asks the jobs API for the deployment kinds only (agent_run, agent_task, flow, loop, heartbeat)', async () => {
    show();
    await waitFor(() => expect(getPlanJobs).toHaveBeenCalled());
    expect(getPlanJobs.mock.calls[0][2]).toEqual(['agent_run', 'agent_task', 'flow', 'loop', 'heartbeat']);
  });
});

describe('Deployments — a new agent run', () => {
  it('starts on an agent run, which needs its agent and takes resources', async () => {
    listWorkspaceFiles.mockImplementation((ws, { q } = {}) => ok({ files: [
      { file_id: 'file_1', name: 'leads.csv', size: 10, source: 'upload' },
      { file_id: 'file_2', name: 'notes.md', size: 20, source: 'upload' },
    ].filter((f) => !q || f.name.includes(q)) }));
    show();
    await waitFor(() => expect(getPlanJobs).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: /new deployment/i }));

    // The Plan form's picker; no orchestrator to fall back on: a run names its agent.
    const picker = screen.getByTestId('agent-picker');
    expect(picker).toHaveTextContent('Select an agent');
    fireEvent.click(picker);
    expect(screen.getByTestId('agent-picker-panel')).toBeInTheDocument();
    expect(document.querySelector('[data-agent-id=""]')).toBeNull();
    fireEvent.click(document.querySelector('[data-agent-id="agent-1"]'));
    expect(screen.getByTestId('agent-picker')).toHaveTextContent('Scout');
    expect(screen.getByText('What the agent is asked')).toBeInTheDocument();
    expect(screen.getByTestId('deployment-resources')).toBeInTheDocument();

    // Files come from the workspace file dialog, searched on the server.
    fireEvent.click(screen.getByRole('button', { name: /add from workspace files/i }));
    fireEvent.change(await screen.findByPlaceholderText('Search files'), { target: { value: 'leads' } });
    await waitFor(() => expect(listWorkspaceFiles).toHaveBeenLastCalledWith('default', { q: 'leads', limit: 200 }));
    await waitFor(() => expect(screen.queryByText('notes.md')).toBeNull());
    fireEvent.click(screen.getByText('leads.csv'));
    fireEvent.click(screen.getByRole('button', { name: 'Add 1 file' }));
    expect(screen.getByText(/Files\s*\(1\)/)).toBeInTheDocument();
    expect(screen.getByTitle('file_1')).toHaveTextContent('leads.csv');
  });
});

describe('Deployments — an agent run in the journal', () => {
  it('links a firing to the conversation it opened', async () => {
    getPlanJobs.mockImplementation(() => ok([{ ...JOB, kind: 'agent_run', created_task_ids: [] }]));
    getJobFires.mockImplementation(() => ok([{ id: 'f1', at: '2026-09-24T09:00:00.000Z', ok: true,
      trigger: 'tick', conversation_id: 'conv-7' }]));
    show();
    await waitFor(() => expect(screen.getByText('Nightly scout run')).toBeInTheDocument());

    fireEvent.click(screen.getByTitle('View firing journal'));
    const link = await screen.findByRole('link', { name: 'Open conversation' });
    expect(link).toHaveAttribute('href', '/chat/conv-7');
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

describe('Deployments — a loop job', () => {
  it('names the loop of a loop job and offers the loops by their loop_id', async () => {
    getPlanJobs.mockImplementation(() => ok([{ ...JOB, id: 'job-2', kind: 'loop', agent_id: null, loop_id: 'loop-1', title: 'Weekly polish' }]));
    show();
    await waitFor(() => expect(screen.getByText('Retry loop')).toBeInTheDocument());
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

  it('keeps the row buttons in place, disabled, while a firing runs', async () => {
    let finish;
    runPlanJobNow.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    getPlanJobs.mockImplementation(() => ok([JOB]));
    show();
    await waitFor(() => expect(screen.getByText('Nightly scout run')).toBeInTheDocument());

    fireEvent.click(screen.getByTitle('Run now'));
    // Still there, so the column keeps its width; hidden under the spinner.
    await waitFor(() => expect(screen.getByTitle('Delete')).toBeDisabled());
    expect(screen.getByTitle('Run now')).toHaveClass('invisible');
    finish({ data: {} });
    await waitFor(() => expect(screen.getByTitle('Delete')).not.toBeDisabled());
  });
});
