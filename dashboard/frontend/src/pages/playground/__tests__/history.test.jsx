import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import { RunHistory, RunParams } from '../history';

const RUNS = [
  {
    sim_run_id: 'sim_2', scenario_id: 'scn_1', scenario_name: 'Market day',
    status: 'completed', stop_reason: 'max_ticks', ticks_done: 20,
    total_cost: 0.1234, scores: { pnl: 3.5 },
    started_at: '2026-01-02T10:00:00Z', finished_at: '2026-01-02T10:02:30Z',
  },
  {
    sim_run_id: 'sim_1', scenario_id: 'scn_1', scenario_name: 'Market day',
    status: 'failed', stop_reason: 'error', ticks_done: 3, total_cost: 0.01,
    scores: {}, error: 'provider refused the request',
    started_at: '2026-01-01T10:00:00Z', finished_at: '2026-01-01T10:00:20Z',
  },
];

// The launch snapshot: what the run was configured with, frozen when it
// started. The scenario it belongs to has since been retuned.
const SNAPSHOT = {
  scenario_id: 'scn_1', name: 'Market day', environment: 'market',
  activation: 'synchronous', max_ticks: 20, seed: 7, max_concurrent: 4,
  stall_timeout: 180, max_turn_seconds: 600, max_wall_seconds: 900,
  cost_ceiling: 2, default_model: 'claude-opus-5', env_params: { agents: 2 },
  roles: [{ agent_id: 'a', name: 'Alice', display_name: 'Alice', role: 'maker',
    goal: 'win the day', private_knowledge: 'the fix is in', memory_horizon: 8 }],
  updated_at: '2026-01-02T09:00:00Z',
};

const LIVE_SCENARIO = {
  ...SNAPSHOT, max_ticks: 50, seed: 99,
  roles: [...SNAPSHOT.roles, { agent_id: 'b', name: 'Bob', display_name: 'Bob' }],
  updated_at: '2026-01-03T09:00:00Z',
};

const wrap = (ui) => render(
  <MemoryRouter><I18nProvider>{ui}</I18nProvider></MemoryRouter>,
);

describe('run history', () => {
  it('shows what a run did, not just when it started', () => {
    wrap(<RunHistory runs={RUNS} maxTicks={20} selectedId="sim_2" />);
    // How far it got, why it ended, what it scored, what it cost — the four
    // things a picker of timestamps cannot say.
    expect(screen.getByText('tick 20 / 20')).toBeTruthy();
    expect(screen.getByText(/tick cap reached/i)).toBeTruthy();
    expect(screen.getByText('pnl 3.50')).toBeTruthy();
    expect(screen.getByText('$0.1234')).toBeTruthy();
    expect(screen.getByText('provider refused the request')).toBeTruthy();
  });

  it('marks the run already on screen', () => {
    wrap(<RunHistory runs={RUNS} maxTicks={20} selectedId="sim_2" />);
    expect(screen.getByText('showing')).toBeTruthy();
  });

  it('hands the whole run back when a row is picked', () => {
    const onSelect = vi.fn();
    wrap(<RunHistory runs={RUNS} maxTicks={20} onSelect={onSelect} />);
    fireEvent.click(screen.getAllByRole('button')[1]);
    expect(onSelect).toHaveBeenCalledWith(RUNS[1]);
  });

  it('names the scenario only where rows can come from several', () => {
    const { unmount } = wrap(<RunHistory runs={RUNS} showScenario />);
    expect(screen.getAllByText('Market day').length).toBe(2);
    unmount();
    wrap(<RunHistory runs={RUNS} />);
    expect(screen.queryByText('Market day')).toBeNull();
  });

  it('says so rather than rendering an empty list', () => {
    wrap(<RunHistory runs={[]} empty="No runs yet." />);
    expect(screen.getByText('No runs yet.')).toBeTruthy();
  });

  it('reads a run against its own settings, not the scenario as it is now', () => {
    // The scenario was retuned to 50 ticks after this run capped out at 20.
    // Showing "20 / 50" would describe a run that never existed.
    const snapped = [{ ...RUNS[0], config: SNAPSHOT }];
    wrap(<RunHistory runs={snapped} maxTicks={50} />);
    expect(screen.getByText('tick 20 / 20')).toBeTruthy();
    expect(screen.getByText(/1 character/)).toBeTruthy();
    expect(screen.getByText(/seed 7/)).toBeTruthy();
  });

  it('falls back to the live cap for runs recorded before snapshots', () => {
    wrap(<RunHistory runs={[RUNS[0]]} maxTicks={50} />);
    expect(screen.getByText('tick 20 / 50')).toBeTruthy();
  });

  it('flattens per-agent score dicts onto the row', () => {
    const nested = [{ ...RUNS[0], scores: { Trader: { items_held: 1, allies: 0 } } }];
    wrap(<RunHistory runs={nested} />);
    expect(screen.getByText('Trader items_held 1/allies 0')).toBeTruthy();
  });
});


describe('run parameters', () => {
  it('shows the settings the run had and warns that the scenario moved on', () => {
    wrap(<RunParams run={{ ...RUNS[0], config: SNAPSHOT }} scenario={LIVE_SCENARIO}
      defaultOpen />);
    expect(screen.getByText('20')).toBeTruthy();          // its own tick cap
    expect(screen.getByText('7')).toBeTruthy();           // its own seed
    expect(screen.getByText('Alice')).toBeTruthy();
    expect(screen.getByText('win the day')).toBeTruthy();
    // The secret each role held is the usual explanation for how a run came
    // out, so it is shown in full rather than as a "has one" badge.
    expect(screen.getByText('the fix is in')).toBeTruthy();
    expect(screen.queryByText('Bob')).toBeNull();         // added after the run
    expect(screen.getByText(/edited since this run/i)).toBeTruthy();
  });

  it('admits when a run predates snapshots instead of passing the live row off as history', () => {
    wrap(<RunParams run={RUNS[0]} scenario={LIVE_SCENARIO} defaultOpen />);
    expect(screen.getByText(/before runs kept their settings/i)).toBeTruthy();
    expect(screen.getByText('50')).toBeTruthy();          // the live cap, labelled as such
  });
});
