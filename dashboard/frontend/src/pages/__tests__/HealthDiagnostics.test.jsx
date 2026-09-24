import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// Feature 3's two new panels on the Health page: the doctor's structured self
// checks, and the system workspace (its clone, its scheduled loop, its
// branches). Both load independently of the plain up/down snapshot the rest
// of the page already had, so they get their own mocked endpoints here.

const ok = (data) => Promise.resolve({ data });
const notFound = () => Promise.reject({ response: { status: 404 } });

const DOCTOR = {
  status: 'warn',
  checked_at: '2026-09-24T10:00:00Z',
  checks: [
    {
      id: 'db', title: 'Database', status: 'ok', summary: 'Reachable.',
      detail: { latency_ms: 12 }, doc: 'service-health', anchor: 'check-db',
    },
    {
      id: 'disk', title: 'Disk space', status: 'warn', summary: 'Getting full.',
      detail: { free_gb: 5 }, doc: 'service-health', anchor: 'check-disk',
    },
  ],
};

const SYSTEM = {
  enabled: true,
  workspace: 'system',
  project_id: 'proj-1',
  repo_dir: '/srv/agents_hub',
  clone: {
    exists: true, head: 'a1b2c3d4e5f', branch: 'main',
    synced_at: '2026-09-24T09:00:00Z', error: null,
  },
  loop: {
    loop_id: 'loop-1', flow_id: 'flow-1', job_id: 'job-1', scheduled: false,
    recurrence: 'hours', every_hours: 24, next_run_at: null, last_run: null,
  },
  branches: [],
};

const getDoctor = vi.fn();
const getSystem = vi.fn();
const syncSystem = vi.fn(() => ok(SYSTEM.clone));
const setSystemSchedule = vi.fn(() => ok({ ...SYSTEM.loop, scheduled: true }));
const pruneSystemBranches = vi.fn(() => ok({ deleted: [] }));
const getSystemBranches = vi.fn(() => ok([]));

vi.mock('../../api/system', () => ({
  getDoctor: (...a) => getDoctor(...a),
  getSystem: (...a) => getSystem(...a),
  syncSystem: (...a) => syncSystem(...a),
  setSystemSchedule: (...a) => setSystemSchedule(...a),
  pruneSystemBranches: (...a) => pruneSystemBranches(...a),
  getSystemBranches: (...a) => getSystemBranches(...a),
}));

vi.mock('../../api', () => ({
  getHealth: () => ok({ status: 'ok', database: {}, services: {}, storage: {}, providers: {} }),
  getServiceChat: () => ok({ messages: [] }),
  clearServiceChat: () => ok({}),
  stopServiceChat: () => ok({}),
  serviceChatUrl: () => '/health/chat',
}));

// The chat column is a whole conversation UI with its own fetches; none of
// that is what this test is about, so it renders as nothing (same pattern as
// MemoryManagerBlocks.test.jsx).
vi.mock('../../components/EntityChat', () => ({ default: () => null }));

import Health from '../Health';

const show = () => render(
  <I18nProvider><MemoryRouter><Health /></MemoryRouter></I18nProvider>,
);

beforeEach(() => {
  getDoctor.mockClear();
  getSystem.mockClear();
  syncSystem.mockClear();
  setSystemSchedule.mockClear();
  pruneSystemBranches.mockClear();
  getSystemBranches.mockClear();
  getDoctor.mockImplementation(() => ok(DOCTOR));
  getSystem.mockImplementation(() => ok(SYSTEM));
});

describe('Health — Diagnostics', () => {
  it('loads the doctor on mount and renders its checks', async () => {
    show();
    await waitFor(() => expect(getDoctor).toHaveBeenCalled());
    // "Database" is ambiguous (the plain snapshot below has a card titled the
    // same), so assert on text unique to the doctor's own checks.
    await waitFor(() => expect(screen.getByText('Disk space')).toBeInTheDocument());
    expect(screen.getByText('Reachable.')).toBeInTheDocument();
    expect(screen.getByText('Getting full.')).toBeInTheDocument();
    // The overall badge reflects the top-level doctor.status (warn here).
    expect(screen.getAllByText(/needs attention/i).length).toBeGreaterThan(0);
  });

  it('links a check to its doc anchor', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Disk space')).toBeInTheDocument());
    const readMoreLinks = screen.getAllByText(/read more/i);
    expect(readMoreLinks[0].closest('a')).toHaveAttribute('href', '/docs/service-health#check-db');
  });

  it('re-runs the checks on Run', async () => {
    show();
    await waitFor(() => expect(getDoctor).toHaveBeenCalledTimes(1));
    // Exact name: "Prune" also contains the substring "run", so a /run/i
    // regex over-matches it.
    fireEvent.click(screen.getByRole('button', { name: 'Run' }));
    await waitFor(() => expect(getDoctor).toHaveBeenCalledTimes(2));
  });
});

describe('Health — System workspace', () => {
  it('shows the disabled message when the workspace is off', async () => {
    getSystem.mockImplementation(notFound);
    show();
    await waitFor(() => expect(getSystem).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/SYSTEM_WORKSPACE=false/)).toBeInTheDocument());
  });

  it('shows the clone and loop state when the workspace is on', async () => {
    show();
    await waitFor(() => expect(screen.getByText('main')).toBeInTheDocument());
    expect(screen.getByText('a1b2c3d')).toBeInTheDocument();
  });

  it('enabling the loop calls setSystemSchedule with the current interval', async () => {
    show();
    await waitFor(() => expect(screen.getByText('main')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /enable/i }));
    await waitFor(() => expect(setSystemSchedule).toHaveBeenCalledWith(true, 24));
  });

  it('prunes branches after a confirmation, using the entered day count', async () => {
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('7');
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    show();
    await waitFor(() => expect(screen.getByText('main')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /prune/i }));
    await waitFor(() => expect(pruneSystemBranches).toHaveBeenCalledWith(7));
    promptSpy.mockRestore();
    confirmSpy.mockRestore();
  });
});
