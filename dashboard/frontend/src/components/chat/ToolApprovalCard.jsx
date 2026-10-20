/**
 * The card a chat turn shows while one of its tool calls waits for a person
 * (common/tool_approvals.py, docs/hooks.md "In chat"): what the agent wants to
 * run and why it was held, a note, Approve and Deny. The answer goes to the
 * server; the turn itself carries on (or reads the refusal) and streams
 * `tool_approval_resolved`, which settles the card. A call a person approved
 * leaves no card behind.
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

  // Approved by a person: nothing left to say, the call just runs. A denial and
  // an approval auto mode gave stay, they tell the person something.
  if (status === 'approved' && !(approval.auto || approval.by === 'auto_mode')) return null;

  return (
    <div
      className="mt-1.5 rounded-md border border-amber-200 bg-amber-50/50 px-2.5 py-1.5 text-xs"
      data-testid="tool-approval-card"
      role="group"
      aria-label={t('toolApproval.title')}
    >
      <div className="flex min-w-0 items-center gap-1.5">
        <ShieldAlert className="h-3.5 w-3.5 flex-shrink-0 text-amber-600" />
        <span className="flex-shrink-0 font-semibold text-amber-800">{t('toolApproval.title')}</span>
        <code className="flex-shrink-0 rounded bg-white/70 px-1 text-[11px] text-gray-700">{approval.tool}</code>
        {approval.reason ? (
          <span className="min-w-0 flex-1 truncate text-[11px] text-gray-500" title={approval.reason}>
            {approval.reason}
          </span>
        ) : <span className="flex-1" />}
        <span
          className={`flex-shrink-0 rounded-full px-1.5 py-px text-[10px] font-medium ${STATUS_CLASSES[status] || STATUS_CLASSES.expired}`}
          title={open && until ? t('toolApproval.waitsUntil', { time: until }) : undefined}
        >
          {t(`toolApproval.status.${status}`)}
        </span>
      </div>
      {input ? (
        <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-all rounded bg-white/80 px-1.5 py-1 text-[11px] leading-snug text-gray-800">
          {input}
        </pre>
      ) : null}
      {open ? (
        <div className="mt-1 flex items-center gap-1.5">
          <input
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !sending) answer('approve'); }}
            maxLength={2000}
            placeholder={t('toolApproval.notePlaceholder')}
            aria-label={t('toolApproval.noteLabel')}
            className="min-w-0 flex-1 rounded border border-gray-200 bg-white px-2 py-0.5 text-[11px] text-gray-800 focus:outline-none"
          />
          <button
            type="button"
            onClick={() => answer('approve')}
            disabled={!!sending}
            className="flex-shrink-0 rounded bg-emerald-600 px-2.5 py-0.5 text-[11px] font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {t('toolApproval.approve')}
          </button>
          <button
            type="button"
            onClick={() => answer('deny')}
            disabled={!!sending}
            className="flex-shrink-0 rounded border border-red-300 bg-white px-2.5 py-0.5 text-[11px] font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
          >
            {t('toolApproval.deny')}
          </button>
        </div>
      ) : null}
      {status === 'approved' ? (
        <p className="mt-0.5 text-[11px] text-amber-700" data-testid="tool-approval-auto">
          {t('toolApproval.approvedAutomatically')}
        </p>
      ) : status !== 'pending' && (settled.decided_by_name || settled.note) ? (
        <p className="mt-0.5 text-[11px] text-gray-500" data-testid="tool-approval-outcome">
          {settled.decided_by_name ? t('toolApproval.decidedBy', { name: settled.decided_by_name }) : null}
          {settled.decided_by_name && settled.note ? ' · ' : null}
          {settled.note ? t('toolApproval.noteShown', { note: settled.note }) : null}
        </p>
      ) : null}
      {error ? <p className="mt-0.5 text-[11px] text-red-600" role="alert">{error}</p> : null}
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
