import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// A running run gets a box to talk to it: Steer posts the message for the
// next model step, Interrupt stops the run (and a task starts again with the
// message). What was sent shows with where it is.

const ok = (data) => Promise.resolve({ data });
const steerRun = vi.fn();
const listRunSteering = vi.fn();

vi.mock('../../api/steering', () => ({
  steerRun: (...a) => steerRun(...a),
  listRunSteering: (...a) => listRunSteering(...a),
}));
vi.mock('../../api/browser', () => ({
  getRunBrowserSession: () => Promise.reject({ response: { status: 404 } }),
  setBrowserControl: () => ok({}),
}));
vi.mock('../stream', () => ({ useChannel: () => {} }));

import LiveRunStream from '../LiveRunStream';

const seed = (status = 'running') => ({
  run_id: 'run-1', agent_id: 'writer', text: '', thinking: [], tools: [], status,
});

const show = (status) => render(
  <I18nProvider>
    <LiveRunStream sessionId="sess-1" runId="run-1" seed={seed(status)} />
  </I18nProvider>,
);

describe('LiveRunStream steering', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = () => {};
    listRunSteering.mockImplementation(() => ok({ run_id: 'run-1', status: 'running', messages: [] }));
  });

  it('steers a running run and lists the message with its state', async () => {
    steerRun.mockImplementation(() => ok({ next: 'wait', message: { msg_id: 'm1' } }));
    show('running');
    const box = await screen.findByPlaceholderText('Tell the agent something while it works…');
    fireEvent.change(box, { target: { value: 'use the 2025 numbers' } });
    listRunSteering.mockImplementation(() => ok({ run_id: 'run-1', status: 'running', messages: [
      { msg_id: 'm1', body: 'use the 2025 numbers', mode: 'inject', status: 'delivered', delivered_step: 3 },
    ] }));
    fireEvent.click(screen.getByRole('button', { name: /Steer/ }));
    await waitFor(() => expect(steerRun).toHaveBeenCalledWith('run-1', 'use the 2025 numbers', 'inject'));
    expect(await screen.findByText('Delivered at step 3')).toBeTruthy();
  });

  it('interrupts a task run and says where it went on', async () => {
    steerRun.mockImplementation(() => ok({ next: 'relaunched', next_run_id: 'abcdef1234' }));
    show('running');
    fireEvent.click(await screen.findByRole('radio', { name: 'Interrupt' }));
    fireEvent.change(screen.getByPlaceholderText('Tell the agent something while it works…'),
      { target: { value: 'wrong repo, stop' } });
    // The mode toggle is a radio group; the send button carries the mode's name.
    fireEvent.click(screen.getByRole('button', { name: /Interrupt/ }));
    await waitFor(() => expect(steerRun).toHaveBeenCalledWith('run-1', 'wrong repo, stop', 'interrupt'));
    expect(await screen.findByText(/started again as abcdef12/)).toBeTruthy();
  });

  it('says so when the run has already ended', async () => {
    steerRun.mockImplementation(() => Promise.reject({
      response: { status: 409, data: { detail: { message: 'x', status: 'completed' } } },
    }));
    show('running');
    fireEvent.change(await screen.findByPlaceholderText('Tell the agent something while it works…'),
      { target: { value: 'late' } });
    fireEvent.keyDown(screen.getByPlaceholderText('Tell the agent something while it works…'), { key: 'Enter' });
    expect(await screen.findByText('The run is no longer running (completed).')).toBeTruthy();
  });

  it('shows no box for a finished run that nobody steered', async () => {
    show('finished');
    await waitFor(() => expect(listRunSteering).toHaveBeenCalledWith('run-1'));
    expect(screen.queryByTestId('run-steer')).toBeNull();
  });
});
