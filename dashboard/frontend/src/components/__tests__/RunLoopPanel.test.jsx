import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

const versionsApi = vi.hoisted(() => ({
  getRunAgentVersion: vi.fn(),
  rollbackRunAgent: vi.fn(),
}));
vi.mock('../../api/agentVersions', () => versionsApi);
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k) }),
}));

import RunLoopPanel from '../run/RunLoopPanel';

const versionInfo = (over = {}) => ({
  data: { agent_id: 'a1', version: 1, hash: 'h1', current_version: 2, is_current: false, pinned: false, ...over },
});

describe('RunLoopPanel', () => {
  beforeEach(() => {
    versionsApi.getRunAgentVersion.mockResolvedValue(versionInfo());
    versionsApi.rollbackRunAgent.mockResolvedValue({ data: { agent_id: 'a1', restored_to: 1 } });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });
  afterEach(() => vi.restoreAllMocks());

  it('renders nothing without a version or loop data', () => {
    const { container } = render(<RunLoopPanel run={{ run_id: 'r1' }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows a run-ran label and a rollback button when not the current version', async () => {
    render(<RunLoopPanel run={{ run_id: 'r1', agent_version: 1 }} />);
    expect(await screen.findByText('runLoop.versionRan {"version":1}')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /runLoop.rollback/ })).toBeInTheDocument();
  });

  it('shows the live label and no rollback button when it is the current version', async () => {
    versionsApi.getRunAgentVersion.mockResolvedValue(versionInfo({ version: 2, is_current: true }));
    render(<RunLoopPanel run={{ run_id: 'r1', agent_version: 2 }} />);
    expect(await screen.findByText('runLoop.versionLive {"version":2}')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /runLoop.rollback/ })).toBeNull();
  });

  it('shows the pinned label when the run was explicitly pinned', async () => {
    versionsApi.getRunAgentVersion.mockResolvedValue(versionInfo({ pinned: true }));
    render(<RunLoopPanel run={{ run_id: 'r1', agent_version: 1 }} />);
    expect(await screen.findByText('runLoop.versionPinned {"version":1}')).toBeInTheDocument();
  });

  it('rolls back on confirm and refreshes the version info', async () => {
    const onChanged = vi.fn();
    render(<RunLoopPanel run={{ run_id: 'r1', agent_version: 1 }} onChanged={onChanged} />);
    const button = await screen.findByRole('button', { name: /runLoop.rollback/ });
    versionsApi.getRunAgentVersion.mockResolvedValue(versionInfo({ is_current: true }));

    fireEvent.click(button);

    await waitFor(() => expect(versionsApi.rollbackRunAgent).toHaveBeenCalledWith('r1'));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('does not roll back when the confirm dialog is declined', async () => {
    window.confirm.mockReturnValue(false);
    render(<RunLoopPanel run={{ run_id: 'r1', agent_version: 1 }} />);
    const button = await screen.findByRole('button', { name: /runLoop.rollback/ });

    fireEvent.click(button);

    expect(versionsApi.rollbackRunAgent).not.toHaveBeenCalled();
  });

  it('renders only the loop sections that are present', () => {
    render(<RunLoopPanel run={{
      run_id: 'r1',
      loop: {
        answered_by: [{ provider: 'openai', model: 'gpt-5', fallback: true, reason: 'primary down' }],
        loaded_tools: ['read_file', 'write_file'],
      },
    }} />);
    expect(screen.getByText('runLoop.answeredBy')).toBeInTheDocument();
    expect(screen.getByText('openai/gpt-5')).toBeInTheDocument();
    expect(screen.getByText('runLoop.fallback')).toBeInTheDocument();
    expect(screen.getByText('runLoop.loadedTools')).toBeInTheDocument();
    expect(screen.getByText('read_file')).toBeInTheDocument();
    expect(screen.queryByText('runLoop.compactions')).toBeNull();
    expect(screen.queryByText('runLoop.guardrails')).toBeNull();
    expect(screen.queryByText('runLoop.structured')).toBeNull();
  });

  it('renders compactions, guardrails and structured-output sections when present', () => {
    render(<RunLoopPanel run={{
      run_id: 'r1',
      loop: {
        compactions: [{ kind: 'clear_tool_results', at_step: 4, chars_before: 8000, chars_after: 1200 }],
        guardrails: [{ guardrail_id: 'g1', name: 'no secrets', stage: 'output', passed: false, reason: 'leaked key' }],
        structured: { attempts: 2, valid: true, errors: [] },
        tool_decisions: [{ tool: 'run_shell', mode: 'always_ask', decision: 'allow', reason: 'approved' }],
      },
    }} />);
    expect(screen.getByText('runLoop.compactions')).toBeInTheDocument();
    expect(screen.getByText('runLoop.guardrails')).toBeInTheDocument();
    expect(screen.getByText('no secrets')).toBeInTheDocument();
    expect(screen.getByText('runLoop.structured')).toBeInTheDocument();
    expect(screen.getByText('runLoop.toolDecisions')).toBeInTheDocument();
    expect(screen.getByText('run_shell')).toBeInTheDocument();
  });

  it('lists the model calls made for the run beside its loop', () => {
    render(<RunLoopPanel run={{
      run_id: 'r1',
      loop: { aux_calls: [{ purpose: 'guardrail', provider: 'openai', model: 'gpt-4o-mini',
                            input_tokens: 120, output_tokens: 8 }] },
    }} />);
    expect(screen.getByText('runLoop.auxCalls')).toBeInTheDocument();
    expect(screen.getByText('openai/gpt-4o-mini')).toBeInTheDocument();
  });

  it('counts structured-output attempts recorded as a list', () => {
    render(<RunLoopPanel run={{
      run_id: 'r1',
      loop: { structured: { attempts: [{ source: 'answer', valid: false }, { source: 'repair', valid: true }],
                            valid: true, errors: [] } },
    }} />);
    expect(screen.getByText(/"attempts":2/)).toBeInTheDocument();
  });
  it('lists instructions added mid-run and links tool outputs saved to files', () => {
    render(<MemoryRouter><RunLoopPanel run={{
      run_id: 'r1',
      loop: {
        system_messages: [{ after_step: 2, text: 'never delete files', mode: 'system' }],
        tool_spills: [
          { tool: 'web_fetch', path: 'tool-outputs/r1/001-web_fetch.txt', file_id: 'file_0123456789abcdef', chars: 76000 },
          { tool: 'run_shell', path: 'tool-outputs/r1/002-run_shell.txt', file_id: null, chars: 30000 },
        ],
      },
    }} /></MemoryRouter>);
    expect(screen.getByText('runLoop.systemMessages')).toBeInTheDocument();
    expect(screen.getByText('never delete files')).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'tool-outputs/r1/001-web_fetch.txt' });
    expect(link).toHaveAttribute('href', '/files?file=file_0123456789abcdef');
    // Not registered yet: the path, without a link.
    expect(screen.getByText('tool-outputs/r1/002-run_shell.txt').tagName).toBe('SPAN');
  });
});
