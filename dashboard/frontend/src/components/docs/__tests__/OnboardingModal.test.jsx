import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../../i18n';
import OnboardingModal, { ONBOARDING_SEEN_KEY } from '../OnboardingModal';

const navigate = vi.hoisted(() => vi.fn());
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

const demoApi = vi.hoisted(() => ({ getDemo: vi.fn(() => Promise.resolve({ data: { present: false } })) }));
vi.mock('../../../api/demo', () => demoApi);

const setupGuideApi = vi.hoisted(() => ({
  getSetupGuide: vi.fn(),
  setupGuideAction: vi.fn(),
}));
vi.mock('../../../api/setupGuide', () => setupGuideApi);

const INACTIVE = {
  active: false, started_at: null, finished_at: null, dismissed_at: null,
  mode: '', admin: true, multi: true, needs_model: false,
  steps: [], done: 0, total: 0, next: null, complete: false, work: null,
};

const renderModal = () => render(
  <I18nProvider><MemoryRouter><OnboardingModal /></MemoryRouter></I18nProvider>,
);

describe('OnboardingModal', () => {
  beforeEach(() => {
    localStorage.clear();
    navigate.mockReset();
    setupGuideApi.getSetupGuide.mockReset();
    setupGuideApi.setupGuideAction.mockReset();
  });

  it('shows nothing until the guide has loaded', () => {
    setupGuideApi.getSetupGuide.mockResolvedValue({ data: INACTIVE });
    const { container } = renderModal();
    expect(container.textContent).toBe('');
  });

  it('stays closed once dismissed before, whatever the guide says', async () => {
    localStorage.setItem(ONBOARDING_SEEN_KEY, '1');
    setupGuideApi.getSetupGuide.mockResolvedValue({ data: INACTIVE });
    const { container } = renderModal();
    await waitFor(() => expect(setupGuideApi.getSetupGuide).toHaveBeenCalled());
    expect(container.textContent).toBe('');
  });

  it('leaves a single operator to the first run', async () => {
    setupGuideApi.getSetupGuide.mockResolvedValue({ data: { ...INACTIVE, multi: false } });
    const { container } = renderModal();
    await waitFor(() => expect(setupGuideApi.getSetupGuide).toHaveBeenCalled());
    expect(container.textContent).toBe('');
  });

  it('does not open while the guide is already active', async () => {
    setupGuideApi.getSetupGuide.mockResolvedValue({ data: { ...INACTIVE, active: true } });
    const { container } = renderModal();
    await waitFor(() => expect(setupGuideApi.getSetupGuide).toHaveBeenCalled());
    expect(container.textContent).toBe('');
  });

  it('shows the FirstModelForm when the guide says a model is needed', async () => {
    setupGuideApi.getSetupGuide.mockResolvedValue({ data: { ...INACTIVE, needs_model: true } });
    renderModal();
    expect(await screen.findByTestId('first-model-form')).toBeTruthy();
    expect(screen.queryByText('Talk to the assistant')).toBeNull();
  });

  it('a non-administrator sees the admin-only line instead of the form', async () => {
    setupGuideApi.getSetupGuide.mockResolvedValue({ data: { ...INACTIVE, needs_model: true, admin: false } });
    renderModal();
    expect(await screen.findByTestId('first-model-admin-only')).toBeTruthy();
  });

  it('the two hand-over buttons start the guide and navigate to the assistant with state', async () => {
    setupGuideApi.getSetupGuide.mockResolvedValue({ data: INACTIVE });
    setupGuideApi.setupGuideAction.mockResolvedValue({ data: { ...INACTIVE, active: true, mode: 'voice' } });
    renderModal();
    fireEvent.click(await screen.findByText('Talk to the assistant'));
    await waitFor(() => expect(setupGuideApi.setupGuideAction).toHaveBeenCalledWith(
      'start', expect.objectContaining({ mode: 'voice' }),
    ));
    expect(navigate).toHaveBeenCalledWith('/assistant', { state: { setup: { mode: 'voice', kickoff: true } } });
  });

  it('the text button hands over in text mode', async () => {
    setupGuideApi.getSetupGuide.mockResolvedValue({ data: INACTIVE });
    setupGuideApi.setupGuideAction.mockResolvedValue({ data: { ...INACTIVE, active: true, mode: 'text' } });
    renderModal();
    fireEvent.click(await screen.findByText('Type to the assistant'));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/assistant', { state: { setup: { mode: 'text', kickoff: true } } }));
  });

  it('skip for now dismisses the modal without touching the guide', async () => {
    setupGuideApi.getSetupGuide.mockResolvedValue({ data: INACTIVE });
    renderModal();
    fireEvent.click(await screen.findByText('Skip'));
    expect(setupGuideApi.setupGuideAction).not.toHaveBeenCalled();
    expect(localStorage.getItem(ONBOARDING_SEEN_KEY)).toBe('1');
  });
});
