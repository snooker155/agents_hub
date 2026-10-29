import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The base image is pulled or built in the background
// (dashboard/backend/routes/containers.py): POST /containers/build-base
// returns at once and the page follows GET /containers/build-base. Holding the
// POST open for the whole build was cut by the proxy in front and surfaced as
// "Network Error". The page also used to call http://localhost:8000 directly,
// which on a server is the visitor's own machine; it now rides the shared
// client, mocked here as the api module's default export.

const ok = (data) => Promise.resolve({ data });

let buildStates = [];
const get = vi.fn((url) => {
  if (url === '/containers/agents-status') return ok({ agents: [] });
  if (url === '/containers') return ok({ containers: [] });
  if (url === '/containers/build-base') return ok(buildStates.length > 1 ? buildStates.shift() : buildStates[0]);
  return ok({});
});
const post = vi.fn(() => ok({ state: 'running' }));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ workspaceFilter: null }),
}));

vi.mock('../../components/stream', () => ({
  useChannel: () => {},
}));

vi.mock('../../api', () => ({
  default: {
    get: (...a) => get(...a),
    post: (...a) => post(...a),
    delete: () => ok({}),
  },
  getAgents: () => ok({ agents: [] }),
}));

import Containers from '../Containers';

const show = () => render(<I18nProvider><Containers /></I18nProvider>);

beforeEach(() => {
  get.mockClear();
  post.mockClear();
});

describe('Containers — base image build', () => {
  it('says nothing about an old finished build on open', async () => {
    buildStates = [{ state: 'failed', error: 'old failure', log: 'old log' }];
    show();
    await waitFor(() => expect(get).toHaveBeenCalledWith('/containers/build-base'));
    expect(screen.queryByText('old failure')).not.toBeInTheDocument();
    expect(screen.queryByText('old log')).not.toBeInTheDocument();
  });

  it('picks up a build already running and follows it to the end', async () => {
    buildStates = [
      { state: 'running', log: '$ docker pull ghcr.io/x/agents-hub-agents:0.12.0' },
      { state: 'done', log: 'Status: Downloaded newer image' },
    ];
    show();
    await waitFor(() => expect(screen.getByText(/docker pull ghcr/)).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('Status: Downloaded newer image')).toBeInTheDocument(),
      { timeout: 5000 });
  });

  it('starts a build with one short request and shows its failure', async () => {
    buildStates = [{ state: 'idle' }];
    show();
    await waitFor(() => expect(get).toHaveBeenCalledWith('/containers/build-base'));
    buildStates = [{ state: 'failed', error: 'no space left on device', log: 'step 3 broke' }];
    fireEvent.click(screen.getByText('Build base').closest('button'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/containers/build-base', { no_cache: false }));
    await waitFor(() => expect(screen.getByText('no space left on device')).toBeInTheDocument());
    expect(screen.getByText('step 3 broke')).toBeInTheDocument();
  });
});
