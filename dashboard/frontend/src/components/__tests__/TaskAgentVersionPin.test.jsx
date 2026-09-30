import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const versionsApi = vi.hoisted(() => ({
  getAgentVersions: vi.fn(),
  updateTask: vi.fn(),
}));
vi.mock('../../api/agentVersions', () => versionsApi);
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k) }),
}));

import TaskAgentVersionPin from '../task/TaskAgentVersionPin';

const versionsResponse = (versions) => ({ data: { agent_id: 'a1', versions } });

const V1 = { version: 1, hash: 'h1', created_at: '2026-09-01T00:00:00Z' };
const V2 = { version: 2, hash: 'h2', created_at: '2026-09-02T00:00:00Z' };

describe('TaskAgentVersionPin', () => {
  beforeEach(() => {
    versionsApi.getAgentVersions.mockResolvedValue(versionsResponse([V1, V2]));
    versionsApi.updateTask.mockResolvedValue({ data: { agent_version: null } });
  });
  afterEach(() => vi.restoreAllMocks());

  it('renders nothing without an assigned agent', () => {
    const { container } = render(
      <TaskAgentVersionPin task={{ id: 't1', assigned_agent_type: null, agent_version: null }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when the agent has no stored version yet', async () => {
    versionsApi.getAgentVersions.mockResolvedValue(versionsResponse([]));
    const { container } = render(
      <TaskAgentVersionPin task={{ id: 't1', assigned_agent_type: 'a1', agent_version: null }} />,
    );
    await waitFor(() => expect(versionsApi.getAgentVersions).toHaveBeenCalledWith('a1'));
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the live label when the task carries no pin', async () => {
    render(<TaskAgentVersionPin task={{ id: 't1', assigned_agent_type: 'a1', agent_version: null }} />);
    expect(await screen.findByTestId('task-agent-version-pin')).toBeInTheDocument();
    expect(screen.getByText('agentVersionPin.live {"version":2}')).toBeInTheDocument();
  });

  it('shows the pinned label when the task carries a pin', async () => {
    render(<TaskAgentVersionPin task={{ id: 't1', assigned_agent_type: 'a1', agent_version: 1 }} />);
    expect(await screen.findByText('agentVersionPin.pinned {"version":1}')).toBeInTheDocument();
  });

  it('pins the task to a chosen version and reports the change', async () => {
    const onChanged = vi.fn();
    render(
      <TaskAgentVersionPin
        task={{ id: 't1', assigned_agent_type: 'a1', agent_version: null }}
        onChanged={onChanged}
      />,
    );
    const select = await screen.findByLabelText('agentVersionPin.title');
    fireEvent.change(select, { target: { value: '1' } });

    await waitFor(() => expect(versionsApi.updateTask).toHaveBeenCalledWith('t1', { agent_version: 1 }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('clears the pin by selecting the live option', async () => {
    render(<TaskAgentVersionPin task={{ id: 't1', assigned_agent_type: 'a1', agent_version: 1 }} />);
    const select = await screen.findByLabelText('agentVersionPin.title');
    fireEvent.change(select, { target: { value: '' } });

    await waitFor(() => expect(versionsApi.updateTask).toHaveBeenCalledWith('t1', { agent_version: null }));
  });
});
