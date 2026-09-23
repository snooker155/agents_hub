import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { I18nProvider } from '../../../i18n';
import { RunMeters } from '../run-meters';

const wrap = (ui) => render(<I18nProvider>{ui}</I18nProvider>);

// A finished run with its own launch snapshot — the cap and cast it actually
// ran with, not whatever the scenario has been retuned to since.
const RUN = {
  sim_run_id: 'run_1',
  status: 'completed',
  activation: 'synchronous',
  ticks_done: 2,
  total_cost: 0.1234,
  stop_reason: 'max_ticks',
  config: { max_ticks: 5, roles: [{ name: 'a' }, { name: 'b' }] },
};

const SCENARIO = { max_ticks: 10, roles: [{ name: 'a' }, { name: 'b' }, { name: 'c' }] };

const TICKS = [
  { tick: 0, decisions: [
    { inbound_tokens: 10, outbound_tokens: 5 },
    { inbound_tokens: 3, outbound_tokens: 2 },
  ] },
  { tick: 1, decisions: [{ inbound_tokens: 1, outbound_tokens: 1 }] },
];

describe('RunMeters', () => {
  it('reads the run against its own launch snapshot, not the live scenario', () => {
    wrap(<RunMeters run={RUN} ticks={TICKS} scenario={SCENARIO} />);
    // The tick cap and cast size come from run.config (5, 2), not from the
    // scenario as it stands now (10, 3).
    expect(screen.getByText('2 / 5')).toBeInTheDocument();
    expect(screen.getByText('Synchronous')).toBeInTheDocument();
  });

  it('totals calls and tokens across every tick', () => {
    wrap(<RunMeters run={RUN} ticks={TICKS} scenario={SCENARIO} />);
    expect(screen.getByText('3')).toBeInTheDocument();     // agent turns
    expect(screen.getByText('22')).toBeInTheDocument();    // tokens
    expect(screen.getByText('$0.1234')).toBeInTheDocument();
  });

  it('says why the run ended when it has stopped', () => {
    wrap(<RunMeters run={RUN} ticks={TICKS} scenario={SCENARIO} />);
    expect(screen.getByText('tick cap reached')).toBeInTheDocument();
  });

  it('says nothing about an ending for a run still going', () => {
    wrap(<RunMeters run={{ ...RUN, stop_reason: undefined }} ticks={TICKS} scenario={SCENARIO} />);
    expect(screen.queryByText('Ended')).not.toBeInTheDocument();
  });
});
