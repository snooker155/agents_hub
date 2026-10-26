import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const agentsApi = vi.hoisted(() => ({ getAgentToolVisibility: vi.fn() }));
vi.mock('../../api/agents', () => agentsApi);
const loopApi = vi.hoisted(() => ({ updateAgentLoopSettings: vi.fn() }));
vi.mock('../../api/agentLoop', () => loopApi);

import ToolVisibilityCard from '../agent/ToolVisibilityCard';

const t = (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k);

const body = (overrides = {}) => ({
  agent_id: 'a1', workspace: 'w1', mode: 'auto', threshold: 30, total: 41, search: true,
  visible: Array.from({ length: 30 }, (_, i) => `v${i}`),
  hidden: Array.from({ length: 11 }, (_, i) => `h${i}`),
  runtime: ['recall', 'get_skill'],
  always: ['ask_user'], named: ['read_file'], named_limit: 12,
  ...overrides,
});

describe('ToolVisibilityCard', () => {
  beforeEach(() => {
    agentsApi.getAgentToolVisibility.mockReset();
    loopApi.updateAgentLoopSettings.mockReset();
  });

  it('says how many tools the model sees, the threshold and what is behind search', async () => {
    agentsApi.getAgentToolVisibility.mockResolvedValue({ data: body() });
    render(<ToolVisibilityCard agentId="a1" workspace="w1" t={t} />);

    expect(await screen.findByText('agentLoop.toolSearch.visible {"visible":30,"total":41}')).toBeInTheDocument();
    expect(screen.getByText('agentLoop.toolSearch.threshold {"threshold":30}')).toBeInTheDocument();
    expect(screen.getByText('agentLoop.toolSearch.composition {"total":41,"picked":39,"runtime":2}')).toBeInTheDocument();
    expect(screen.getByText('agentLoop.toolSearch.breakdownAlways {"n":1,"limit":12}')).toBeInTheDocument();
    expect(screen.getByText('agentLoop.toolSearch.breakdownRuntime {"n":2,"limit":12}')).toBeInTheDocument();
    expect(screen.getByText('agentLoop.toolSearch.breakdownNamed {"n":1,"limit":12}')).toBeInTheDocument();
    const hiddenRow = screen.getByText('agentLoop.toolSearch.breakdownHidden {"n":11,"limit":12}');
    expect(agentsApi.getAgentToolVisibility).toHaveBeenCalledWith('a1', 'w1');

    // A row opens to name its tools; the service row also says why.
    fireEvent.click(hiddenRow);
    expect(screen.getByTitle('h0')).toBeInTheDocument();
    fireEvent.click(screen.getByText('agentLoop.toolSearch.breakdownAlways {"n":1,"limit":12}'));
    expect(screen.getByText('agentLoop.toolSearch.alwaysWhy')).toBeInTheDocument();
    expect(screen.getByTitle('ask_user')).toBeInTheDocument();
    expect(screen.queryByTitle('h0')).not.toBeInTheDocument();
  });

  it('says no search is needed when every tool is shown', async () => {
    agentsApi.getAgentToolVisibility.mockResolvedValue({
      data: body({ total: 12, search: false, visible: ['a'], hidden: [], runtime: [] }),
    });
    render(<ToolVisibilityCard agentId="a1" t={t} />);
    expect(await screen.findByText('agentLoop.toolSearch.notNeeded')).toBeInTheDocument();
    expect(screen.queryByText(/breakdownHidden/)).not.toBeInTheDocument();
  });

  it('saves the switch alone and counts again', async () => {
    agentsApi.getAgentToolVisibility
      .mockResolvedValueOnce({ data: body() })
      .mockResolvedValueOnce({ data: body({ mode: 'off', search: false, visible: [], hidden: [] }) });
    loopApi.updateAgentLoopSettings.mockResolvedValue({ data: {} });
    render(<ToolVisibilityCard agentId="a1" t={t} />);
    const select = await screen.findByLabelText('agentLoop.toolSearch.title');
    await waitFor(() => expect(select).not.toBeDisabled());

    fireEvent.change(select, { target: { value: 'off' } });
    await waitFor(() => expect(loopApi.updateAgentLoopSettings).toHaveBeenCalledWith('a1', { tool_search: false }));
    expect(await screen.findByText('agentLoop.toolSearch.searchOff')).toBeInTheDocument();
  });

  it('says so when the count cannot be made', async () => {
    agentsApi.getAgentToolVisibility.mockRejectedValue(new Error('boom'));
    render(<ToolVisibilityCard agentId="a1" t={t} />);
    expect(await screen.findByText('agentLoop.toolSearch.loadFailed')).toBeInTheDocument();
  });
});
