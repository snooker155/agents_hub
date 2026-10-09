import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import process from 'node:process';

import { I18nProvider } from '../../i18n';
import en from '../../i18n/locales/en/health.js';
import ru from '../../i18n/locales/ru/health.js';
import de from '../../i18n/locales/de/health.js';

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
      id: 'disk_space', title: 'Disk space', status: 'warn', summary: 'Getting full.',
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
    const run = screen.getByRole('button', { name: 'Run' });
    // The button stays disabled while the mount load is in flight; the first
    // call above is made synchronously on mount, so on a slow runner the
    // mocked promise may not have settled yet and a click now would be
    // dropped. Wait for the load to finish before pressing it.
    await waitFor(() => expect(run).not.toBeDisabled());
    fireEvent.click(run);
    await waitFor(() => expect(getDoctor).toHaveBeenCalledTimes(2));
  });
});

// The doctor's checks as common/doctor.py sends them: English summary plus
// the message key the page translates by.
const DOCTOR_I18N = {
  status: 'warn',
  checked_at: '2026-09-24T10:00:00Z',
  checks: [
    {
      id: 'disk', title: 'Free disk', status: 'ok',
      summary: '3.0 GB free under the state directory.',
      summary_i18n: { key: 'disk.free', params: { gb: '3.0' } },
      detail: { free_bytes: 3221225472, path: '/srv/state' },
      doc: 'service-health', anchor: 'check-disk',
    },
    {
      id: 'stale_runs', title: 'Stale runs and leases', status: 'warn',
      summary: '2 run(s) have not beaten for over 180 s; expired lease(s): worker.',
      summary_i18n: { parts: [
        { key: 'staleRuns.stale', params: { count: 2, seconds: 180 } },
        { key: 'staleRuns.leases', params: { roles: 'worker' } },
      ] },
      detail: {}, doc: 'service-health', anchor: 'check-stale-runs',
    },
    {
      id: 'brand_new', title: 'Brand new check', status: 'ok',
      summary: 'A sentence this build has no key for.',
      summary_i18n: { key: 'brandNew.ok', params: {} },
      detail: { available: true }, doc: 'service-health', anchor: 'check-brand-new',
    },
  ],
};

describe('Health — Diagnostics in the reader\'s language', () => {
  beforeEach(() => {
    localStorage.setItem('agents_hub_language', 'ru');
    getDoctor.mockImplementation(() => ok(DOCTOR_I18N));
  });
  afterEach(() => localStorage.removeItem('agents_hub_language'));

  it('translates titles and summaries by their keys', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Свободное место на диске')).toBeInTheDocument());
    expect(screen.getByText('В каталоге состояния свободно 3.0 ГБ.')).toBeInTheDocument();
    expect(screen.getByText('Зависшие запуски и аренды')).toBeInTheDocument();
    // Two findings joined into one sentence, plural form picked by count.
    expect(screen.getByText('2 запуска молчат дольше 180 с; просроченные аренды: worker.'))
      .toBeInTheDocument();
  });

  it('keeps the English for a check this build has no translation for', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Brand new check')).toBeInTheDocument());
    expect(screen.getByText('A sentence this build has no key for.')).toBeInTheDocument();
  });

  it('translates detail labels and formats their values', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Свободное место на диске')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Свободное место на диске'));
    expect(screen.getByText('Свободно')).toHaveAttribute('title', 'free_bytes');
    expect(screen.getByText('3.0 GB')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Brand new check'));
    expect(screen.getByText('Доступен')).toBeInTheDocument();
    expect(screen.getByText('да')).toBeInTheDocument();
  });
});

// Every message key the doctor can send has a sentence in every locale, so a
// new check cannot quietly show English to a Russian or German reader.
describe('Health — doctor message keys', () => {
  // Vitest runs from dashboard/frontend.
  const source = readFileSync(resolve(process.cwd(), '../../common/doctor.py'), 'utf8');
  const keys = [...new Set([...source.matchAll(/Msg\(\s*"([\w.]+)"/g)].map((m) => m[1]))];
  const titleIds = [...source.matchAll(/^\s*\("(\w+)", "[^"]+", check_\w+\),$/gm)].map((m) => m[1]);

  const has = (dict, path) => {
    let node = dict;
    for (const part of path.split('.')) node = node?.[part];
    return typeof node === 'string';
  };

  it.each([['en', en], ['ru', ru], ['de', de]])('%s has every key', (_lang, dict) => {
    expect(keys.length).toBeGreaterThan(40);
    expect(titleIds.length).toBeGreaterThan(10);
    const summaries = dict.doctor.summaries;
    const missing = keys.filter((k) => !has(summaries, k) && !has(summaries, `${k}_other`));
    expect(missing).toEqual([]);
    expect(titleIds.filter((id) => !has(dict.doctor.titles, id))).toEqual([]);
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
