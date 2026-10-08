import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../../i18n';
import ReadySetCard from '../ReadySetCard';

// The Local tab's one-button path: the plan before, the job's steps while it runs.

const getReadySet = vi.fn();
const startReadySet = vi.fn();
const cancelReadySet = vi.fn();

vi.mock('../../../api/localModels', () => ({
  getReadySet: (...a) => getReadySet(...a),
  startReadySet: (...a) => startReadySet(...a),
  cancelReadySet: (...a) => cancelReadySet(...a),
}));

const STEP_IDS = ['runtime', 'engine', 'chat', 'load', 'hearing', 'voice', 'assign'];
const plan = (statuses = {}) => ({
  available: true,
  ready: false,
  default_would_change: true,
  chat: { id: 'qwen3-8b', label: 'Qwen3 8B', size_bytes: 5_030_000_000 },
  steps: STEP_IDS.map((id) => ({ id, label: id, status: statuses[id] || 'todo' })),
  job: null,
});

function renderCard() {
  return render(<I18nProvider><ReadySetCard onJobStarted={() => {}} /></I18nProvider>);
}

describe('ReadySetCard', () => {
  beforeEach(() => {
    getReadySet.mockReset();
    startReadySet.mockReset();
    cancelReadySet.mockReset();
  });

  it('shows the plan with the chosen model and starts the job', async () => {
    getReadySet.mockResolvedValue({ data: plan({ runtime: 'skipped' }) });
    startReadySet.mockResolvedValue({ data: { job: { id: 'j1' } } });
    renderCard();
    expect(await screen.findByText(/Qwen3 8B/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Set up everything' }));
    await waitFor(() => expect(startReadySet).toHaveBeenCalled());
  });

  it('follows a running job step by step and offers cancel', async () => {
    const steps = STEP_IDS.map((id, i) => ({
      id, label: id, percent: i === 2 ? 40 : 0, detail: i === 2 ? 'downloading Qwen3 8B' : '',
      status: i < 2 ? 'done' : i === 2 ? 'running' : 'todo',
    }));
    getReadySet.mockResolvedValue({ data: { ...plan(), job: { id: 'j1', status: 'running', meta: { steps } } } });
    cancelReadySet.mockResolvedValue({ data: {} });
    const { container } = renderCard();
    await screen.findByText('downloading Qwen3 8B');
    expect(container.querySelector('[data-step="chat"]').getAttribute('data-status')).toBe('running');
    expect(container.querySelector('[data-step="engine"]').getAttribute('data-status')).toBe('done');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(cancelReadySet).toHaveBeenCalled());
  });

  it('renders nothing without a runtime', async () => {
    getReadySet.mockResolvedValue({ data: { available: false, steps: [], job: null } });
    const { container } = renderCard();
    await waitFor(() => expect(getReadySet).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="ready-set"]')).toBeNull();
  });
});
