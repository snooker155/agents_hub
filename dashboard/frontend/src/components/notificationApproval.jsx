/**
 * A notification about a tool call that waits for a person in a background
 * flow or loop (common/tool_approvals.py, source.kind `tool_approval`):
 * Approve / Deny right where the notification is read (the bell, the Plan
 * page's Notifications tab). The run it waits in is approvalRunPath
 * (notificationText.js).
 */
import { useState } from 'react';
import { useI18n } from '../i18n';
import { decideToolApproval } from '../api/toolApprovals';

// Approve / Deny for a call that waits in a background run, right in the
// bell. A call already answered, timed out or stopped says so instead.
export function ApprovalActions({ notification, onAnswered }) {
  const { t } = useI18n();
  const [sending, setSending] = useState('');
  const [result, setResult] = useState('');
  const answer = async (e, decision) => {
    e.stopPropagation();
    setSending(decision);
    try {
      const { data } = await decideToolApproval(notification.source.approval_id, decision);
      setResult(t(`toolApproval.status.${data?.approval?.status || (decision === 'approve' ? 'approved' : 'denied')}`));
      onAnswered(notification);
    } catch (err) {
      setResult(t(err?.response?.status === 409 ? 'toolApproval.errors.gone' : 'toolApproval.errors.failed'));
    } finally {
      setSending('');
    }
  };
  if (result) return <div className="mt-1 text-[11px] text-gray-500">{result}</div>;
  // Read: answered elsewhere, or the wait is over (the backend marks it so).
  if (notification.read) return null;
  return (
    <div className="mt-1 flex items-center gap-1.5" data-testid="notification-approval">
      <button
        type="button"
        onClick={(e) => answer(e, 'approve')}
        disabled={!!sending}
        className="rounded bg-emerald-600 px-2 py-0.5 text-[11px] font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
      >
        {t('toolApproval.approve')}
      </button>
      <button
        type="button"
        onClick={(e) => answer(e, 'deny')}
        disabled={!!sending}
        className="rounded border border-gray-200 bg-white px-2 py-0.5 text-[11px] font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
      >
        {t('toolApproval.deny')}
      </button>
    </div>
  );
}
