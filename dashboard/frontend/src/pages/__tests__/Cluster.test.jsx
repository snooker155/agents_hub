import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The Cluster page is one document (GET /api/cluster) drawn as tables:
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
  instances: [],
  containers: [],
  hosts: [
    { host: 'host-a', members: ['host-a:100'], runs: 0, flow_runs: 0, instances: 0, containers: 0 },
    { host: 'host-b', members: ['worker-b'], runs: 1, flow_runs: 0, instances: 0, containers: 0 },
  ],
};

const getCluster = vi.fn(() => ok(MAP));
const getMemberLogs = vi.fn(() => ok('2026-09-23 10:00:00 INFO worker ready'));
const forgetMember = vi.fn(() => ok({ forgotten: 'worker-b' }));
const getContainerLogs = vi.fn(() => ok('Traceback: boom'));
const getInstanceLogs = vi.fn(() => ok({ logs: '[carrier_spawn] reason=start mode=docker' }));

vi.mock('../../api', () => ({
  getCluster: (...a) => getCluster(...a),
  getMemberLogs: (...a) => getMemberLogs(...a),
  forgetMember: (...a) => forgetMember(...a),
  getContainerLogs: (...a) => getContainerLogs(...a),
  getInstanceLogs: (...a) => getInstanceLogs(...a),
}));

vi.mock('../../components/stream', () => ({
  useChannel: () => {},
}));

// The diagram has its own test; here reactflow would need a layout engine.
vi.mock('../../components/cluster/ServiceMap', () => ({ default: () => <div data-testid="service-map" /> }));
vi.mock('../../components/cluster/WorkersCard', () => ({ default: () => <div data-testid="workers-card" /> }));

import Cluster from '../Cluster';

const show = () => render(<MemoryRouter><I18nProvider><Cluster /></I18nProvider></MemoryRouter>);

beforeEach(() => {
  getCluster.mockClear();
  getMemberLogs.mockClear();
  forgetMember.mockClear();
});

describe('Cluster map', () => {
  it('shows live members by default and the rest on request', async () => {
    show();
    await waitFor(() => expect(screen.getByText('host-a:100')).toBeInTheDocument());
    expect(screen.queryByText('worker-b')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: '' } });
    expect(screen.getByText('worker-b')).toBeInTheDocument();
  });

  it('lists members with status, leases and load', async () => {
    show();
    await waitFor(() => expect(screen.getByLabelText('Status')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: '' } });
    await waitFor(() => expect(screen.getByText('worker-b')).toBeInTheDocument());
    // Each status shows once as a pill and once in the status filter.
    expect(screen.getAllByText('Live')).toHaveLength(2);
    expect(screen.getAllByText('Stale')).toHaveLength(2);
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

  it('links each active run to its own page', async () => {
    getCluster.mockImplementationOnce(() => ok({
      ...MAP,
      entity_runs: { counts_by_kind: { flow: 1 }, active: [
        { run_id: 'flowrun-abcdef', kind: 'flow', entity_id: 'f-1', workspace: 'default', status: 'running' }] },
      loops: [{ loop_run_id: 'lr-1', loop_id: 'nightly', workspace: 'default', iterations_done: 2 }],
    }));
    show();
    await waitFor(() => expect(screen.getByText('run-1234')).toBeInTheDocument());
    expect(screen.getByText('run-1234').closest('a')).toHaveAttribute('href', '/messages/run-1234-5678');
    expect(screen.getByText('flowrun-').closest('a')).toHaveAttribute('href', '/flows/f-1/runs/flowrun-abcdef');
    expect(screen.getByText('nightly').closest('a')).toHaveAttribute('href', '/loops/nightly/runs/lr-1');
    // A queued item is not a run page yet.
    expect(screen.getByText('run-queu').closest('a')).toBeNull();
  });

  it('opens a member log and forgets only a member that is not live', async () => {
    show();
    await waitFor(() => expect(screen.getByLabelText('Status')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: '' } });
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

  it('filters members and shows them in batches', async () => {
    const many = Array.from({ length: 25 }, (_, i) => ({
      member_id: `worker-${String(i).padStart(2, '0')}`, role: 'worker', host: `host-${i % 3}`,
      status: i < 2 ? 'live' : 'stopped', heartbeat_age_seconds: 5 + i, uptime_seconds: 60, leases: [], load: {},
    }));
    getCluster.mockImplementation(() => ok({ ...MAP, members: [MAP.members[0], ...many] }));
    window.localStorage.removeItem('listPaging:cluster-members');
    show();
    await waitFor(() => expect(screen.getByText('worker-00')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: '' } });
    // Live first, then the first batch of 20 out of 26.
    expect(screen.getAllByTitle('View log')).toHaveLength(20);
    expect(screen.getByText('20 of 26 shown')).toBeInTheDocument();
    fireEvent.click(screen.getByText(/Load 6 more/));
    expect(screen.getAllByTitle('View log')).toHaveLength(26);

    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'live' } });
    expect(screen.getAllByTitle('View log')).toHaveLength(3);
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: '' } });
    fireEvent.change(screen.getByPlaceholderText(/Member, host/), { target: { value: 'worker-1' } });
    expect(screen.getAllByTitle('View log')).toHaveLength(10);
    fireEvent.change(screen.getByLabelText('Role'), { target: { value: 'api' } });
    expect(screen.getByText('No member matches the filters.')).toBeInTheDocument();
    getCluster.mockImplementation(() => ok(MAP));
  });

  it('shows active hosts only, the rest on request, and whose container is whose', async () => {
    getCluster.mockImplementation(() => ok({
      ...MAP,
      hosts: [
        { host: 'host-a', status: 'live', active: true, members: ['host-a:100'], runs: 0, flow_runs: 0, instances: 1, containers: 1 },
        { host: 'old-host', status: 'stopped', active: false, members: [], runs: 0, flow_runs: 0, instances: 0, containers: 0 },
      ],
      containers: [{ name: 'agents-hub-node-inst_1', agent_id: 'writer', state: 'running', host: 'daemon-1',
                     owner_kind: 'instance', owner_id: 'inst_1', worker: 'host-b:7', launched_on: 'host-b' }],
    }));
    show();
    await waitFor(() => expect(screen.getByText('agents-hub-node-inst_1')).toBeInTheDocument());
    expect(screen.queryByText('old-host')).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/Show stopped and silent hosts \(1\)/));
    expect(screen.getByText(/old-host/)).toBeInTheDocument();
    expect(screen.getByText('host-b:7')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'writer' })).toHaveAttribute('href', '/instances/inst_1');
    getCluster.mockImplementation(() => ok(MAP));
  });

  it('shows the load of each machine, the usage of each container and its log', async () => {
    getCluster.mockImplementation(() => ok({
      ...MAP,
      machines: [{
        id: 'boot-1', name: 'api-ctr', hosts: ['api-ctr'], members: [{ member_id: 'host-a:100', role: 'api' }],
        metrics: { cpus: 8, cpu_percent: 42, mem_total: 8 * 1024 ** 3, mem_used: 2 * 1024 ** 3, mem_percent: 25,
                   load: [1.5, 1.2, 1.0], sampled_at: new Date().toISOString(),
                   history: [[1, 40, 25], [16, 42, 25]] },
        docker: [{ daemon_id: 'D1', name: 'docker-desktop', server_version: '28.1.1', cpus: 8,
                   agent_containers: 1, containers_running: 5, containers_cpu_percent: 80, containers_mem_used: 1024 ** 3 }],
      }],
      containers: [{ name: 'agents-hub-node-inst_1', agent_id: 'writer', state: 'running', host: 'daemon-1',
                     owner_kind: 'instance', owner_id: 'inst_1',
                     stats: { cpu_percent: 12.5, mem_used: 100 * 1024 ** 2, mem_limit: 2 * 1024 ** 3, pids: 3 } }],
    }));
    show();
    await waitFor(() => expect(screen.getByText('Machines')).toBeInTheDocument());
    expect(screen.getByText('42% of 8 CPU')).toBeInTheDocument();
    expect(screen.getByText('2.0 GB of 8.0 GB')).toBeInTheDocument();
    // docker counts a busy core as 100%: 80% over 8 CPUs is a tenth of the machine.
    expect(screen.getByText('agent containers: 10.0%')).toBeInTheDocument();
    expect(screen.getByText('1 / 5')).toBeInTheDocument();
    expect(screen.getByText('12.5%')).toBeInTheDocument();
    expect(screen.getByText('100 MB / 2.0 GB')).toBeInTheDocument();

    const logButtons = screen.getAllByLabelText('View log');
    fireEvent.click(logButtons[logButtons.length - 1]);
    await waitFor(() => expect(screen.getByText('Traceback: boom')).toBeInTheDocument());
    expect(getContainerLogs).toHaveBeenCalledWith('agents-hub-node-inst_1', 500);
    fireEvent.click(screen.getByRole('button', { name: 'Instance log' }));
    await waitFor(() => expect(screen.getByText(/\[carrier_spawn\]/)).toBeInTheDocument());
    expect(getInstanceLogs).toHaveBeenCalledWith('inst_1');
    getCluster.mockImplementation(() => ok(MAP));
  });
});
