import { render } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import Docs from '../Docs';
import OnboardingModal, { ONBOARDING_SEEN_KEY } from '../../components/docs/OnboardingModal';
import { I18nProvider } from '../../i18n';

// The Docs page used to mix translated strings with hardcoded English prose, so
// half of it kept its language when the switcher changed. These tests render
// every section in every language and fail on either symptom: an unresolved
// `docs.*` key leaking into the output, or no localised text at all.

vi.mock('../../api', () => ({
  getSystemHealth: () => Promise.resolve({ data: {} }),
  getSettings: () => Promise.resolve({ data: {} }),
  getWorkspaces: () => Promise.resolve({ data: [] }),
  getAgents: () => Promise.resolve({ data: [] }),
  testProvider: () => Promise.resolve({ data: { ok: true } }),
}));

const SECTIONS = [
  'getting-started', 'installation', 'concepts', 'features', 'chat', 'workspaces', 'projects', 'tasks',
  'plan', 'views', 'agents', 'importing-agents', 'marketplace', 'skills', 'orchestrator',
  'teams', 'flows', 'loops', 'tools', 'memory', 'web-logs', 'evals', 'playground',
  'instances', 'nodes', 'containers', 'models', 'costs', 'providers', 'connectors',
  'tutorials', 'cli', 'cli-api', 'faq',
];

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
          if (EVIDENCE[lang]) expect(text).toMatch(EVIDENCE[lang]);
          unmount();
        });
      }
    });
  }
});

describe('OnboardingModal', () => {
  beforeEach(() => localStorage.clear());

  it('opens on first launch and links to the Getting Started guide', () => {
    const { container, getByText } = render(
      <I18nProvider>
        <MemoryRouter><OnboardingModal /></MemoryRouter>
      </I18nProvider>,
    );
    expect(getByText('Open Getting Started')).toBeTruthy();
    expect(getByText('Open documentation')).toBeTruthy();
    expect(getByText('Skip')).toBeTruthy();
    expect(container.textContent).not.toMatch(/onboardingModal\./);
  });

  it('stays closed once dismissed', () => {
    localStorage.setItem(ONBOARDING_SEEN_KEY, '1');
    const { container } = render(
      <I18nProvider>
        <MemoryRouter><OnboardingModal /></MemoryRouter>
      </I18nProvider>,
    );
    expect(container.textContent).toBe('');
  });
});
