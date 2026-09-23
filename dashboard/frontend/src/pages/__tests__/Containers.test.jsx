import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// Workers on other hosts register their containers in the shared `containers`
// table, and managers/container_manager.py's list_containers() merges them
// into this same list with `host` set to where they actually run and
// `remote: true` (docs/workers.md's "Containers across hosts"). This page
// must show where a container lives and must not offer Stop/Remove for one
// this replica has no daemon connection to, since those calls would 404
// against this host's own `docker ps`.

const ok = (data) => Promise.resolve({ data });

const CONTAINERS = [
  {
    id: 'abc123', name: 'agents-hub-agent-local', image: 'agents-hub/base:latest',
    status: 'Up 2 minutes', state: 'running', created: '', agent_id: 'writer',
    host: 'host-a',
  },
  {
    id: '', name: 'agents-hub-agent-remote', image: 'agents-hub/base:latest',
    status: 'running', state: 'running', created: '', agent_id: 'reviewer',
    host: 'host-b', remote: true,
  },
];

const get = vi.fn((url) => {
  if (url === '/api/containers/agents-status') return ok({ agents: [] });
  if (url === '/api/containers') return ok({ containers: CONTAINERS });
  return ok({});
});

vi.mock('axios', () => ({
  default: {
    create: () => ({
      get: (...a) => get(...a),
      post: () => ok({}),
      delete: () => ok({}),
    }),
  },
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ workspaceFilter: null }),
}));

vi.mock('../../components/stream', () => ({
  useChannel: () => {},
}));

vi.mock('../../api', () => ({
  getAgents: () => ok({ agents: [] }),
}));

import Containers from '../Containers';

const show = () => render(<I18nProvider><Containers /></I18nProvider>);

beforeEach(() => {
  get.mockClear();
});

describe('Containers — remote rows', () => {
  it('shows every container on the Containers tab, switching from the default Agent Images tab', async () => {
    show();
    await waitFor(() => expect(get).toHaveBeenCalledWith('/api/containers'));
    screen.getByText('Containers', { selector: 'button span' }).closest('button').click();
    await waitFor(() => expect(screen.getByText('agents-hub-agent-local')).toBeInTheDocument());
    expect(screen.getByText('agents-hub-agent-remote')).toBeInTheDocument();
  });

  it('badges a remote row with its host and a Remote marker', async () => {
    show();
    screen.getByText('Containers', { selector: 'button span' }).closest('button').click();
    await waitFor(() => expect(screen.getByText('agents-hub-agent-remote')).toBeInTheDocument());
    expect(screen.getByText('host-b')).toBeInTheDocument();
    expect(screen.getByText('Remote')).toBeInTheDocument();
    // The local row has a host badge too, but no Remote marker.
    expect(screen.getByText('host-a')).toBeInTheDocument();
  });

  it('disables Stop for a remote running container, with a tooltip explaining why', async () => {
    show();
    screen.getByText('Containers', { selector: 'button span' }).closest('button').click();
    await waitFor(() => expect(screen.getByText('agents-hub-agent-remote')).toBeInTheDocument());

    const remoteRow = screen.getByText('agents-hub-agent-remote').closest('div.flex.items-center.justify-between');
    const stopButton = remoteRow.querySelector('button[title*="cannot be stopped"]');
    expect(stopButton).toBeTruthy();
    expect(stopButton).toBeDisabled();

    const localRow = screen.getByText('agents-hub-agent-local').closest('div.flex.items-center.justify-between');
    const localStopButton = localRow.querySelector('button[title="Stop container"]');
    expect(localStopButton).toBeTruthy();
    expect(localStopButton).not.toBeDisabled();
  });
});
