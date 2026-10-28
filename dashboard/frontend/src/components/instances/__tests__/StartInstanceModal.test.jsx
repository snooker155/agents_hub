import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../../i18n';

// The Run button's form: the agent, shown by its name, starts in a service
// (its own, else the workspace's runner) in the current workspace (no choice
// offered) with the environment the operator picked, and the page of the
// replica opens; a runner replica opens with the agent picked, since it
// answers for whichever agent a message names. A refusal from the backend
// (a paused service, one at its replica limit, an unknown environment) is
// shown inside the form instead of being swallowed.

const ok = (data) => Promise.resolve({ data });

const startInstance = vi.fn(() => ok({ instance_id: 'inst_new1' }));
const getWorkspaces = vi.fn(() => ok([{ name: 'team', label: 'Team' }]));
const getEnvironments = vi.fn(() => ok([{ id: 'env-1', name: 'Sandbox' }]));
const getAgents = vi.fn(() => ok([{ id: 'swe_agent', name: 'Software engineer' }]));
const getAgentVersions = vi.fn(() => ok({ versions: [], pinned_version: null, current_version: 4 }));
const navigate = vi.fn();

vi.mock('../../../api', () => ({
  startInstance: (...a) => startInstance(...a),
  getWorkspaces: (...a) => getWorkspaces(...a),
  getEnvironments: (...a) => getEnvironments(...a),
  getAgents: (...a) => getAgents(...a),
  getAgentVersions: (...a) => getAgentVersions(...a),
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate,
}));

vi.mock('../../workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'team' }),
}));

import StartInstanceModal from '../StartInstanceModal';

const show = (props = {}) => render(
  <I18nProvider>
    <StartInstanceModal open onClose={() => {}} agentId="swe_agent" {...props} />
  </I18nProvider>,
);

beforeEach(() => {
  startInstance.mockClear();
  navigate.mockClear();
});

describe('StartInstanceModal', () => {
  it('starts the agent in the current workspace with the chosen environment and opens the replica', async () => {
    show({ agentName: 'Software engineer' });
    await waitFor(() => expect(getEnvironments).toHaveBeenCalled());
    expect(screen.getByText('Software engineer')).toBeInTheDocument();
    expect(screen.queryByText('swe_agent')).not.toBeInTheDocument();
    expect(await screen.findByText('Team')).toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue('Workspace default'), { target: { value: 'env-1' } });
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }));

    await waitFor(() => expect(startInstance).toHaveBeenCalledTimes(1));
    expect(startInstance.mock.calls[0][0]).toEqual({
      agent_id: 'swe_agent', workspace: 'team', environment_id: 'env-1',
    });
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/instances/inst_new1'));
  });

  it('opens a runner replica with the agent it was started for', async () => {
    startInstance.mockImplementationOnce(() => ok({ instance_id: 'inst_run1', kind: 'runner', for_agent: 'swe_agent' }));
    show();
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/instances/inst_run1?agent=swe_agent'));
  });

  it('names a fixed agent from the agent list when no name is passed', async () => {
    show();
    expect(await screen.findByText('Software engineer')).toBeInTheDocument();
  });

  it('shows the version the run builds from', async () => {
    show({ agentName: 'Software engineer' });
    expect(await screen.findByText('Current (v4)')).toBeInTheDocument();
  });

  it('shows a pinned version', async () => {
    getAgentVersions.mockImplementationOnce(() => ok({ versions: [], pinned_version: 2, current_version: 4 }));
    show({ agentName: 'Software engineer' });
    expect(await screen.findByText('v2, pinned')).toBeInTheDocument();
  });

  it('shows the backend refusal inside the form', async () => {
    startInstance.mockImplementationOnce(() => Promise.reject({
      response: { data: { detail: "Service 'team runner' is paused" } },
    }));
    show();
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
    expect(await screen.findByText(/is paused/)).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });
});
