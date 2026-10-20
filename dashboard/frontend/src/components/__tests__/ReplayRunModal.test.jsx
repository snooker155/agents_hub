import React from 'react';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k) => k }),
}));

// Set per test: a mock reset between tests leaked its rejection as unhandled.
let replayRun;
vi.mock('../../api', () => ({ replayRun: (...a) => replayRun(...a) }));
vi.mock('../../api/agentLoop', async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    // The shape GET /models answers with: the provider map under `providers`.
    getModelsCatalog: () => Promise.resolve({ data: {
      providers: {
        openai: { models: [{ id: 'gpt-4o', enabled: true }, { id: 'gpt-4o-mini', enabled: true }, { id: 'gpt-off', enabled: false }] },
        anthropic: { models: [{ id: 'claude-haiku', enabled: true }] },
      },
      global_default: { provider: 'openai', model: 'gpt-4o' },
    } }),
  };
});

// The page's stream connection: the channel the panel follows and its latest
// handler, so a test can play the replay's events into it.
let followed = null;
vi.mock('../stream', () => ({
  useStream: () => ({ clientId: 'client-1' }),
  useChannel: (channel, handler) => { if (channel) followed = { channel, handler }; },
}));
vi.mock('../../api/steering', () => ({
  listRunSteering: () => Promise.resolve({ data: { messages: [] } }),
  steerRun: vi.fn(),
}));

import ReplayRunModal from '../run/ReplayRunModal';

const run = { run_id: 'r1', provider: 'openai', model: 'gpt-4o' };

describe('ReplayRunModal', () => {
  it('offers the enabled catalog models and replays on the picked one', async () => {
    replayRun = vi.fn().mockResolvedValue({ data: {
      identical: false, replay_run_id: 'r2', diff: '-a\n+b',
      original: { model: 'gpt-4o', text: 'a', cost: 0 },
      replay: { ok: true, model: 'gpt-4o-mini', text: 'b', cost: 0, duration_ms: 5 },
    } });
    render(<MemoryRouter><ReplayRunModal run={run} onClose={() => {}} /></MemoryRouter>);
    const select = screen.getByRole('combobox');
    await waitFor(() => expect(screen.getByRole('option', { name: 'claude-haiku' })).toBeInTheDocument());
    expect(screen.queryByRole('option', { name: 'gpt-off' })).toBeNull();
    fireEvent.change(select, { target: { value: 'anthropic/claude-haiku' } });
    fireEvent.click(screen.getByText('messageDetails.runReplay'));
    await waitFor(() => expect(screen.getByText('messageDetails.outputDiffers')).toBeInTheDocument());
    expect(replayRun).toHaveBeenCalledWith('r1', expect.objectContaining({
      provider: 'anthropic', model: 'claude-haiku', client_id: 'client-1',
    }));
    expect(replayRun.mock.calls[0][1].channel).toMatch(/^replay:/);
    expect(screen.getByText('messageDetails.openReplayRun').closest('a')).toHaveAttribute('href', '/messages/r2');
    expect(screen.getByText('messageDetails.replayAgain')).toBeInTheDocument();
  });

  it('shows the error and closes on Escape', async () => {
    replayRun = vi.fn(() => Promise.reject(Object.assign(new Error('400'), { response: { data: { detail: 'no input' } } })));
    const onClose = vi.fn();
    render(<MemoryRouter><ReplayRunModal run={run} onClose={onClose} /></MemoryRouter>);
    fireEvent.click(screen.getByText('messageDetails.runReplay'));
    await waitFor(() => expect(screen.getByText('no input')).toBeInTheDocument());
    expect(replayRun.mock.calls[0][1]).not.toHaveProperty('model');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('streams the replay while it runs and renders the diff when it ends', async () => {
    let finish;
    replayRun = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
    Element.prototype.scrollIntoView = () => {};
    render(<MemoryRouter><ReplayRunModal run={run} onClose={() => {}} /></MemoryRouter>);
    fireEvent.click(screen.getByText('messageDetails.runReplay'));
    await waitFor(() => expect(replayRun).toHaveBeenCalled());
    const { channel } = replayRun.mock.calls[0][1];
    expect(followed.channel).toBe(channel);

    act(() => {
      followed.handler({ type: 'meta', run_id: 'r2', agent_id: 'main-agent' });
      followed.handler({ type: 'tool_start', run_id: 'r2', step: 1, tool: 'read_file', input: 'a.txt' });
      followed.handler({ type: 'token', run_id: 'r2', token: 'partial answer' });
    });
    expect(screen.getByText('messageDetails.replayLive')).toBeInTheDocument();
    expect(screen.getByText('read_file')).toBeInTheDocument();
    expect(screen.getByText('partial answer')).toBeInTheDocument();

    await act(async () => {
      finish({ data: {
        identical: false, replay_run_id: 'r2', diff: '--- original\n+++ replay\n-old line\n+new line',
        original: { model: 'gpt-4o', text: 'old line', cost: 0 },
        replay: { ok: true, model: 'gpt-4o', text: 'new line', cost: 0, duration_ms: 5 },
      } });
    });
    expect(screen.getByText('messageDetails.replayDiff')).toBeInTheDocument();
    expect(screen.getByText('-old line')).toHaveClass('bg-red-50');
    expect(screen.getByText('+new line')).toHaveClass('bg-emerald-50');
  });
});
