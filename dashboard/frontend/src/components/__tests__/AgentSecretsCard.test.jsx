import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  getAgentSecrets: vi.fn(),
  getWorkspaceSecrets: vi.fn(),
  updateAgentSecrets: vi.fn(),
}));
vi.mock('../../api', () => api);
vi.mock('../workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: 'w1' }) }));
vi.mock('../../i18n', () => ({ useI18n: () => ({ t: (k) => k }) }));

import AgentSecretsCard from '../agent/AgentSecretsCard';

describe('AgentSecretsCard', () => {
  beforeEach(() => {
    api.getAgentSecrets.mockResolvedValue({ data: { secrets: ['GITHUB_TOKEN'] } });
    api.getWorkspaceSecrets.mockResolvedValue({ data: [{ name: 'GITHUB_TOKEN' }, { name: 'SLACK_TOKEN' }] });
    api.updateAgentSecrets.mockResolvedValue({ data: { secrets: ['GITHUB_TOKEN', 'SLACK_TOKEN'] } });
  });

  it('shows the declared names and offers the workspace ones not yet declared', async () => {
    render(<AgentSecretsCard agentId="a1" />);
    await waitFor(() => expect(screen.getByText('GITHUB_TOKEN')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'SLACK_TOKEN' })).toBeInTheDocument();
  });

  it('adds a suggestion and saves the allowlist', async () => {
    render(<AgentSecretsCard agentId="a1" />);
    await waitFor(() => expect(screen.getByText('GITHUB_TOKEN')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'SLACK_TOKEN' }));
    fireEvent.click(screen.getByRole('button', { name: 'agentDetails.saveSecrets' }));
    await waitFor(() => expect(api.updateAgentSecrets).toHaveBeenCalledWith(
      'a1', ['GITHUB_TOKEN', 'SLACK_TOKEN'], { github_identity: 'app' }));
    expect(await screen.findByText('agentDetails.secretsSaved')).toBeInTheDocument();
  });

  it('refuses a name that is not an environment variable name', async () => {
    render(<AgentSecretsCard agentId="a1" />);
    await waitFor(() => expect(screen.getByText('GITHUB_TOKEN')).toBeInTheDocument());
    const input = screen.getByLabelText('agentDetails.secretsPlaceholder');
    fireEvent.change(input, { target: { value: 'bad name' } });
    fireEvent.submit(input.closest('form'));
    expect(await screen.findByText('agentDetails.secretsBadName')).toBeInTheDocument();
    expect(api.updateAgentSecrets).not.toHaveBeenCalled();
  });

  it('shows the guard refusal as it comes back', async () => {
    api.updateAgentSecrets.mockRejectedValueOnce({ response: { data: { detail: 'lethal trifecta' } } });
    render(<AgentSecretsCard agentId="a1" />);
    await waitFor(() => expect(screen.getByText('GITHUB_TOKEN')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'SLACK_TOKEN' }));
    fireEvent.click(screen.getByRole('button', { name: 'agentDetails.saveSecrets' }));
    expect(await screen.findByText('lethal trifecta')).toBeInTheDocument();
  });

  it('offers the GitHub identity choice only when GITHUB_TOKEN is declared', async () => {
    render(<AgentSecretsCard agentId="a1" />);
    await waitFor(() => expect(screen.getByText('GITHUB_TOKEN')).toBeInTheDocument());
    const select = screen.getByLabelText('agentDetails.githubIdentity');
    fireEvent.change(select, { target: { value: 'user' } });
    fireEvent.click(screen.getByRole('button', { name: 'agentDetails.saveSecrets' }));
    await waitFor(() => expect(api.updateAgentSecrets).toHaveBeenCalledWith(
      'a1', ['GITHUB_TOKEN'], { github_identity: 'user' }));
  });

  it('hides the GitHub identity choice without GITHUB_TOKEN', async () => {
    api.getAgentSecrets.mockResolvedValueOnce({ data: { secrets: ['SLACK_TOKEN'] } });
    render(<AgentSecretsCard agentId="a1" />);
    await waitFor(() => expect(screen.getByText('SLACK_TOKEN')).toBeInTheDocument());
    expect(screen.queryByLabelText('agentDetails.githubIdentity')).toBeNull();
  });
});
