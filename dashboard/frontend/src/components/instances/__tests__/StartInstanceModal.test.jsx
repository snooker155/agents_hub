import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../../i18n';

// The Run button's form: the agent starts with the workspace the operator
// picked, and the page of the copy opens; a runner opens with the agent
// picked, since it
// answers for whichever agent a message names. A refusal from the backend is
// shown inside the form instead of being swallowed.

const ok = (data) => Promise.resolve({ data });

const startInstance = vi.fn(() => ok({ instance_id: 'inst_new1' }));
const getWorkspaces = vi.fn(() => ok([{ name: 'team', label: 'Team' }]));
const getAgents = vi.fn(() => ok([]));
const navigate = vi.fn();

vi.mock('../../../api', () => ({
  startInstance: (...a) => startInstance(...a),
  getWorkspaces: (...a) => getWorkspaces(...a),
  getAgents: (...a) => getAgents(...a),
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
  it('starts the agent with the chosen inputs and opens the copy', async () => {
    show();
    await waitFor(() => expect(getWorkspaces).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: /^run$/i }));

    await waitFor(() => expect(startInstance).toHaveBeenCalledTimes(1));
    expect(startInstance.mock.calls[0][0]).toEqual({
      agent_id: 'swe_agent', workspace: 'team',
    });
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/instances/inst_new1'));
  });

  it('opens a runner with the agent it was started for', async () => {
    startInstance.mockImplementationOnce(() => ok({ instance_id: 'inst_run1', kind: 'runner', for_agent: 'swe_agent' }));
    show();
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/instances/inst_run1?agent=swe_agent'));
  });

  it('shows the backend refusal inside the form', async () => {
    startInstance.mockImplementationOnce(() => Promise.reject({
      response: { data: { detail: "Agent 'swe_agent' is paused" } },
    }));
    show();
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
    expect(await screen.findByText(/is paused/)).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });
});
