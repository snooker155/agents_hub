import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '../../i18n';

const decideToolApproval = vi.fn();
vi.mock('../../api/toolApprovals', () => ({
  decideToolApproval: (...a) => decideToolApproval(...a),
}));

import { ApprovalActions } from '../notificationApproval';
import { approvalRunPath } from '../notificationText';

// A notification about a call that waits in a background run answers it
// where it is read, and links to the run it waits in.

const N = { id: 'n-1', read: false, source: { kind: 'tool_approval', approval_id: 'appr_1' } };
const show = (props) => render(<I18nProvider><ApprovalActions notification={N} onAnswered={vi.fn()} {...props} /></I18nProvider>);

describe('ApprovalActions', () => {
  beforeEach(() => vi.clearAllMocks());

  it('approves and says so', async () => {
    decideToolApproval.mockResolvedValue({ data: { approval: { status: 'approved' } } });
    const onAnswered = vi.fn();
    show({ onAnswered });
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await screen.findByText('Approved');
    expect(decideToolApproval).toHaveBeenCalledWith('appr_1', 'approve');
    expect(onAnswered).toHaveBeenCalledWith(N);
  });

  it('says when the call no longer waits', async () => {
    decideToolApproval.mockRejectedValue({ response: { status: 409 } });
    const onAnswered = vi.fn();
    show({ onAnswered });
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }));
    await waitFor(() => expect(screen.getByText(/no longer waiting/i)).toBeTruthy());
    expect(onAnswered).not.toHaveBeenCalled();
  });

  it('has no buttons once the notification is read', () => {
    show({ notification: { ...N, read: true } });
    expect(screen.queryByTestId('notification-approval')).toBeNull();
  });
});

describe('approvalRunPath', () => {
  it('links to a team run', () => {
    expect(approvalRunPath({ team_id: 't1', team_run_id: 'tr 1' })).toBe('/teams/t1?run=tr%201');
  });

  it('links to the loop run first, then the flow run', () => {
    expect(approvalRunPath({ loop_id: 'l 1', loop_run_id: 'lr', flow_id: 'f', flow_run_id: 'fr' }))
      .toBe('/loops/l%201/runs/lr');
    expect(approvalRunPath({ flow_id: 'f', flow_run_id: 'fr' })).toBe('/flows/f/runs/fr');
    expect(approvalRunPath({})).toBeNull();
  });
});
