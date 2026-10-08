/**
 * The card a chat turn shows while one of its tool calls waits for a person
 * (common/tool_approvals.py, docs/hooks.md "In chat"): what the agent wants to
 * run and why it was held, a note, Approve and Deny. The answer goes to the
 * server; the turn itself carries on (or reads the refusal) and streams
 * `tool_approval_resolved`, which settles the card.
 *
 * `ToolApprovals` is the list under a live bubble. A chat reopened while its
 * turn waits has no `tool_approval` event to replay, so the list also asks the
 * server once which calls of the run are still waiting.
 */
import { useEffect, useState } from 'react';
import { ShieldAlert } from 'lucide-react';
import { useI18n } from '../../i18n';
import { decideToolApproval, listRunApprovals } from '../../api/toolApprovals';
import { mergeServerApprovals } from './toolApprovals';
import { ConnectionProposalCard } from './ConnectionProposalCard';

const STATUS_CLASSES = {
  pending: 'bg-amber-100 text-amber-800',
  approved: 'bg-emerald-100 text-emerald-700',
  denied: 'bg-red-100 text-red-700',
  expired: 'bg-gray-100 text-gray-600',
  cancelled: 'bg-gray-100 text-gray-600',
};

function prettyInput(input) {
  if (input == null || input === '') return '';
  if (typeof input === 'string') return input;
  try {
    return JSON.stringify(input, null, 2);
  } catch {
    return String(input);
  }
}

function clockTime(iso) {
  if (!iso) return '';
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? '' : when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function errorKey(err) {
  const status = err?.response?.status;
  if (status === 403) return 'toolApproval.errors.forbidden';
  if (status === 409) return 'toolApproval.errors.gone';
  return 'toolApproval.errors.failed';
}

// An agent proposing a connection gets its own card: a form built from the
// proposal's fields and a Connect button, instead of a raw input dump
// (connection-proposals-contract.md). The check runs before any hook of the
// generic card below, so it can switch cards without breaking hook order.
export function ToolApprovalCard({ approval, live = true }) {
  if (approval.tool === 'propose_connection') {
    return <ConnectionProposalCard approval={approval} live={live} />;
  }
  return <GenericToolApprovalCard approval={approval} live={live} />;
}

function GenericToolApprovalCard({ approval, live = true }) {
  const { t } = useI18n();
  const [note, setNote] = useState('');
  const [sending, setSending] = useState('');
  const [answered, setAnswered] = useState(null);
  const [error, setError] = useState('');
  // What the server said to our own answer counts until the stream confirms it.
  const status = approval.status !== 'pending' ? approval.status : (answered?.status || 'pending');
  const settled = answered && approval.status === 'pending' ? answered : approval;
  const open = status === 'pending' && live;
  const input = prettyInput(approval.input);
  const until = clockTime(approval.expires_at);

  const answer = async (decision) => {
    setSending(decision);
    setError('');
    try {
      const { data } = await decideToolApproval(approval.approval_id, decision, note.trim());
      setAnswered(data?.approval || { status: decision === 'approve' ? 'approved' : 'denied' });
    } catch (err) {
      setError(t(errorKey(err)));
    } finally {
      setSending('');
    }
  };

  return (
    <div
      className="mt-2 rounded-lg border border-amber-200 bg-amber-50/50 px-3 py-2.5 text-sm"
      data-testid="tool-approval-card"
      role="group"
      aria-label={t('toolApproval.title')}
    >
      <div className="flex items-center gap-1.5">
        <ShieldAlert className="h-4 w-4 flex-shrink-0 text-amber-600" />
        <span className="font-semibold text-amber-800">{t('toolApproval.title')}</span>
        <code className="rounded bg-white/70 px-1 text-xs text-gray-700">{approval.tool}</code>
        <span className={`ml-auto rounded-full px-2 py-0.5 text-[10px] font-medium ${STATUS_CLASSES[status] || STATUS_CLASSES.expired}`}>
          {t(`toolApproval.status.${status}`)}
        </span>
      </div>
      {approval.reason ? (
        <p className="mt-1 text-xs text-gray-600">
          {approval.by ? <span className="mr-1 text-gray-400">{t(`toolApproval.by.${approval.by}`, { defaultValue: approval.by })}:</span> : null}
          {approval.reason}
        </p>
      ) : null}
      {input ? (
        <pre className="mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-white/80 p-2 text-[11px] text-gray-800">
          {input}
        </pre>
      ) : null}
      {open ? (
        <>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            maxLength={2000}
            placeholder={t('toolApproval.notePlaceholder')}
            aria-label={t('toolApproval.noteLabel')}
            className="mt-2 w-full resize-y rounded border border-gray-200 bg-white px-2 py-1 text-xs text-gray-800 focus:outline-none"
          />
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => answer('approve')}
              disabled={!!sending}
              className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {t('toolApproval.approve')}
            </button>
            <button
              type="button"
              onClick={() => answer('deny')}
              disabled={!!sending}
              className="rounded border border-red-300 bg-white px-3 py-1 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
            >
              {t('toolApproval.deny')}
            </button>
            {until ? <span className="text-[11px] text-gray-500">{t('toolApproval.waitsUntil', { time: until })}</span> : null}
          </div>
        </>
      ) : null}
      {status !== 'pending' && (settled.decided_by_name || settled.note) ? (
        <p className="mt-1 text-[11px] text-gray-500" data-testid="tool-approval-outcome">
          {settled.decided_by_name ? t('toolApproval.decidedBy', { name: settled.decided_by_name }) : null}
          {settled.decided_by_name && settled.note ? ' · ' : null}
          {settled.note ? t('toolApproval.noteShown', { note: settled.note }) : null}
        </p>
      ) : null}
      {error ? <p className="mt-1 text-[11px] text-red-600" role="alert">{error}</p> : null}
    </div>
  );
}

/** The cards of a live bubble's turn, recovered from the server once after a reload. */
export default function ToolApprovals({ msg, live }) {
  const [fromServer, setFromServer] = useState([]);
  const runId = msg?.run_id;
  useEffect(() => {
    if (!live || !runId) return undefined;
    let current = true;
    // A failed read only means no recovered card: the stream still brings new ones.
    Promise.resolve()
      .then(() => listRunApprovals(runId, 'pending'))
      .then(({ data }) => { if (current) setFromServer(data?.approvals || []); })
      .catch(() => {});
    return () => { current = false; };
  }, [live, runId]);
  if (!live) return null;
  const approvals = mergeServerApprovals(msg?.approvals, fromServer);
  if (!approvals.length) return null;
  return approvals.map((a) => <ToolApprovalCard key={a.approval_id} approval={a} live={live} />);
}
