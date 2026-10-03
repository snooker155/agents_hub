import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The run's steer box offers a Model mode: a picker of the enabled catalog
// models, and the pick goes to the run as a switch_model message.

const ok = (data) => Promise.resolve({ data });
const steerRun = vi.fn();
const listRunSteering = vi.fn();
const getModelsCatalog = vi.fn();

vi.mock('../../api/steering', () => ({
  steerRun: (...a) => steerRun(...a),
  listRunSteering: (...a) => listRunSteering(...a),
}));
vi.mock('../../api/agentLoop', async () => {
  const real = await vi.importActual('../../api/agentLoop');
  return { ...real, getModelsCatalog: (...a) => getModelsCatalog(...a) };
});
vi.mock('../../api/browser', () => ({
  getRunBrowserSession: () => Promise.reject({ response: { status: 404 } }),
  setBrowserControl: () => ok({}),
}));
vi.mock('../stream', () => ({ useChannel: () => {} }));

import LiveRunStream from '../LiveRunStream';

const show = () => render(
  <I18nProvider>
    <LiveRunStream sessionId="sess-1" runId="run-1"
      seed={{ run_id: 'run-1', agent_id: 'writer', text: '', thinking: [], tools: [], status: 'running' }} />
  </I18nProvider>,
);

describe('LiveRunStream model switch', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = () => {};
    listRunSteering.mockImplementation(() => ok({ run_id: 'run-1', status: 'running', messages: [] }));
    getModelsCatalog.mockImplementation(() => ok({
      openai: { models: [{ id: 'gpt-b', enabled: true }, { id: 'gpt-off', enabled: false }] },
      anthropic: { models: [{ id: 'claude-a', enabled: true }] },
    }));
  });

  it('switches the run to a model picked from the enabled ones', async () => {
    steerRun.mockImplementation(() => ok({ next: 'wait', message: { msg_id: 'm1', body: 'openai/gpt-b' } }));
    show();
    fireEvent.click(await screen.findByRole('radio', { name: 'Model' }));
    const picker = await screen.findByRole('combobox', { name: 'Model' });
    await waitFor(() => expect(screen.getByRole('option', { name: 'openai/gpt-b' })).toBeTruthy());
    expect(screen.queryByRole('option', { name: 'openai/gpt-off' })).toBeNull();
    fireEvent.change(picker, { target: { value: 'openai/gpt-b' } });
    fireEvent.click(screen.getByRole('button', { name: /Model/ }));
    await waitFor(() => expect(steerRun).toHaveBeenCalledWith('run-1', 'openai/gpt-b', 'switch_model', { send: false }));
    expect(await screen.findByText('The run moves to openai/gpt-b before its next step.')).toBeTruthy();
  });

  it('tags a switch in the list of messages sent', async () => {
    listRunSteering.mockImplementation(() => ok({ run_id: 'run-1', status: 'running', messages: [
      { msg_id: 'm1', body: 'openai/gpt-b', mode: 'switch_model', status: 'delivered', delivered_step: 2 },
    ] }));
    show();
    expect(await screen.findByText('Model switch')).toBeTruthy();
    expect(getModelsCatalog).not.toHaveBeenCalled();
  });
});
