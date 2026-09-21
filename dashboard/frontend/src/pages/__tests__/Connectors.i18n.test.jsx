import { render, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import Connectors from '../Connectors';
import { I18nProvider } from '../../i18n';

// Telegram, Git and Blender used to be tabs on the Settings page, and this is
// the coverage that came with them: every tab, in every language, rendering its
// real body rather than a spinner, with no unresolved i18n key leaking through.
//
// It is also what proves the move itself: these components were cut out of a
// 1800-line page, so a lost import shows up here as a tab that will not render.

const ok = (data) => Promise.resolve({ data });

vi.mock('../../api', () => ({
  getTelegramConfig: () => ok({ enabled: false, has_token: false, allowed_users: [] }),
  updateTelegramConfig: () => ok({}),
  testTelegramToken: () => ok({ ok: true }),
  getTelegramStatus: () => ok({ running: false }),
  getTelegramBindings: () => ok([]),
  deleteTelegramBinding: () => ok({}),
  getGitConfig: () => ok({ github: { has_token: false }, gitlab: { has_token: false, base_url: '' } }),
  updateGitConfig: () => ok({}),
  testGitConnection: () => ok({ ok: true }),
  getBlenderConfig: () => ok({ enabled: false, binary_path: '', max_daemons: 2 }),
  updateBlenderConfig: () => ok({}),
  testBlenderBinary: () => ok({ ok: true, version: '4.2' }),
  getBlenderDaemons: () => ok({ daemons: [], running: 0, max_daemons: 2 }),
  stopBlenderDaemon: () => ok({}),
  stopAllBlenderDaemons: () => ok({}),
}));

const EVIDENCE = {
  ru: /[А-Яа-я]/,
  de: /\b(der|die|das|und|ein|eine|mit|für)\b/,
};

const TABS = ['Telegram', 'GitHub', 'Blender'];

function show() {
  return render(
    <I18nProvider><MemoryRouter><Connectors /></MemoryRouter></I18nProvider>,
  );
}

describe('Connectors', () => {
  beforeEach(() => localStorage.clear());

  for (const lang of ['en', 'ru', 'de']) {
    describe(lang, () => {
      for (const [index, tab] of TABS.entries()) {
        it(`renders the ${tab} connector with no unresolved keys`, async () => {
          localStorage.setItem('agents_hub_language', lang);
          const { container, unmount } = show();

          const buttons = container.querySelectorAll('nav ~ div button, button');
          // The tab strip is the first three buttons on the page.
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

  it('opens on Telegram, which is the one most installs configure', async () => {
    const { container, unmount } = show();
    await waitFor(() => expect(container.querySelector('section')).toBeTruthy());
    expect(container.textContent).toMatch(/Telegram/);
    unmount();
  });
});
