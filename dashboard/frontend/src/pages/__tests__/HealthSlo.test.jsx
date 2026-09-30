import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// Stream C's two additions to the Health page: the SLO card
// (GET /api/support/slo) and the "Download support bundle" button
// (GET /api/support/bundle, a blob). Both load independently of the plain
// health snapshot and of feature 3's doctor/system panels, so they get their
// own mocked endpoints here (same pattern as HealthDiagnostics.test.jsx).

const ok = (data, headers = {}) => Promise.resolve({ data, headers });

const SLO_OK = {
  status: 'ok', checked_at: '2026-09-25T10:00:00Z', window_seconds: 3600,
  objectives: {
    start_p95: { status: 'ok', value_seconds: 4.2, threshold_seconds: 30, sample: 40, window_seconds: 3600 },
    error_rate: { status: 'ok', value: 0.01, threshold: 0.05, sample: 40, window_seconds: 3600 },
  },
};

const SLO_BREACH = {
  status: 'breach', checked_at: '2026-09-25T10:05:00Z', window_seconds: 3600,
  objectives: {
    start_p95: { status: 'breach', value_seconds: 45.5, threshold_seconds: 30, sample: 40, window_seconds: 3600 },
    error_rate: { status: 'no_data', value: null, threshold: 0.05, sample: 3, window_seconds: 3600 },
  },
};

const getSlo = vi.fn();
const getSupportBundle = vi.fn();

vi.mock('../../api/support', () => ({
  getSlo: (...a) => getSlo(...a),
  getSupportBundle: (...a) => getSupportBundle(...a),
}));

const saveBlobAs = vi.fn();
vi.mock('../../api/files', () => ({ saveBlobAs: (...a) => saveBlobAs(...a) }));

const getDoctor = vi.fn(() => ok({ status: 'ok', checked_at: '2026-09-25T10:00:00Z', checks: [] }));
const getSystem = vi.fn(() => Promise.reject({ response: { status: 404 } }));

vi.mock('../../api/system', () => ({
  getDoctor: (...a) => getDoctor(...a),
  getSystem: (...a) => getSystem(...a),
  syncSystem: vi.fn(),
  setSystemSchedule: vi.fn(),
  pruneSystemBranches: vi.fn(),
  getSystemBranches: vi.fn(),
}));

vi.mock('../../api', () => ({
  getHealth: () => ok({ status: 'ok', database: {}, services: {}, storage: {}, providers: {} }),
  getServiceChat: () => ok({ messages: [] }),
  clearServiceChat: () => ok({}),
  stopServiceChat: () => ok({}),
  serviceChatUrl: () => '/health/chat',
}));

vi.mock('../../components/EntityChat', () => ({ default: () => null }));

import Health from '../Health';

const show = () => render(
  <I18nProvider><MemoryRouter><Health /></MemoryRouter></I18nProvider>,
);

beforeEach(() => {
  getSlo.mockReset();
  getSupportBundle.mockReset();
  saveBlobAs.mockReset();
  getSlo.mockImplementation(() => ok(SLO_OK));
});

describe('Health — SLO card', () => {
  it('loads the SLO objectives on mount and shows them ok', async () => {
    show();
    await waitFor(() => expect(getSlo).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText('SLO')).toBeInTheDocument());
    expect(screen.getByText('Run start time (p95)')).toBeInTheDocument();
    expect(screen.getByText('Error rate')).toBeInTheDocument();
    expect(screen.getAllByText('Ok').length).toBeGreaterThan(0);
  });

  it('shows a breach and a no_data objective distinctly', async () => {
    getSlo.mockImplementation(() => ok(SLO_BREACH));
    show();
    // The overall badge and the start_p95 row both read "Breach" (top status
    // is the worst of the two objectives), so this asserts at least one
    // rather than picking a single element.
    await waitFor(() => expect(screen.getAllByText('Breach').length).toBeGreaterThan(0));
    expect(screen.getByText('No data')).toBeInTheDocument();
  });

  it('refreshes on demand', async () => {
    show();
    await waitFor(() => expect(getSlo).toHaveBeenCalledTimes(1));
    const refreshButtons = screen.getAllByRole('button', { name: 'Refresh' });
    fireEvent.click(refreshButtons[refreshButtons.length - 1]);
    await waitFor(() => expect(getSlo).toHaveBeenCalledTimes(2));
  });
});

describe('Health — support bundle download', () => {
  it('downloads and saves the bundle, reading the filename off Content-Disposition', async () => {
    const blob = new Blob(['zip-bytes']);
    getSupportBundle.mockImplementation(() => ok(blob, {
      'content-disposition': 'attachment; filename="agents-hub-support-20260925T100000Z.zip"',
    }));
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Download support bundle' }));
    await waitFor(() => expect(getSupportBundle).toHaveBeenCalled());
    await waitFor(() => expect(saveBlobAs).toHaveBeenCalledWith(blob, 'agents-hub-support-20260925T100000Z.zip'));
  });

  it('falls back to a generic name when Content-Disposition is missing', async () => {
    const blob = new Blob(['zip-bytes']);
    getSupportBundle.mockImplementation(() => ok(blob, {}));
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Download support bundle' }));
    await waitFor(() => expect(saveBlobAs).toHaveBeenCalledWith(blob, 'agents-hub-support-bundle.zip'));
  });
});
