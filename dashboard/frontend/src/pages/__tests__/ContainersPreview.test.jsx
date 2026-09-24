import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// Feature 7a: a "Preview" button on the Containers page opens a container's
// web page inside the hub through the authenticated ticket proxy
// (dashboard/backend/routes/preview.py), not as a direct iframe. The button
// only makes sense for a container that is both running and has something
// exposed (http_url), and the resulting iframe must never be given
// allow-same-origin: that is what keeps the previewed page from reading the
// dashboard's own tokens out of localStorage.

const ok = (data) => Promise.resolve({ data });

const CONTAINERS = [
  {
    id: 'abc123', name: 'agents-hub-agent-web', image: 'agents-hub/base:latest',
    status: 'Up 2 minutes', state: 'running', created: '', agent_id: 'writer',
    host: 'host-a', http_url: 'http://localhost:5055', http_expose: true,
  },
  {
    id: 'def456', name: 'agents-hub-agent-headless', image: 'agents-hub/base:latest',
    status: 'Up 2 minutes', state: 'running', created: '', agent_id: 'reviewer',
    host: 'host-a',
  },
  {
    id: 'ghi789', name: 'agents-hub-agent-stopped', image: 'agents-hub/base:latest',
    status: 'Exited', state: 'exited', created: '', agent_id: 'planner',
    host: 'host-a', http_url: 'http://localhost:5056', http_expose: true,
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

const mintPreviewTicket = vi.fn(() => ok({ url: '/preview/tkt-123abc/', expires_in: 3600 }));
vi.mock('../../api/preview', () => ({
  mintPreviewTicket: (...a) => mintPreviewTicket(...a),
}));

import Containers from '../Containers';

const show = () => render(<I18nProvider><Containers /></I18nProvider>);

const goToContainersTab = async () => {
  screen.getByText('Containers', { selector: 'button span' }).closest('button').click();
  await waitFor(() => expect(screen.getByText('agents-hub-agent-web')).toBeInTheDocument());
};

beforeEach(() => {
  get.mockClear();
  mintPreviewTicket.mockClear();
});

describe('Containers, preview through the hub', () => {
  it('offers Preview only for a running container that exposes an http_url', async () => {
    show();
    await goToContainersTab();

    const webRow = screen.getByText('agents-hub-agent-web').closest('div.py-3');
    expect(webRow.querySelector('button[title="Preview"]')).toBeTruthy();

    const headlessRow = screen.getByText('agents-hub-agent-headless').closest('div.py-3');
    expect(headlessRow.querySelector('button[title="Preview"]')).toBeNull();

    const stoppedRow = screen.getByText('agents-hub-agent-stopped').closest('div.py-3');
    expect(stoppedRow.querySelector('button[title="Preview"]')).toBeNull();
  });

  it('mints a ticket and renders a sandboxed iframe with no allow-same-origin', async () => {
    show();
    await goToContainersTab();

    const webRow = screen.getByText('agents-hub-agent-web').closest('div.py-3');
    webRow.querySelector('button[title="Preview"]').click();

    await waitFor(() => expect(mintPreviewTicket).toHaveBeenCalledWith(
      { kind: 'container', name: 'agents-hub-agent-web' },
    ));

    const iframe = await waitFor(() => {
      const el = webRow.querySelector('iframe');
      expect(el).toBeTruthy();
      return el;
    });
    expect(iframe.getAttribute('src')).toMatch(/^\/preview\//);
    expect(iframe.getAttribute('sandbox')).not.toMatch(/allow-same-origin/);
    expect(iframe.getAttribute('sandbox')).toMatch(/allow-scripts/);
  });
});
