import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  getAgentToolPolicy: vi.fn(),
  updateAgentToolPolicy: vi.fn(),
  getToolPolicyDecisions: vi.fn(),
}));
vi.mock('../../api/toolPolicy', () => api);
vi.mock('../workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: 'w1' }) }));
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k) }),
}));

import ToolPolicyCard from '../agent/ToolPolicyCard';

const policyBody = (toolPolicy = {}) => ({
  agent_id: 'a1',
  workspace: 'w1',
  tool_policy: toolPolicy,
  workspace_policy: {},
  effective: [
    { tool: 'read_file', mode: 'always_allow', source: 'default' },
    { tool: 'run_shell', mode: 'always_ask', source: 'approval_list' },
  ],
  default: { mode: 'always_allow', source: 'default' },
  modes: ['always_allow', 'always_ask', 'auto'],
  gate_enabled: true,
  classifier_model: 'anthropic/claude-haiku',
});

describe('ToolPolicyCard', () => {
  beforeEach(() => {
    api.getAgentToolPolicy.mockReset();
    api.updateAgentToolPolicy.mockReset();
    api.getToolPolicyDecisions.mockReset();
    api.getAgentToolPolicy.mockResolvedValue({ data: policyBody() });
    api.updateAgentToolPolicy.mockResolvedValue({ data: policyBody({ run_shell: 'auto', '*': 'always_ask' }) });
    api.getToolPolicyDecisions.mockResolvedValue({
      data: { decisions: [{ id: 'd1', tool: 'run_shell', decision: 'deny', by: 'auto', reason: 'deletes the repo' }] },
    });
  });

  it('shows each tool with its effective mode and the recent decisions', async () => {
    render(<ToolPolicyCard agentId="a1" agent={{ id: 'a1' }} />);
    await screen.findByLabelText('toolPolicy.modeFor {"tool":"run_shell"}');
    expect(api.getAgentToolPolicy).toHaveBeenCalledWith('a1', 'w1');
    expect(api.getToolPolicyDecisions).toHaveBeenCalledWith({ agentId: 'a1', limit: 10 });
    expect(screen.getByText('deletes the repo')).toBeInTheDocument();
    expect(screen.getByText('toolPolicy.decision.deny')).toBeInTheDocument();
    expect(screen.getByText(/anthropic\/claude-haiku/)).toBeInTheDocument();
  });

  it('saves the per-tool mode and the default, then calls onSaved', async () => {
    const onSaved = vi.fn();
    render(<ToolPolicyCard agentId="a1" agent={{ id: 'a1' }} onSaved={onSaved} />);
    await screen.findByLabelText('toolPolicy.modeFor {"tool":"run_shell"}');

    const save = screen.getByRole('button', { name: 'toolPolicy.save' });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByLabelText('toolPolicy.modeFor {"tool":"run_shell"}'), { target: { value: 'auto' } });
    fireEvent.change(screen.getByLabelText('toolPolicy.defaultMode'), { target: { value: 'always_ask' } });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);

    await waitFor(() => expect(api.updateAgentToolPolicy).toHaveBeenCalledWith(
      'a1', { run_shell: 'auto', '*': 'always_ask' }, 'w1'));
    expect(await screen.findByText('toolPolicy.saved')).toBeInTheDocument();
    expect(onSaved).toHaveBeenCalled();
  });

  it('setting a tool back to inherit removes its entry', async () => {
    api.getAgentToolPolicy.mockResolvedValue({ data: policyBody({ run_shell: 'auto' }) });
    render(<ToolPolicyCard agentId="a1" agent={{ id: 'a1' }} />);
    await screen.findByLabelText('toolPolicy.modeFor {"tool":"run_shell"}');
    fireEvent.change(screen.getByLabelText('toolPolicy.modeFor {"tool":"run_shell"}'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'toolPolicy.save' }));
    await waitFor(() => expect(api.updateAgentToolPolicy).toHaveBeenCalledWith('a1', {}, 'w1'));
  });

  it('shows the refusal the backend sends back', async () => {
    api.updateAgentToolPolicy.mockRejectedValueOnce({ response: { data: { detail: 'Administrator access required' } } });
    render(<ToolPolicyCard agentId="a1" agent={{ id: 'a1' }} />);
    await screen.findByLabelText('toolPolicy.modeFor {"tool":"run_shell"}');
    fireEvent.change(screen.getByLabelText('toolPolicy.defaultMode'), { target: { value: 'auto' } });
    fireEvent.click(screen.getByRole('button', { name: 'toolPolicy.save' }));
    expect(await screen.findByText('Administrator access required')).toBeInTheDocument();
  });
});
