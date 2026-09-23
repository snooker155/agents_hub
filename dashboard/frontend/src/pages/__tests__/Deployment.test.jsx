import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The Deployment page is one document (GET /api/deployment) drawn as tables:
// members with their heartbeat, load and leases, the launch queue, and the
// working entities by host. It must show a stale member as such, a run's
// heartbeat age, and offer Forget only for a member that is not live.

const ok = (data) => Promise.resolve({ data });

const MAP = {
  generated_at: '2026-09-23T10:00:00+00:00',
  self: { member_id: 'host-a:100', role: 'api' },
  members: [
    { member_id: 'host-a:100', role: 'api', host: 'host-a', status: 'live', heartbeat_age_seconds: 3,
      uptime_seconds: 7200, leases: ['scheduler', 'watchdog'], load: { sse_clients: 2 }, version: 'abc1234', self: true },
    { member_id: 'worker-b', role: 'worker', host: 'host-b', status: 'stale', heartbeat_age_seconds: 400,
      uptime_seconds: 60, leases: [], load: { tracked: 1 }, version: 'abc1234', self: false },
  ],
  leases: [],
  queue: { queued: 1, leased: 0, running: 0, failed: 0, oldest_queued_seconds: 12,
           items: [{ run_id: 'run-queued-1', kind: 'task', status: 'queued', workspace: 'default', attempts: 0 }] },
  outbox: { pending: 0, dead: 0 },
  runs: [{ run_id: 'run-1234-5678', agent_id: 'writer', workspace: 'default', status: 'running',
           host: 'host-b', heartbeat_age_seconds: 9, checkpoint_step: 4, resume_attempts: 1 }],
  flow_runs: [],
  loops: [],
  nodes: [],
  containers: [],
  hosts: [
    { host: 'host-a', members: ['host-a:100'], runs: 0, flow_runs: 0, nodes: 0, containers: 0 },
    { host: 'host-b', members: ['worker-b'], runs: 1, flow_runs: 0, nodes: 0, containers: 0 },
  ],
};

const getDeployment = vi.fn(() => ok(MAP));
const getMemberLogs = vi.fn(() => ok('2026-09-23 10:00:00 INFO worker ready'));
const forgetMember = vi.fn(() => ok({ forgotten: 'worker-b' }));

vi.mock('../../api', () => ({
  getDeployment: (...a) => getDeployment(...a),
  getMemberLogs: (...a) => getMemberLogs(...a),
  forgetMember: (...a) => forgetMember(...a),
}));

vi.mock('../../components/stream', () => ({
  useChannel: () => {},
}));

import Deployment from '../Deployment';

const show = () => render(<I18nProvider><Deployment /></I18nProvider>);

beforeEach(() => {
  getDeployment.mockClear();
  getMemberLogs.mockClear();
  forgetMember.mockClear();
});

describe('Deployment map', () => {
  it('lists members with status, leases and load', async () => {
    show();
    await waitFor(() => expect(screen.getByText('worker-b')).toBeInTheDocument());
    expect(screen.getByText('Live')).toBeInTheDocument();
    expect(screen.getByText('Stale')).toBeInTheDocument();
    expect(screen.getByText('scheduler, watchdog')).toBeInTheDocument();
    expect(screen.getByText('sse_clients=2')).toBeInTheDocument();
    expect(screen.getByText('(you)')).toBeInTheDocument();
  });

  it('shows the queue and a run with its heartbeat age and checkpoint on its host', async () => {
    show();
    await waitFor(() => expect(screen.getByText('run-1234')).toBeInTheDocument());
    expect(screen.getByText('run-queu')).toBeInTheDocument();
    expect(screen.getByText('9s ago')).toBeInTheDocument();
    expect(screen.getByText('step 4, resumed 1×')).toBeInTheDocument();
    expect(screen.getAllByText('host-b').length).toBeGreaterThan(1);
  });

  it('opens a member log and forgets only a member that is not live', async () => {
    show();
    await waitFor(() => expect(screen.getByText('worker-b')).toBeInTheDocument());
    const buttons = screen.getAllByTitle('View log');
    fireEvent.click(buttons[1]);
    await waitFor(() => expect(getMemberLogs).toHaveBeenCalledWith('worker-b', 400));
    expect(await screen.findByText(/worker ready/)).toBeInTheDocument();

    const forgets = screen.getAllByTitle(/Only a stale or stopped member/);
    expect(forgets[0]).toBeDisabled();
    expect(forgets[1]).not.toBeDisabled();
    fireEvent.click(forgets[1]);
    await waitFor(() => expect(forgetMember).toHaveBeenCalledWith('worker-b'));
  });
});
