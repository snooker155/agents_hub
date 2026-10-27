import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '../../../i18n';

const handlers = {};
vi.mock('../../stream', () => ({
  useStream: () => ({
    acquireChannel: () => () => {},
    on: (channel, fn) => { handlers[channel] = fn; return () => {}; },
  }),
}));
const listFlowRunApprovals = vi.fn();
const listTeamRunApprovals = vi.fn();
const decideToolApproval = vi.fn();
vi.mock('../../../api/toolApprovals', () => ({
  listFlowRunApprovals: (...a) => listFlowRunApprovals(...a),
  listTeamRunApprovals: (...a) => listTeamRunApprovals(...a),
  decideToolApproval: (...a) => decideToolApproval(...a),
  listRunApprovals: vi.fn(),
}));

import RunApprovals from '../RunApprovals';

// A node of a background flow run waits for a person: the run's page shows
// the call with Approve / Deny, read from the server and kept current by the
// run's channel.

const ROW = {
  approval_id: 'appr_9', run_id: 'node-run-1', flow_run_id: 'fr-1', agent_id: 'swe_agent',
  tool: 'run_shell', status: 'pending', input: { command: 'make release' }, reason: '',
};

const show = () => render(<I18nProvider><RunApprovals flowRunId="fr-1" /></I18nProvider>);

describe('RunApprovals', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listFlowRunApprovals.mockResolvedValue({ data: { approvals: [ROW] } });
  });

  it('shows the waiting call of the run and answers it', async () => {
    decideToolApproval.mockResolvedValue({ data: { approval: { ...ROW, status: 'approved' } } });
    show();
    await screen.findByTestId('tool-approval-card');
    expect(listFlowRunApprovals).toHaveBeenCalledWith('fr-1', 'pending');
    expect(screen.getByText(/swe_agent waits for approval/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(decideToolApproval).toHaveBeenCalledWith('appr_9', 'approve', ''));
    await waitFor(() => expect(screen.queryByTestId('run-approvals')).toBeNull());
  });

  it('follows the run channel: a new call is read, a settled one leaves', async () => {
    listFlowRunApprovals.mockResolvedValueOnce({ data: { approvals: [] } });
    show();
    await waitFor(() => expect(listFlowRunApprovals).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('run-approvals')).toBeNull();
    act(() => handlers['run:fr-1']({ type: 'tool_approval', approval_id: 'appr_9', agent_label: 'Writer' }));
    await screen.findByText(/Writer waits for approval/);
    act(() => handlers['run:fr-1']({ type: 'tool_approval_resolved', approval_id: 'appr_9', status: 'expired' }));
    await waitFor(() => expect(screen.queryByTestId('run-approvals')).toBeNull());
  });

  it('reads a team run and follows its team channel', async () => {
    listTeamRunApprovals.mockResolvedValue({ data: { approvals: [] } });
    render(<I18nProvider><RunApprovals teamRunId="tr-1" /></I18nProvider>);
    await waitFor(() => expect(listTeamRunApprovals).toHaveBeenCalledWith('tr-1', 'pending'));
    expect(listFlowRunApprovals).not.toHaveBeenCalled();
    listTeamRunApprovals.mockResolvedValue({ data: { approvals: [{ ...ROW, team_run_id: 'tr-1' }] } });
    act(() => handlers['team:tr-1']({ type: 'tool_approval', approval_id: 'appr_9', speaker: 'Sam' }));
    await screen.findByText(/Sam waits for approval/);
  });
});
