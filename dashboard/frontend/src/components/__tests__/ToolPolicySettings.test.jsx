import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  getWorkspacePolicy: vi.fn(),
  updateWorkspacePolicy: vi.fn(),
  getModelsCatalog: vi.fn(),
}));
vi.mock('../../api/toolPolicy', () => api);
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k) }),
}));

import ToolPolicySettings from '../settings/ToolPolicySettings';

describe('ToolPolicySettings', () => {
  beforeEach(() => {
    api.getWorkspacePolicy.mockReset();
    api.updateWorkspacePolicy.mockReset();
    api.getModelsCatalog.mockReset();
    api.getWorkspacePolicy.mockResolvedValue({
      data: { require_tool_approval: true, hooks: {}, tool_policy: { run_shell: 'always_ask' }, tool_policy_model: null },
    });
    api.getModelsCatalog.mockResolvedValue({
      data: {
        providers: {
          anthropic: { models: [{ id: 'claude-haiku', enabled: true }, { id: 'claude-old', enabled: false }] },
          openai: { models: [{ id: 'gpt-mini', enabled: true }] },
        },
      },
    });
    api.updateWorkspacePolicy.mockImplementation((ws, patch) => Promise.resolve({
      data: { require_tool_approval: true, hooks: {}, ...patch },
    }));
  });

  it('lists the overrides and offers only enabled catalog models', async () => {
    render(<ToolPolicySettings workspace="w1" />);
    await waitFor(() => expect(screen.getByText('run_shell')).toBeInTheDocument());
    expect(api.getWorkspacePolicy).toHaveBeenCalledWith('w1');
    await waitFor(() => expect(screen.getByRole('option', { name: 'anthropic/claude-haiku' })).toBeInTheDocument());
    expect(screen.getByRole('option', { name: 'openai/gpt-mini' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'anthropic/claude-old' })).toBeNull();
  });

  it('adds an override, sets the default and the model, and saves only its own keys', async () => {
    render(<ToolPolicySettings workspace="w1" />);
    await waitFor(() => expect(screen.getByText('run_shell')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole('option', { name: 'openai/gpt-mini' })).toBeInTheDocument());

    const input = screen.getByLabelText('toolPolicy.toolIdPlaceholder');
    fireEvent.change(input, { target: { value: 'delete_file' } });
    fireEvent.change(screen.getByLabelText('toolPolicy.newMode'), { target: { value: 'auto' } });
    fireEvent.submit(input.closest('form'));
    expect(screen.getByText('delete_file')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('toolPolicy.workspaceDefault'), { target: { value: 'always_allow' } });
    fireEvent.change(screen.getByLabelText('toolPolicy.model'), { target: { value: 'openai/gpt-mini' } });
    fireEvent.click(screen.getByRole('button', { name: /toolPolicy.saveWorkspace/ }));

    await waitFor(() => expect(api.updateWorkspacePolicy).toHaveBeenCalledWith('w1', {
      tool_policy: { run_shell: 'always_ask', delete_file: 'auto', '*': 'always_allow' },
      tool_policy_model: 'openai/gpt-mini',
    }));
    expect(await screen.findByText('toolPolicy.saved')).toBeInTheDocument();
  });

  it('removes an override and clears the model back to the agent\'s own', async () => {
    api.getWorkspacePolicy.mockResolvedValue({
      data: { tool_policy: { run_shell: 'always_ask' }, tool_policy_model: 'custom/tiny' },
    });
    render(<ToolPolicySettings workspace="w1" />);
    await waitFor(() => expect(screen.getByText('run_shell')).toBeInTheDocument());
    // A saved model the catalog no longer enables is still shown, not lost.
    expect(screen.getByRole('option', { name: 'custom/tiny' })).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('toolPolicy.removeFor {"tool":"run_shell"}'));
    fireEvent.change(screen.getByLabelText('toolPolicy.model'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /toolPolicy.saveWorkspace/ }));
    await waitFor(() => expect(api.updateWorkspacePolicy).toHaveBeenCalledWith('w1', {
      tool_policy: {}, tool_policy_model: null,
    }));
  });

  it('refuses an empty tool id', async () => {
    render(<ToolPolicySettings workspace="w1" />);
    await waitFor(() => expect(screen.getByText('run_shell')).toBeInTheDocument());
    fireEvent.submit(screen.getByLabelText('toolPolicy.toolIdPlaceholder').closest('form'));
    expect(await screen.findByText('toolPolicy.badToolId')).toBeInTheDocument();
  });

  it('shows the validation error the backend returns', async () => {
    api.updateWorkspacePolicy.mockRejectedValueOnce({ response: { data: { detail: 'tool_policy[x]: mode must be one of' } } });
    render(<ToolPolicySettings workspace="w1" />);
    await waitFor(() => expect(screen.getByText('run_shell')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('toolPolicy.workspaceDefault'), { target: { value: 'auto' } });
    fireEvent.click(screen.getByRole('button', { name: /toolPolicy.saveWorkspace/ }));
    expect(await screen.findByText('tool_policy[x]: mode must be one of')).toBeInTheDocument();
  });
});
