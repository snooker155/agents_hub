import { render, screen, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { I18nProvider } from '../../i18n';

// The base image card shows the base's version, commit and creation date, and
// each built agent image the base it was built on. An agent image whose base
// has changed since (`stale` from /containers/agents-status) runs the old
// code: its row is highlighted and offers a rebuild.

const ok = (data) => Promise.resolve({ data });

const BASE = {
  exists: true, image: 'agents-hub/base:latest', id: 'sha256:1e0ba831f89eb42f',
  created: '2026-11-01T19:40:48Z', version: '0.12.0',
  revision: '3a4a53b5c0ffee', source: 'v0.12.0-215-g3a4a53b5',
};

const AGENTS = [
  {
    agent_id: 'main-agent', agent_name: 'Main agent', image_tag: 'agents-hub/main-agent:latest',
    image_exists: true, base_exists: true, image_created: '2026-11-01T19:41:00Z',
    base_id: BASE.id, base_version: '0.12.0', base_revision: '3a4a53b5c0ffee',
    base_created: BASE.created, stale: false,
  },
  {
    agent_id: 'swe_agent', agent_name: 'SWE agent', image_tag: 'agents-hub/swe_agent:latest',
    image_exists: true, base_exists: true, image_created: '2026-10-27T18:58:38Z',
    base_id: 'sha256:0ld', base_version: '0.12.0', base_revision: 'e4ee60fa00',
    base_created: '2026-10-27T18:00:00Z', stale: true,
  },
  {
    agent_id: 'bean_scout', agent_name: 'Bean scout', image_tag: 'agents-hub/bean_scout:latest',
    image_exists: true, base_exists: true, image_created: '2026-10-27T18:58:38Z',
    base_id: '', stale: true,
  },
  {
    agent_id: 'writer', agent_name: 'Writer', image_tag: 'agents-hub/writer:latest',
    image_exists: false, base_exists: true,
  },
];

const post = vi.fn(() => ok({ log: 'built' }));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ workspaceFilter: null }),
}));

vi.mock('../../components/stream', () => ({
  useChannel: () => {},
}));

vi.mock('../../api', () => ({
  default: {
    get: (url) => {
      if (url === '/containers/agents-status') return ok({ agents: AGENTS, base: BASE });
      if (url === '/containers') return ok({ containers: [] });
      return ok({ state: 'idle' });
    },
    post: (...a) => post(...a),
    delete: () => ok({}),
  },
  getAgents: () => ok({ agents: [] }),
}));

import Containers from '../Containers';

const show = () => render(<I18nProvider><Containers /></I18nProvider>);

describe('Containers: base image version and outdated agent images', () => {
  it('shows the base image version, commit and source', async () => {
    show();
    const line = await screen.findByTestId('base-image-build');
    expect(line.textContent).toContain('version 0.12.0');
    expect(line.textContent).toContain('commit 3a4a53b5');
    expect(line.textContent).toContain('v0.12.0-215-g3a4a53b5');
    expect(line.textContent).toContain('2026');
  });

  it('marks an agent image on an older base as outdated and one on the current base as built', async () => {
    show();
    const stale = await screen.findByTestId('agent-image-swe_agent');
    expect(within(stale).getByText('Outdated')).toBeInTheDocument();
    expect(stale.className).toContain('bg-amber-50');
    expect(stale.textContent).toContain('on base version 0.12.0 · commit e4ee60fa');
    expect(within(stale).getByRole('button', { name: /Rebuild/ })).toBeInTheDocument();

    const current = screen.getByTestId('agent-image-main-agent');
    expect(within(current).getByText('Built')).toBeInTheDocument();
    expect(current.className).not.toContain('bg-amber-50');

    const unknown = screen.getByTestId('agent-image-bean_scout');
    expect(within(unknown).getByText('Outdated')).toBeInTheDocument();
    expect(unknown.textContent).toContain('on an unknown base');

    const notBuilt = screen.getByTestId('agent-image-writer');
    expect(within(notBuilt).getByText('Not built')).toBeInTheDocument();
    expect(within(notBuilt).getByRole('button', { name: 'Build' })).toBeInTheDocument();
  });

  it('counts the outdated images and rebuilds them all from one button', async () => {
    show();
    expect(await screen.findByText('3 / 4 · 2 outdated')).toBeInTheDocument();
    screen.getByRole('button', { name: 'Rebuild 2 outdated' }).click();
    await waitFor(() => expect(post).toHaveBeenCalledWith('/containers/build/bean_scout', { no_cache: false }));
    expect(post).toHaveBeenCalledWith('/containers/build/swe_agent', { no_cache: false });
    expect(post).not.toHaveBeenCalledWith('/containers/build/main-agent', expect.anything());
  });
});
