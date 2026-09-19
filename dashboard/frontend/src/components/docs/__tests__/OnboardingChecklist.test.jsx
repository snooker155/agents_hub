import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import OnboardingChecklist from '../OnboardingChecklist';
import { I18nProvider } from '../../../i18n';

// The checklist is the only thing in the app that probes whether the backend is
// up. When it is down, that has to be legible in the UI — otherwise the only
// evidence is a browser network/CORS error in the console.

const api = vi.hoisted(() => ({
  getSystemHealth: vi.fn(),
  getSettings: vi.fn(),
  getWorkspaces: vi.fn(),
  getAgents: vi.fn(),
  testProvider: vi.fn(),
}));
vi.mock('../../../api', () => api);

const renderChecklist = () => render(
  <I18nProvider>
    <MemoryRouter><OnboardingChecklist /></MemoryRouter>
  </I18nProvider>,
);

describe('OnboardingChecklist', () => {
  beforeEach(() => {
    localStorage.clear();
    api.getSettings.mockResolvedValue({ data: { default_provider: 'openai', openai_api_key_masked: 'sk-live' } });
    api.getWorkspaces.mockResolvedValue({ data: [{ name: 'default' }] });
    api.getAgents.mockResolvedValue({ data: [{ id: 'a' }] });
  });

  it('probes /api/health rather than the bare API root', async () => {
    api.getSystemHealth.mockResolvedValue({ data: { status: 'ok' } });
    renderChecklist();
    await waitFor(() => expect(api.getSystemHealth).toHaveBeenCalled());
    expect(await screen.findByText(/The API is reachable/)).toBeTruthy();
  });

  it('reports an unreachable backend instead of an unticked step', async () => {
    api.getSystemHealth.mockRejectedValue(new Error('Network Error'));
    renderChecklist();
    expect(await screen.findByText(/Can't reach the API/)).toBeTruthy();
    // A down backend must not trigger the follow-up probes.
    expect(api.getSettings).not.toHaveBeenCalled();
    expect(api.getWorkspaces).not.toHaveBeenCalled();
    expect(api.getAgents).not.toHaveBeenCalled();
  });
});
