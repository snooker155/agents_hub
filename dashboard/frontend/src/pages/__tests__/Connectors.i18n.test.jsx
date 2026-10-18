import { fireEvent, render, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import Connectors from '../Connectors';
import { I18nProvider } from '../../i18n';
import { StreamContext } from '../../components/stream';
import { WorkspaceContext } from '../../components/workspace';

const silentStream = { connected: true, on: () => () => {}, acquireChannel: () => () => {}, onRefetch: () => () => {} };

// The connector tabs, in every language, each rendering its real body rather
// than a spinner, with no unresolved i18n key leaking through.

const ok = (data) => Promise.resolve({ data });

vi.mock('../../api', () => ({
  getGitConfig: () => ok({
    gitlab: { has_token: false, base_url: '' },
    sources: { gitlab: 'here' },
  }),
  updateGitConfig: () => ok({}),
  deleteGitConfig: () => ok({}),
  testGitConnection: () => ok({ ok: true }),
  getWorkspaces: () => ok([]),
  getAgents: () => ok([]),
  listFlows: () => ok([]),
  listConnectors: () => ok([{ name: 'jira', fields: [{ key: 'base_url', secret: false, kind: 'text', required: true, options: [] }] }]),
  getConnectorConfig: () => ok({ config: {}, configured: false, source: 'here' }),
  updateConnectorConfig: () => ok({}),
  deleteConnectorConfig: () => ok({}),
  testConnector: () => ok({ ok: true }),
}));

const EVIDENCE = {
  ru: /[А-Яа-я]/,
  de: /\b(der|die|das|und|ein|eine|mit|für)\b/,
};

// [label, index in the tab strip]: git, trackers, alert rules.
const TABS = [['GitLab', 0], ['Trackers', 1], ['Alert rules', 2]];

function show() {
  return render(
    <StreamContext.Provider value={silentStream}>
      {/* The channel binding form and the databases card read the selected workspace. */}
      <WorkspaceContext.Provider value={{ selectedWorkspace: 'default', liveUpdates: false }}>
        <I18nProvider><MemoryRouter><Connectors /></MemoryRouter></I18nProvider>
      </WorkspaceContext.Provider>
    </StreamContext.Provider>,
  );
}

describe('Connectors', () => {
  beforeEach(() => localStorage.clear());

  for (const lang of ['en', 'ru', 'de']) {
    describe(lang, () => {
      for (const [tab, index] of TABS) {
        it(`renders the ${tab} connector with no unresolved keys`, async () => {
          localStorage.setItem('agents_hub_language', lang);
          const { container, unmount } = show();

          const buttons = container.querySelectorAll('nav ~ div button, button');
          // The tab strip is the first buttons on the page, in TABS order.
          buttons[index].click();

          await waitFor(() => expect(container.querySelector('section')).toBeTruthy());
          const text = container.textContent;
          expect(text.length).toBeGreaterThan(120);
          expect(text).not.toMatch(/settings\.[a-zA-Z]/);
          expect(text).not.toMatch(/connectors\.[a-zA-Z]/);
          expect(text).not.toMatch(/common\.[a-zA-Z]/);
          if (EVIDENCE[lang]) expect(text).toMatch(EVIDENCE[lang]);
          unmount();
        });
      }
    });
  }

  it('opens on Git', async () => {
    const { container, unmount } = show();
    await waitFor(() => expect(container.querySelector('section')).toBeTruthy());
    expect(container.textContent).toMatch(/Git/);
    unmount();
  });

  it('names the workspace and the default-visibility rule in the page info', async () => {
    const { findByLabelText, findByTestId, unmount } = show();
    fireEvent.click(await findByLabelText('About this page'));
    const banner = await findByTestId('connectors-workspace-banner');
    expect(banner.textContent).toMatch(/default/i);
    expect(banner.textContent).toMatch(/everywhere/i);
    unmount();
  });

  it('names a non default workspace in the page info instead of "default"', async () => {
    const { findByLabelText, findByTestId, unmount } = render(
      <StreamContext.Provider value={silentStream}>
        <WorkspaceContext.Provider value={{ selectedWorkspace: 'acme', liveUpdates: false }}>
          <I18nProvider><MemoryRouter><Connectors /></MemoryRouter></I18nProvider>
        </WorkspaceContext.Provider>
      </StreamContext.Provider>,
    );
    fireEvent.click(await findByLabelText('About this page'));
    const banner = await findByTestId('connectors-workspace-banner');
    expect(banner.textContent).toMatch(/acme/);
    unmount();
  });
});
