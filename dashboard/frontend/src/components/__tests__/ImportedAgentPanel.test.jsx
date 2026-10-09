import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  recheckImportedAgent: vi.fn(),
  refreshAgentTopology: vi.fn(),
}));
const dockerApi = vi.hoisted(() => ({
  getImportedAgentDocker: vi.fn(),
  setImportedAgentRuntimeMode: vi.fn(),
  buildImportedAgentImage: vi.fn(),
  startImportedAgentContainer: vi.fn(),
  stopImportedAgentContainer: vi.fn(),
}));
vi.mock('../../api', () => api);
vi.mock('../../api/agentImport', () => dockerApi);
vi.mock('../../i18n', () => ({ useI18n: () => ({ t: (k) => k }) }));
vi.mock('../GraphMirror', () => ({ default: () => null }));

import ImportedAgentPanel from '../ImportedAgentPanel';

const remote = (extra = {}) => ({
  url: 'http://localhost:8430',
  run_path: '/run',
  health_path: '/health',
  dockerfile: 'Dockerfile.agenthub',
  readiness: { runnable: true, summary: 'ok', checks: [] },
  ...extra,
});

describe('ImportedAgentPanel: where it runs', () => {
  beforeEach(() => {
    dockerApi.getImportedAgentDocker.mockResolvedValue({ data: {
      mode: 'docker', docker_available: true,
      image: { tag: 'agents-hub-import/claude-code:latest', exists: true },
      containers: [{ name: 'agents-hub-import-claude-code-w1', workspace: 'w1', state: 'running',
        url: 'http://127.0.0.1:49152', mounts: ['/hub/workspaces/w1'] }],
    } });
    dockerApi.setImportedAgentRuntimeMode.mockResolvedValue({ data: { mode: 'docker', report: { runnable: true, checks: [] } } });
    dockerApi.buildImportedAgentImage.mockResolvedValue({ data: { report: { runnable: true, checks: [] } } });
    dockerApi.stopImportedAgentContainer.mockResolvedValue({ data: { removed: [] } });
    dockerApi.startImportedAgentContainer.mockResolvedValue({ data: {} });
  });

  it('offers the choice only when the manifest ships a Dockerfile', () => {
    const { rerender } = render(<ImportedAgentPanel agent={{ id: 'a', remote: remote({ dockerfile: '' }) }} workspace="w1" />);
    expect(screen.queryByTestId('imported-runtime')).toBeNull();
    rerender(<ImportedAgentPanel agent={{ id: 'a', remote: remote() }} workspace="w1" />);
    expect(screen.getByTestId('imported-runtime')).toBeInTheDocument();
    expect(screen.getByLabelText(/importedAgentPanel.modeUrl/)).toBeChecked();
  });

  it('switching to docker saves the mode and builds the image at once', async () => {
    render(<ImportedAgentPanel agent={{ id: 'claude-code', remote: remote() }} workspace="w1" />);
    fireEvent.click(screen.getByLabelText(/importedAgentPanel.modeDocker/));
    await waitFor(() => expect(dockerApi.setImportedAgentRuntimeMode).toHaveBeenCalledWith('claude-code', 'docker'));
    await waitFor(() => expect(dockerApi.buildImportedAgentImage).toHaveBeenCalledWith('claude-code'));
    // the containers the daemon knows, with the workspace mount named
    expect(await screen.findByText('w1')).toBeInTheDocument();
    expect(screen.getByText(/\/hub\/workspaces\/w1/)).toBeInTheDocument();
    // the URL field no longer applies
    expect(screen.getByPlaceholderText('importedAgentPanel.modeDocker')).toBeDisabled();
  });

  it('in docker mode stops one workspace container and starts the current one', async () => {
    render(<ImportedAgentPanel agent={{ id: 'claude-code', remote: remote({ runtime_mode: 'docker' }) }} workspace="w2" />);
    expect(await screen.findByText('w1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /importedAgentPanel.stop$/ }));
    await waitFor(() => expect(dockerApi.stopImportedAgentContainer).toHaveBeenCalledWith('claude-code', 'w1'));
    fireEvent.click(screen.getByRole('button', { name: /importedAgentPanel.startForWorkspace/ }));
    await waitFor(() => expect(dockerApi.startImportedAgentContainer).toHaveBeenCalledWith('claude-code', 'w2'));
  });

  it('a re-check in docker mode does not send the empty URL', async () => {
    api.recheckImportedAgent.mockResolvedValue({ data: { report: { runnable: true, checks: [] } } });
    render(<ImportedAgentPanel agent={{ id: 'claude-code', remote: remote({ runtime_mode: 'docker' }) }} workspace="w1" />);
    fireEvent.click(screen.getByRole('button', { name: /re-check/i }));
    await waitFor(() => expect(api.recheckImportedAgent).toHaveBeenCalledWith('claude-code', { url: undefined, workspace: 'w1' }));
  });
});
