import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// Environments (Part D of the run-budget / firing-journal / environments /
// deployments contract): reusable execution profiles a task or node can run
// in. The backend (Part C) is written in parallel, so every call here is
// mocked against the contract's documented shapes rather than a live API.

const ok = (data) => Promise.resolve({ data });

const getEnvironments = vi.fn(() => ok([]));
const createEnvironment = vi.fn(() => ok({ id: 'env-new' }));
const updateEnvironment = vi.fn(() => ok({}));
const archiveEnvironment = vi.fn(() => ok({}));
const deleteEnvironment = vi.fn(() => ok({}));
const setDefaultEnvironment = vi.fn(() => ok({}));
const getEnvironmentUsage = vi.fn(() => ok({ nodes: [], jobs: [], runs: [] }));
const buildEnvironmentImage = vi.fn(() => ok({ ok: true, image: 'agents-hub-env:abc123' }));
// sandbox/registry.py's providers, fetched by the environment form to show
// availability hints next to the sandbox provider picker.
const getSandboxProviders = vi.fn(() => ok({
  docker: { available: true, reason: '' },
  local: { available: true, reason: '' },
  e2b: { available: false, reason: 'E2B_API_KEY is not set' },
  modal: { available: false, reason: 'MODAL_TOKEN_ID / MODAL_TOKEN_SECRET are not set' },
}));

vi.mock('../../api', () => ({
  getEnvironments: (...args) => getEnvironments(...args),
  createEnvironment: (...args) => createEnvironment(...args),
  updateEnvironment: (...args) => updateEnvironment(...args),
  archiveEnvironment: (...args) => archiveEnvironment(...args),
  deleteEnvironment: (...args) => deleteEnvironment(...args),
  setDefaultEnvironment: (...args) => setDefaultEnvironment(...args),
  getEnvironmentUsage: (...args) => getEnvironmentUsage(...args),
  buildEnvironmentImage: (...args) => buildEnvironmentImage(...args),
  getSandboxProviders: (...args) => getSandboxProviders(...args),
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ workspaceFilter: undefined, selectedWorkspace: 'default' }),
}));

import Environments from '../Environments';

const show = () => render(
  <I18nProvider><MemoryRouter><Environments /></MemoryRouter></I18nProvider>,
);

const ENV = {
  id: 'env-1',
  name: 'sandboxed-python',
  description: 'Isolated python for untrusted code',
  workspace: null,
  mode: 'docker',
  image: 'python:3.12-slim',
  packages: ['numpy'],
  network: { type: 'limited', allowed_hosts: ['pypi.org'], allow_package_managers: true },
  limits: { memory: '2g', cpus: '1.5', pids_limit: 256 },
  env: {},
  is_default: true,
  archived_at: null,
  created_at: '2026-09-20T10:00:00Z',
  updated_at: '2026-09-20T10:00:00Z',
  usage_counts: { instances: 2, jobs: 1 },
};

beforeEach(() => {
  getEnvironments.mockClear();
  createEnvironment.mockClear();
  updateEnvironment.mockClear();
  archiveEnvironment.mockClear();
  deleteEnvironment.mockClear();
  setDefaultEnvironment.mockClear();
  buildEnvironmentImage.mockClear();
  getSandboxProviders.mockClear();
  getEnvironments.mockImplementation(() => ok([]));
});

describe('Environments — empty state', () => {
  it('says there is nothing yet', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/no environments yet/i)).toBeInTheDocument());
  });
});

describe('Environments — the list', () => {
  it('renders an environment row with its mode, network and usage', async () => {
    getEnvironments.mockImplementation(() => ok([ENV]));
    show();

    await waitFor(() => expect(screen.getByText('sandboxed-python')).toBeInTheDocument());
    expect(screen.getByText(/docker container/i)).toBeInTheDocument();
    expect(screen.getByText(/limited/i)).toBeInTheDocument();
    expect(screen.getByText(/2 instances/i)).toBeInTheDocument();
    expect(screen.getByText(/default/i)).toBeInTheDocument();
  });

  it('disables delete while the environment is in use', async () => {
    getEnvironments.mockImplementation(() => ok([ENV]));
    show();
    await waitFor(() => expect(screen.getByText('sandboxed-python')).toBeInTheDocument());

    const deleteBtn = screen.getByTitle(/in use.*cannot delete/i);
    expect(deleteBtn).toBeDisabled();
  });

  it('builds the docker image on request', async () => {
    getEnvironments.mockImplementation(() => ok([ENV]));
    show();
    await waitFor(() => expect(screen.getByText('sandboxed-python')).toBeInTheDocument());

    fireEvent.click(screen.getByTitle('Build image'));
    await waitFor(() => expect(buildEnvironmentImage).toHaveBeenCalledWith('env-1'));
    await waitFor(() => expect(screen.getByText('agents-hub-env:abc123')).toBeInTheDocument());
  });
});

describe('Environments — creating one', () => {
  it('opens the form and creates a global environment', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/no environments yet/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /new environment/i }));
    expect(screen.getByPlaceholderText(/sandboxed-python/i)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/sandboxed-python/i), { target: { value: 'my-env' } });
    fireEvent.click(screen.getByRole('button', { name: /^create$/i }));

    await waitFor(() => expect(createEnvironment).toHaveBeenCalled());
    const payload = createEnvironment.mock.calls[0][0];
    expect(payload.name).toBe('my-env');
    expect(payload.workspace).toBeNull();
    expect(payload.mode).toBe('inherit');
    expect(payload.sandbox_provider).toBe('inherit');
    expect(payload.size).toBeNull();
  });

  it('picks a sandbox size preset', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/no environments yet/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /new environment/i }));
    fireEvent.change(screen.getByPlaceholderText(/sandboxed-python/i), { target: { value: 'sized-env' } });
    fireEvent.change(screen.getByLabelText(/sandbox size/i), { target: { value: 'medium' } });
    fireEvent.click(screen.getByRole('button', { name: /^create$/i }));

    await waitFor(() => expect(createEnvironment).toHaveBeenCalled());
    expect(createEnvironment.mock.calls[0][0].size).toBe('medium');
  });

  it('shows the environment size on its row', async () => {
    getEnvironments.mockImplementation(() => ok([{ ...ENV, size: 'large' }]));
    show();
    await waitFor(() => expect(screen.getByText('sandboxed-python')).toBeInTheDocument());
    expect(screen.getByText(/large/i)).toBeInTheDocument();
  });

  it('shows availability hints next to the sandbox provider picker', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/no environments yet/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /new environment/i }));
    await waitFor(() => expect(getSandboxProviders).toHaveBeenCalled());
    // Both e2b and modal are unavailable in the mocked response (no key).
    await waitFor(() => expect(screen.getAllByText(/unavailable/i).length).toBeGreaterThanOrEqual(2));
  });
});
