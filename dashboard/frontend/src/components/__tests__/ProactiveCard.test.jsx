import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getAgentProactive: vi.fn(),
  updateAgentProactive: vi.fn(),
  pauseAgentProactive: vi.fn(),
  resumeAgentProactive: vi.fn(),
  wakeAgentProactive: vi.fn(),
}));
vi.mock('../../api/proactive', async (importOriginal) => ({ ...(await importOriginal()), ...api }));
vi.mock('../../api/watchers', () => ({ getWatchers: vi.fn(() => Promise.resolve({ data: [] })) }));
// CronHint, under the schedule field, debounces a call to this; never
// resolving keeps every existing assertion unchanged, since none of them
// look at the hint.
vi.mock('../../api', () => ({ previewCron: vi.fn(() => new Promise(() => {})) }));
vi.mock('../workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: 'default' }) }));
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k) }),
}));

import ProactiveCard from '../agent/ProactiveCard';
import { collapseTicks } from '../../api/proactive';

const profile = (overrides = {}) => ({
  enabled: false, interval_minutes: 60, cron: '', timezone: 'UTC',
  quiet_hours: { from: '', to: '' }, daily_budget_usd: 0, max_runs_per_day: 0,
  tick_budget_usd: null, environment_id: null, brief: '', triggers: [], notify: ['dashboard'],
  workspace: null, auto_pause_after: 3, job_id: null, ...overrides,
});

const body = (overrides = {}) => ({
  agent_id: 'watcher', profile: profile(), schedule: 'every 60 minutes', job: null, usage: null, ticks: [], ...overrides,
});

const job = (overrides = {}) => ({
  id: 'j1', kind: 'heartbeat', status: 'scheduled', paused_reason: null,
  run_at: '2026-10-02T12:00:00+00:00', cron: '*/15 * * * *', ...overrides,
});

describe('ProactiveCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getAgentProactive.mockResolvedValue({ data: body() });
    api.updateAgentProactive.mockImplementation(async () => ({ data: body() }));
  });

  it('shows the off state and no feed when there is no pulse yet', async () => {
    render(<ProactiveCard agentId="watcher" />);
    await waitFor(() => expect(screen.getByText('proactive.state.off')).toBeInTheDocument());
    expect(screen.queryByTestId('tick-feed')).toBeNull();
    expect(screen.queryByText('proactive.pause')).toBeNull();
  });

  it('saves the profile with the schedule, quiet hours and channels', async () => {
    render(<ProactiveCard agentId="watcher" />);
    await waitFor(() => expect(screen.getByLabelText('proactive.brief.title')).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText('proactive.enabled'));
    fireEvent.change(screen.getByLabelText('proactive.brief.title'), { target: { value: 'watch the inbox' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'proactive.schedule.interval' }), { target: { value: '15' } });
    fireEvent.change(screen.getByLabelText('proactive.quietHours.from'), { target: { value: '22:00' } });
    fireEvent.change(screen.getByLabelText('proactive.quietHours.to'), { target: { value: '07:00' } });
    fireEvent.click(screen.getByLabelText('proactive.notify.telegram'));
    fireEvent.click(screen.getByText('proactive.save'));

    await waitFor(() => expect(api.updateAgentProactive).toHaveBeenCalledTimes(1));
    const [, patch] = api.updateAgentProactive.mock.calls[0];
    expect(patch).toMatchObject({
      enabled: true, interval_minutes: 15, cron: '', brief: 'watch the inbox',
      quiet_hours: { from: '22:00', to: '07:00' }, notify: ['dashboard', 'telegram'],
    });
  });

  it('shows the live state with usage, and pause, resume and wake act on it', async () => {
    api.getAgentProactive.mockResolvedValue({ data: body({
      profile: profile({ enabled: true, job_id: 'j1', daily_budget_usd: 2 }),
      job: job(),
      usage: { runs: 3, spent_usd: 0.42, max_runs_per_day: 0, daily_budget_usd: 2 },
    }) });
    api.pauseAgentProactive.mockResolvedValue({ data: body({ job: job({ status: 'paused', paused_reason: 'manual' }) }) });
    api.resumeAgentProactive.mockResolvedValue({ data: body({ job: job() }) });
    api.wakeAgentProactive.mockResolvedValue({ data: { ...body({ job: job() }), result: { ok: true, task_id: 't9' } } });

    render(<ProactiveCard agentId="watcher" />);
    await waitFor(() => expect(screen.getByTestId('pulse-status')).toHaveTextContent('proactive.state.on'));
    expect(screen.getByTestId('pulse-usage')).toHaveTextContent('"runs":3');
    expect(screen.getByTestId('pulse-usage')).toHaveTextContent('"spent":"0.42"');

    fireEvent.click(screen.getByText('proactive.pause'));
    await waitFor(() => expect(api.pauseAgentProactive).toHaveBeenCalledWith('watcher'));
    await waitFor(() => expect(screen.getByTestId('pulse-status')).toHaveTextContent('paused'));

    fireEvent.click(screen.getByText('proactive.resume'));
    await waitFor(() => expect(api.resumeAgentProactive).toHaveBeenCalledWith('watcher'));

    fireEvent.click(screen.getByText('proactive.wake'));
    await waitFor(() => expect(api.wakeAgentProactive).toHaveBeenCalledWith('watcher'));
    await waitFor(() => expect(screen.getByText('proactive.wokeStarted')).toBeInTheDocument());
  });

  it('renders the tick feed with quiet ticks folded into one row', async () => {
    api.getAgentProactive.mockResolvedValue({ data: body({
      profile: profile({ enabled: true, job_id: 'j1' }),
      job: job(),
      usage: { runs: 4, spent_usd: 0, max_runs_per_day: 0, daily_budget_usd: 0 },
      ticks: [
        { id: 'f4', at: '2026-10-02T11:00:00Z', outcome: 'acted', summary: 'Drafted two answers', next_check: 'row 14', cost_usd: 0.0123, task_id: 't4', trigger: 'schedule' },
        { id: 'f3', at: '2026-10-02T10:00:00Z', outcome: 'quiet', summary: 'nothing', task_id: 't3' },
        { id: 'f2', at: '2026-10-02T09:00:00Z', outcome: 'quiet', summary: 'nothing', task_id: 't2' },
        { id: 'f1', at: '2026-10-02T08:00:00Z', outcome: null, task_id: 't1' },
      ],
    }) });

    render(<ProactiveCard agentId="watcher" />);
    await waitFor(() => expect(screen.getByTestId('tick-feed')).toBeInTheDocument());
    const rows = screen.getByTestId('tick-feed').querySelectorAll('li');
    expect(rows).toHaveLength(3);
    expect(screen.getByText('Drafted two answers')).toBeInTheDocument();
    expect(screen.getByText(/proactive.feed.group.quiet \{"count":2\}/)).toBeInTheDocument();
    expect(screen.getByText('proactive.outcome.running')).toBeInTheDocument();
    expect(screen.getByText(/proactive.feed.nextCheck/)).toHaveTextContent('row 14');
  });
});

describe('collapseTicks', () => {
  it('folds runs of the same skip reason and keeps acted ticks apart', () => {
    const out = collapseTicks([
      { id: 'a', outcome: 'quiet', at: '3' },
      { id: 'b', outcome: 'quiet', at: '2' },
      { id: 'c', outcome: 'budget', at: '1b' },
      { id: 'd', outcome: 'acted', at: '1' },
      { id: 'e', outcome: 'quiet', at: '0' },
    ]);
    expect(out.map((r) => [r.group, r.outcome, r.count])).toEqual([
      [true, 'quiet', 2], [true, 'budget', 1], [false, 'acted', undefined], [true, 'quiet', 1],
    ]);
    expect(out[0].until).toBe('2');
  });
});
