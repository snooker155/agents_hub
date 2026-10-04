import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../../i18n';

const decideToolApproval = vi.fn();
const listRunApprovals = vi.fn();
vi.mock('../../../api/toolApprovals', () => ({
  decideToolApproval: (...a) => decideToolApproval(...a),
  listRunApprovals: (...a) => listRunApprovals(...a),
}));

import ToolApprovals, { ToolApprovalCard } from '../ToolApprovalCard';
import { MessageBubble } from '../MessageBubble';
import { applyApprovalEvent, mergeServerApprovals, pendingApproval, upsertApproval } from '../toolApprovals';

// A tool call of a chat turn waits for a person: the card shows what the
// agent wants to run, and Approve / Deny answer it in the same turn.

const PENDING = {
  approval_id: 'appr_1', run_id: 'run-1', tool: 'run_shell', status: 'pending',
  input: { command: 'rm -rf build' }, reason: '`run_shell` is on this workspace\'s approval list.',
  by: 'policy', expires_at: '2026-10-03T12:30:00+00:00',
};

const show = (ui) => render(<I18nProvider>{ui}</I18nProvider>);

describe('ToolApprovalCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listRunApprovals.mockResolvedValue({ data: { approvals: [] } });
  });

  it('shows the call and why it was held', () => {
    show(<ToolApprovalCard approval={PENDING} />);
    expect(screen.getByText('Approval needed')).toBeTruthy();
    expect(screen.getByText('run_shell')).toBeTruthy();
    expect(screen.getByText(/rm -rf build/)).toBeTruthy();
    expect(screen.getByText(/approval list/)).toBeTruthy();
    expect(screen.getByText('Waiting for you')).toBeTruthy();
  });

  it('approves with the note', async () => {
    decideToolApproval.mockResolvedValue({ data: { approval: { ...PENDING, status: 'approved' } } });
    show(<ToolApprovalCard approval={PENDING} />);
    fireEvent.change(screen.getByLabelText('Note for the agent'), { target: { value: ' only build/ ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(decideToolApproval).toHaveBeenCalledWith('appr_1', 'approve', 'only build/'));
    await waitFor(() => expect(screen.getByText('Approved')).toBeTruthy());
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  });

  it('denies', async () => {
    decideToolApproval.mockResolvedValue({ data: { approval: { ...PENDING, status: 'denied' } } });
    show(<ToolApprovalCard approval={PENDING} />);
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }));
    await waitFor(() => expect(decideToolApproval).toHaveBeenCalledWith('appr_1', 'deny', ''));
    await waitFor(() => expect(screen.getByText('Denied')).toBeTruthy());
  });

  it('says who may answer when the server refuses', async () => {
    decideToolApproval.mockRejectedValue({ response: { status: 403 } });
    show(<ToolApprovalCard approval={PENDING} />);
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/owner or an admin/));
    expect(screen.getByRole('button', { name: 'Approve' })).toBeTruthy();
  });

  it('settles from the stream with the answer and the note', () => {
    show(<ToolApprovalCard approval={{ ...PENDING, status: 'denied', decided_by_name: 'ann', note: 'use staging' }} />);
    expect(screen.getByText('Denied')).toBeTruthy();
    expect(screen.getByTestId('tool-approval-outcome').textContent).toBe('Answered by ann · Note: use staging');
    expect(screen.queryByRole('button', { name: 'Deny' })).toBeNull();
  });

  it('offers no buttons once the turn is over', () => {
    show(<ToolApprovalCard approval={PENDING} live={false} />);
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  });
});

describe('ToolApprovals under a live bubble', () => {
  beforeEach(() => vi.clearAllMocks());

  it('reads back a call that waits after a reload', async () => {
    listRunApprovals.mockResolvedValue({ data: { approvals: [PENDING] } });
    show(<ToolApprovals msg={{ id: 'a1', run_id: 'run-1', approvals: [] }} live />);
    await waitFor(() => expect(screen.getByTestId('tool-approval-card')).toBeTruthy());
    expect(listRunApprovals).toHaveBeenCalledWith('run-1', 'pending');
  });

  it('shows nothing when the turn is over', () => {
    show(<ToolApprovals msg={{ id: 'a1', run_id: 'run-1', approvals: [PENDING] }} live={false} />);
    expect(screen.queryByTestId('tool-approval-card')).toBeNull();
    expect(listRunApprovals).not.toHaveBeenCalled();
  });

  it('labels the waiting bubble', () => {
    listRunApprovals.mockResolvedValue({ data: { approvals: [] } });
    show(<MessageBubble msg={{ id: 'a1', role: 'agent', content: '', run_id: 'run-1', approvals: [PENDING] }} isStreaming />);
    expect(screen.getByText('Waiting for your approval of run_shell')).toBeTruthy();
    expect(screen.getByTestId('tool-approval-card')).toBeTruthy();
  });
});

describe('approval events on a message', () => {
  it('adds a call, then settles it in place', () => {
    const messages = [{ id: 'u1', role: 'user' }, { id: 'a1', role: 'agent' }];
    const waiting = applyApprovalEvent(messages, 'a1', { type: 'tool_approval', ...PENDING });
    expect(pendingApproval(waiting[1]).tool).toBe('run_shell');
    const done = applyApprovalEvent(waiting, 'a1', {
      type: 'tool_approval_resolved', approval_id: 'appr_1', status: 'approved', decided_by_name: 'ann',
    });
    expect(done[1].approvals).toHaveLength(1);
    expect(done[1].approvals[0]).toMatchObject({ status: 'approved', tool: 'run_shell', decided_by_name: 'ann' });
    expect(pendingApproval(done[1])).toBeNull();
    expect(done[0]).toBe(messages[0]);
  });

  it('ignores an event without an id or a target', () => {
    expect(upsertApproval([], { type: 'tool_approval' })).toEqual([]);
    const messages = [{ id: 'a1' }];
    expect(applyApprovalEvent(messages, null, PENDING)).toBe(messages);
  });

  it('keeps what the stream said over an older server read', () => {
    const local = [{ ...PENDING, status: 'approved' }];
    const merged = mergeServerApprovals(local, [PENDING, { ...PENDING, approval_id: 'appr_2' }]);
    expect(merged.map((a) => [a.approval_id, a.status])).toEqual([['appr_1', 'approved'], ['appr_2', 'pending']]);
  });
});
