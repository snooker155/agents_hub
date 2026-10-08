import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect } from 'vitest';

import { I18nProvider } from '../../../i18n';
import {
  LiveWidget, RunsWidget, CostsWidget, LocalModelsWidget, EndpointWidget, ServicesWidget,
} from '../OverviewWidgets';

// The Dashboard's load and spend widgets, each fed one section of
// GET /api/stats/overview.

const wrap = (ui) => render(<I18nProvider><MemoryRouter>{ui}</MemoryRouter></I18nProvider>);

const days = (rows) => rows.map((r, i) => ({ date: `2026-10-0${i + 1}`, ...r }));

describe('OverviewWidgets', () => {
  it('shows the last day of runs and names a day on hover', () => {
    wrap(<RunsWidget runs={{
      days: 3,
      per_day: days([{ completed: 1, failed: 0, other: 0 }, { completed: 4, failed: 2, other: 0 }, { completed: 0, failed: 0, other: 0 }]),
      last_24h: { total: 6, completed: 4, failed: 2, success_rate: 66.7, avg_duration_ms: 2500,
        p95_duration_ms: 9000, channels: { chat: 5, voice: 1 } },
    }} />);
    expect(screen.getByText('66.7%')).toBeInTheDocument();
    expect(screen.getByText('2.5 s')).toBeInTheDocument();
    const chart = screen.getByTestId('dash-runs-chart');
    const columns = chart.querySelectorAll('.flex-1');
    fireEvent.mouseEnter(columns[1]);
    expect(chart.textContent).toContain('4');
    expect(chart.textContent).toContain('2');
  });

  it('shows spend, the budget bar and the costliest agents', () => {
    wrap(<CostsWidget costs={{
      today: 0.004, last_7d: 1.5, last_30d: 12.25, tokens_30d: 2_400_000, runs_30d: 40,
      per_day: days([{ cost: 0.5 }, { cost: 1.0 }]),
      top_agents: [{ key: 'visualizer', cost: 10, runs: 3 }, { key: 'main-agent', cost: 2.25, runs: 30 }],
      budget: { period: 'monthly', spend: 12.25, hard_limit_usd: 20, soft_limit_usd: 0 },
    }} />);
    expect(screen.getByText('< $0.01')).toBeInTheDocument();
    expect(screen.getByText('$12.25')).toBeInTheDocument();
    expect(screen.getByTestId('dash-budget')).toHaveTextContent('$20.00');
    expect(screen.getByText('visualizer')).toBeInTheDocument();
  });

  it('says when the local runtime is off, and lists its load when it runs', () => {
    const { unmount } = wrap(<LocalModelsWidget local={{ configured: true, ok: false, state: 'stopped', stopped_by_user: true }} />);
    expect(screen.getByTestId('dash-local-models')).toHaveTextContent(/stopped by hand/i);
    unmount();
    wrap(<LocalModelsWidget local={{
      configured: true, ok: true, state: 'running', models: 4,
      loaded: [{ name: 'qwen', kind: 'chat' }],
      memory: { ram_total_bytes: 64 * 2 ** 30, ram_available_bytes: 32 * 2 ** 30 },
      usage: { since: '2026-10-06T10:00:00Z', totals: { requests: 10, errors: 1, duration_ms: 5000 },
        by_source: { hub: 7, endpoint: 3 },
        top_models: [{ model: 'qwen', requests: 10, tokens_per_second: 42.5, avg_ms: 500 }] },
    }} />);
    expect(screen.getByText('1/4')).toBeInTheDocument();
    expect(screen.getByText('10 · 42.5 tok/s')).toBeInTheDocument();
    expect(screen.getByText('32 GB / 64 GB')).toBeInTheDocument();
  });

  it('shows /v1 calls or that nobody called it', () => {
    wrap(<EndpointWidget days={14} endpoint={{
      last_24h: { requests: 0, total_tokens: 0, errors: 0 }, window: { requests: 0 },
      per_day: [], top_models: [],
    }} />);
    expect(screen.getByTestId('dash-endpoint')).toHaveTextContent(/14 days/);
  });

  it('lists services with their replicas and turns', () => {
    wrap(<ServicesWidget services={{
      totals: { services: 2, active: 1, paused: 1, replicas_live: 1, turns_24h: 5, failed_24h: 0 },
      items: [{ service_id: 'svc_a', name: 'support', workspace: 'ws', paused: false, replicas_live: 1,
        replicas_max: 3, turns_24h: 5 }],
    }} />);
    expect(screen.getByText('support').closest('a')).toHaveAttribute('href', '/services/svc_a');
    expect(screen.getByText('1/3')).toBeInTheDocument();
  });

  it('shows a section error instead of numbers', () => {
    wrap(<ServicesWidget services={{ error: 'no table' }} />);
    expect(screen.getByTestId('dash-services')).toHaveTextContent('no table');
  });

  it('lists what runs now with links to each run, and counts the silent apart', () => {
    wrap(<LiveWidget generatedAt="2026-10-07T10:00:00Z" live={{
      total: 2, by_kind: { agent: 1, flow: 1 }, queue: { queued: 0 },
      items: [
        { kind: 'agent', run_id: 'r1', entity_id: 'a1', name: 'Writer', title: 'draft', status: 'running',
          started_at: '2026-10-07T09:58:00Z' },
        { kind: 'flow', run_id: 'f1', entity_id: 'flow-1', name: 'Nightly', status: 'running',
          started_at: '2026-10-07T09:59:30Z' },
      ],
      stale_total: 3, stale: [], recent: [],
    }} />);
    expect(screen.getByText('Writer').closest('a')).toHaveAttribute('href', '/messages/r1');
    expect(screen.getByText('Nightly').closest('a')).toHaveAttribute('href', '/flows/flow-1?run=f1');
    expect(screen.getByText('2 min')).toBeInTheDocument();
    expect(screen.getByTestId('dash-live-stale')).toHaveTextContent('3');
  });

  it('shows the runs that ended when nothing runs', () => {
    wrap(<LiveWidget generatedAt="2026-10-07T10:00:00Z" live={{
      total: 0, by_kind: {}, items: [], stale_total: 0, queue: {},
      recent: [{ kind: 'team', run_id: 't1', entity_id: 'team-1', name: 'Review', status: 'failed',
        duration_ms: 4200, finished_at: '2026-10-07T09:55:00Z' }],
    }} />);
    expect(screen.getByTestId('dash-live-recent')).toHaveTextContent('Review');
    expect(screen.getByText('Review').closest('a')).toHaveAttribute('href', '/teams/team-1?run=t1');
    expect(screen.queryByTestId('dash-live-stale')).toBeNull();
  });
});
