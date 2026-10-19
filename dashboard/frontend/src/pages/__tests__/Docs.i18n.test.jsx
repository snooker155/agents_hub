import { render, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import Docs from '../Docs';
import { SECTIONS as REGISTRY } from '../../components/docs/sections';
import { I18nProvider } from '../../i18n';

// The Docs page used to mix translated strings with hardcoded English prose, so
// half of it kept its language when the switcher changed. These tests render
// every section in every language and fail on either symptom: an unresolved
// `docs.*` key leaking into the output, or no localised text at all.

const getDocMock = vi.fn((id) => Promise.resolve({ data: { id, title: id, content: `# ${id}\n\nReference text.` } }));

vi.mock('../../api', () => ({
  getSystemHealth: () => Promise.resolve({ data: {} }),
  getSettings: () => Promise.resolve({ data: {} }),
  getWorkspaces: () => Promise.resolve({ data: [] }),
  getAgents: () => Promise.resolve({ data: [] }),
  testProvider: () => Promise.resolve({ data: { ok: true } }),
  getDoc: (...a) => getDocMock(...a),
}));

// Every section the registry knows, so a section that is added or dropped there
// is covered (or stops being asked for) without editing this list.
const SECTIONS = REGISTRY.map((section) => section.id);

// A language is "reaching the page" when its own script/function words show up.
const EVIDENCE = {
  ru: /[А-Яа-я]/,
  de: /\b(der|die|das|und|ein|eine|mit|für)\b/,
};

function renderAt(path, element) {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/docs/:section" element={element} /></Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe('Docs', () => {
  beforeEach(() => localStorage.clear());

  for (const lang of ['en', 'ru', 'de']) {
    describe(lang, () => {
      for (const id of SECTIONS) {
        it(`renders ${id} with no unresolved keys`, () => {
          localStorage.setItem('agents_hub_language', lang);
          const { container, unmount } = renderAt(`/docs/${id}`, <Docs />);
          const text = container.textContent;
          expect(text.length).toBeGreaterThan(200);
          expect(text).not.toMatch(/docs\.[a-zA-Z]/);
          expect(text).not.toMatch(/docsGuide\./);
          if (EVIDENCE[lang]) expect(text).toMatch(EVIDENCE[lang]);
          unmount();
        });
      }
    });
  }
});

describe('Docs full reference language', () => {
  beforeEach(() => { localStorage.clear(); getDocMock.mockClear(); });

  it('asks for the page in the interface language', async () => {
    localStorage.setItem('agents_hub_language', 'ru');
    const { container } = renderAt('/docs/steering', <Docs />);
    const details = container.querySelector('details');
    details.open = true;
    fireEvent(details, new Event('toggle'));
    await waitFor(() => expect(getDocMock).toHaveBeenCalledWith('steering', 'ru'));
  });
});
