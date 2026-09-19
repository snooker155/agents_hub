import { render, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import Settings from '../Settings';
import { I18nProvider } from '../../i18n';

// The Settings page used to mix translated strings with hardcoded English
// ("Test connection", "Save token", the whole streaming explainer), so it only
// half-followed the language switcher. These tests open every section in every
// language and fail on either symptom: an unresolved `settings.*` / `common.*`
// key leaking into the output, or no localised text reaching the page at all.

const ok = (data) => Promise.resolve({ data });

vi.mock('axios', () => ({
  default: {
    create: () => ({
      get: () => ok({ env_defined_fields: [], backends: [], adapters: [] }),
      post: () => ok({ ok: true }),
      put: () => ok({}),
      delete: () => ok({}),
    }),
  },
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'default' }),
}));

vi.mock('../../api', () => ({
  getWorkspaceSettingsOverrides: () => ok({ overrides: {} }),
  updateWorkspaceSettingsOverrides: () => ok({}),
  getTelegramConfig: () => ok({ enabled: false, has_token: false, running: false }),
  updateTelegramConfig: () => ok({}),
  testTelegramToken: () => ok({ ok: true }),
  getTelegramStatus: () => ok({}),
  getTelegramBindings: () => ok([]),
  deleteTelegramBinding: () => ok({}),
  getGitConfig: () => ok({
    github: { has_token: false },
    gitlab: { has_token: false, base_url: 'https://gitlab.com' },
  }),
  updateGitConfig: () => ok({}),
  testGitConnection: () => ok({ ok: true }),
  getBlenderConfig: () => ok({ enabled: false, binary_path: '', availability: {} }),
  updateBlenderConfig: () => ok({}),
  testBlenderBinary: () => ok({ ok: true }),
  getBlenderDaemons: () => ok({ daemons: [], running: 0, max_daemons: 0 }),
  stopBlenderDaemon: () => ok({}),
  stopAllBlenderDaemons: () => ok({}),
  updateSettings: () => ok({}),
}));

const SECTIONS = [
  'providers', 'local', 'custom',
  'telegram', 'git', 'blender',
  'execution', 'rag', 'observability', 'logging',
];

// A language is "reaching the page" when its own script/function words show up.
const EVIDENCE = {
  ru: /[А-Яа-я]/,
  de: /\b(der|die|das|und|ein|eine|mit|für)\b/,
};

function renderAt(path) {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/settings/:section" element={<Settings />} /></Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe('Settings', () => {
  beforeEach(() => localStorage.clear());

  for (const lang of ['en', 'ru', 'de']) {
    describe(lang, () => {
      for (const id of SECTIONS) {
        it(`renders ${id} with no unresolved keys`, async () => {
          localStorage.setItem('agents_hub_language', lang);
          const { container, unmount } = renderAt(`/settings/${id}`);
          // Every section is built from SectionCards, and the connector ones
          // load async — waiting on the first <section> means the assertions
          // below read the body, not just the nav and a spinner.
          await waitFor(() => expect(container.querySelector('section')).toBeTruthy());
          const text = container.textContent;
          expect(text.length).toBeGreaterThan(200);
          expect(text).not.toMatch(/settings\.[a-zA-Z]/);
          expect(text).not.toMatch(/common\.[a-zA-Z]/);
          if (EVIDENCE[lang]) expect(text).toMatch(EVIDENCE[lang]);
          unmount();
        });
      }
    });
  }

  it('shows the workspace Save button only on workspace-scoped sections', async () => {
    const scoped = renderAt('/settings/providers');
    await waitFor(() => expect(scoped.container.textContent).toMatch(/Save "default" settings/));
    scoped.unmount();

    const connector = renderAt('/settings/telegram');
    await waitFor(() => expect(connector.container.textContent.length).toBeGreaterThan(200));
    expect(connector.container.textContent).not.toMatch(/Save "default" settings/);
    connector.unmount();
  });

  it('falls back to the first section when the URL names none', async () => {
    const { container, unmount } = render(
      <I18nProvider>
        <MemoryRouter initialEntries={['/settings']}>
          <Routes><Route path="/settings" element={<Settings />} /></Routes>
        </MemoryRouter>
      </I18nProvider>,
    );
    await waitFor(() => expect(container.textContent).toMatch(/Cloud providers/));
    unmount();
  });
});
