import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../../i18n';

const ok = (data) => Promise.resolve({ data });
const DATA = {
  launcher: 'docker',
  workers: [
    { member_id: 'w-free', host: 'aaa', origin: 'hub', container: 'agents_hub-worker-hub-1a2b3c',
      runs: 1, concurrency: 4, carriers: 0, draining: false, stoppable: true },
    { member_id: 'w-busy', host: 'bbb', origin: 'compose', container: '', runs: 0, concurrency: 4,
      carriers: 2, draining: false, stoppable: false },
    { member_id: 'w-going', host: 'ccc', origin: 'external', container: '', runs: 0, concurrency: 4,
      carriers: 0, draining: true, stoppable: true },
  ],
  autoscale: {
    policy: { enabled: false, min_workers: 1, max_workers: 4, scale_up_wait_seconds: 30,
              scale_down_idle_minutes: 10, cooldown_seconds: 90 },
    events: [{ at: '2026-10-31T10:00:00+00:00', action: 'up', reason: 'queue_waiting', started: ['c1'] }],
    last_decision: { action: null, reason: 'steady' },
  },
};

const getWorkers = vi.fn(() => ok(DATA));
const addWorkers = vi.fn(() => ok({ started: ['agents_hub-worker-hub-9f9f9f'] }));
const stopWorker = vi.fn(() => ok({ draining: true }));
const cancelWorkerStop = vi.fn(() => ok({ draining: false }));
const updateAutoscale = vi.fn((p) => ok(p));

vi.mock('../../../api', () => ({
  getWorkers: (...a) => getWorkers(...a),
  addWorkers: (...a) => addWorkers(...a),
  stopWorker: (...a) => stopWorker(...a),
  cancelWorkerStop: (...a) => cancelWorkerStop(...a),
  updateAutoscale: (...a) => updateAutoscale(...a),
}));

import WorkersCard from '../WorkersCard';

const show = (props = {}) => render(<I18nProvider><WorkersCard {...props} /></I18nProvider>);

describe('WorkersCard', () => {
  beforeEach(() => { [getWorkers, addWorkers, stopWorker, cancelWorkerStop, updateAutoscale].forEach((f) => f.mockClear()); });

  it('lists who started each worker and what it carries', async () => {
    show();
    await screen.findByText(/w-free/);
    expect(screen.getByText('A new worker starts as a Docker container like an existing one.')).toBeInTheDocument();
    expect(screen.getByText(/w-free/).closest('tr').textContent).toMatch(/hub.*1\/4.*0.*working/);
    expect(screen.getByText(/w-going/).closest('tr').textContent).toContain('stopping');
  });

  it('adds a worker and tells the page', async () => {
    const onChanged = vi.fn();
    show({ onChanged });
    fireEvent.click(await screen.findByText('Add worker'));
    await screen.findByText('Started: agents_hub-worker-hub-9f9f9f');
    expect(addWorkers).toHaveBeenCalledWith(1);
    expect(onChanged).toHaveBeenCalled();
  });

  it('stops a free worker, never one carrying instances, and takes a stop back', async () => {
    show();
    await screen.findByText(/w-free/);
    const stopOf = (id) => screen.getByText(new RegExp(id)).closest('tr').querySelector('button');
    expect(stopOf('w-busy')).toBeDisabled();
    expect(stopOf('w-busy')).toHaveAttribute('title', 'It carries 2 instances, so it is not stopped.');
    fireEvent.click(stopOf('w-free'));
    await waitFor(() => expect(stopWorker).toHaveBeenCalledWith('w-free'));
    fireEvent.click(screen.getByText('Cancel stop'));
    await waitFor(() => expect(cancelWorkerStop).toHaveBeenCalledWith('w-going'));
  });

  it('in Kubernetes names the Deployment and shows a drained pod without a button', async () => {
    getWorkers.mockImplementationOnce(() => ok({
      ...DATA, launcher: 'kubernetes', launcher_detail: 'hub-worker',
      workers: [{ member_id: 'pod-1', host: 'pod-1', origin: 'kubernetes', container: '', runs: 0,
                  concurrency: 4, carriers: 0, draining: true, drained: true, stoppable: true }],
    }));
    show();
    await screen.findByText('A new worker is one more replica of the Deployment hub-worker in Kubernetes.');
    const row = screen.getByText(/pod-1/).closest('tr');
    expect(row.textContent).toMatch(/kubernetes.*done, being taken away/);
    expect(row.querySelector('button')).toBeNull();
  });

  it('turns autoscaling on and saves its bounds', async () => {
    show();
    await screen.findByTestId('autoscale');
    expect(screen.getByText('Now: nothing needed')).toBeInTheDocument();
    expect(screen.getByTestId('autoscale').querySelector('li').textContent).toMatch(/added · runs wait in the queue · c1/);
    fireEvent.click(screen.getByLabelText('On'));
    await waitFor(() => expect(updateAutoscale).toHaveBeenCalledWith(expect.objectContaining({ enabled: true })));
    fireEvent.change(screen.getByLabelText('Maximum workers'), { target: { value: '6' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(updateAutoscale).toHaveBeenLastCalledWith(expect.objectContaining({ max_workers: '6' })));
    await screen.findByText('Saved');
  });
});
