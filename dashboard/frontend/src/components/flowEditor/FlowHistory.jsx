import { useState } from 'react';
import { FlaskConical, MessageSquare, RotateCcw, SquareTerminal, Trash2, Workflow } from 'lucide-react';
import { useI18n } from '../../i18n';
import { RUN_STATUS_STYLES, fmtDateTime, rerunBubbleText } from './runFormat';
import SaveAsEvalCaseDialog from '../evals/SaveAsEvalCaseDialog';

export function FlowRunMessages({ run, messages: given = [], onClose, workspace }) {
  const { t } = useI18n();
  const isChat = run?.kind === 'chat';
  const [caseDialogOpen, setCaseDialogOpen] = useState(false);
  // A run again from a node has no message of its own (runFormat.js).
  const messages = given.map((m) => (m.rerun && !m.content ? { ...m, content: rerunBubbleText(t, m.rerun) } : m));
  return (
    <div className="flex h-full w-full min-w-0 flex-col overflow-hidden bg-slate-50">
      <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-3 py-2.5">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-slate-100">
          {isChat ? <MessageSquare className="h-4 w-4 text-indigo-600" /> : <Workflow className="h-4 w-4 text-emerald-600" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-bold text-slate-900">
            {run?.title || (isChat ? t('flowEditor.flowChat') : t('flowEditor.flowRun'))}
          </div>
          <div className="truncate text-[11px] text-slate-400">
            {isChat ? t('flowEditor.chat') : t('flowEditor.taskRun')} · {fmtDateTime(run?.started_at)} · {t('flowEditor.readOnlyHistory')}
          </div>
        </div>
        {!isChat && run?.run_group && (
          <button
            type="button"
            onClick={() => setCaseDialogOpen(true)}
            title={t('messageDetails.toEvalCaseHint')}
            className="rounded-lg px-2.5 py-1 text-xs font-semibold text-indigo-600 transition hover:bg-indigo-50 inline-flex items-center gap-1"
          >
            <FlaskConical className="h-3.5 w-3.5" /> {t('messageDetails.toEvalCase')}
          </button>
        )}
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg px-2.5 py-1 text-xs font-semibold text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
        >
          {t('flowEditor.close')}
        </button>
      </div>
      {caseDialogOpen && (
        <SaveAsEvalCaseDialog
          runId={run.run_group}
          workspace={workspace}
          onClose={() => setCaseDialogOpen(false)}
        />
      )}

      <div className="flex-1 overflow-y-auto px-3 py-3">
        {messages.length === 0 ? (
          <div className="flex h-full min-h-[200px] flex-col items-center justify-center text-center text-sm text-slate-500">
            {t('flowEditor.thisRunProducedNoMessages')}
          </div>
        ) : (
          messages.map((msg) => {
            const isUser = msg.role === 'user';
            return (
              <div key={msg.id} className={`mb-4 flex gap-2.5 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
                <div className={`flex shrink-0 flex-col items-center gap-1 ${isUser ? '' : 'w-14'}`}>
                  <div
                    className={`flex h-7 w-7 items-center justify-center rounded-full text-white ${
                      isUser ? 'bg-indigo-600' : 'bg-gray-800'
                    }`}
                  >
                    {isUser ? <SquareTerminal className="h-3.5 w-3.5" /> : <Workflow className="h-3.5 w-3.5" />}
                  </div>
                  {!isUser && msg.agent_label && (
                    <span title={msg.agent_label} className="w-full truncate text-center text-[9px] font-medium leading-tight text-gray-400">
                      {msg.agent_label}
                    </span>
                  )}
                </div>
                <div
                  className={`max-w-[78%] whitespace-pre-wrap text-sm leading-relaxed ${
                    isUser
                      ? 'rounded-2xl rounded-tr-sm bg-indigo-600 px-3.5 py-2.5 text-white'
                      : 'rounded-2xl rounded-tl-sm border border-gray-200 bg-white px-3.5 py-2.5 text-gray-800 shadow-sm'
                  } ${msg.error ? 'border-red-300 bg-red-50 text-red-700' : ''}`}
                >
                  {msg.content || (msg.error ? '(no output)' : '')}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

// A run that failed, or parked on a human_interrupt node, keeps the checkpoint
// it got to. `instances` is the per-execution record (flow/run_store.py) that
// carries it: the log-derived rows the list is built from do not.
const RESUMABLE_STATUSES = new Set(['failed', 'awaiting_input']);

// Any entry can be deleted from the list (onDelete): a chat with its
// conversation, a task run with its record (the task itself stays). Not one
// still running, nor an entry of the old shared log, which has no file of its own.
export function FlowHistory({
  runs = [], instances = {}, onResume, onDelete, resumingRun, selectedRunGroup, onSelect,
}) {
  const { t } = useI18n();
  if (!runs.length) {
    return (
      <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
        {t('flowEditor.noPreviousRunsYetRun')}
      </div>
    );
  }
  // A compact list, one row per record: the kind as an icon, the title and
  // the status dot on top, the kind, time and step count under it.
  return (
    <ul className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white">
      {runs.map((run) => {
        const isActive = run.run_group === selectedRunGroup;
        const isChat = run.kind === 'chat';
        const record = instances[run.run_group] || {};
        const canResume = RESUMABLE_STATUSES.has(record.status) && !!record.checkpoint;
        const status = RUN_STATUS_STYLES[record.status] || RUN_STATUS_STYLES[run.status] || RUN_STATUS_STYLES.running;
        const nodeCount = (run.events || []).filter((e) => e.type === 'agent_start').length;
        const KindIcon = isChat ? MessageSquare : Workflow;
        return (
          <li
            key={run.run_group}
            className={`flex items-center gap-1 pr-1 transition ${isActive ? 'bg-cyan-50' : 'hover:bg-slate-50'}`}
          >
            <button type="button" onClick={() => onSelect(run)} className="flex min-w-0 flex-1 items-start gap-2 px-2.5 py-1.5 text-left">
              <KindIcon className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${isChat ? 'text-indigo-500' : 'text-emerald-500'}`} aria-hidden="true" />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <span className={`min-w-0 flex-1 truncate text-[13px] font-medium ${isActive ? 'text-cyan-800' : 'text-slate-800'}`}>
                    {run.title || (isChat ? t('flowEditor.flowChat') : t('flowEditor.flowRun'))}
                  </span>
                  <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${status.dot}`} title={status.label} />
                  <span className="sr-only">{status.label}</span>
                </span>
                <span className="block truncate text-[11px] text-slate-400">
                  {isChat ? t('flowEditor.chat') : t('flowEditor.taskRun')} · {fmtDateTime(run.started_at)} · {t('flowEditor.stepCount', { count: nodeCount })}
                </span>
              </span>
            </button>
            {canResume ? (
              <button
                type="button"
                onClick={() => onResume(run.run_group)}
                disabled={resumingRun === run.run_group}
                title={resumingRun === run.run_group ? t('flowEditor.resuming') : t('flowEditor.resume')}
                aria-label={resumingRun === run.run_group ? t('flowEditor.resuming') : t('flowEditor.resume')}
                className="shrink-0 rounded-md p-1 text-amber-600 transition hover:bg-amber-50 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <RotateCcw className="h-3.5 w-3.5" />
              </button>
            ) : null}
            {onDelete && (record.status || run.status) !== 'running' && !String(run.run_group).startsWith('legacy-') ? (
              <button
                type="button"
                onClick={() => onDelete(run)}
                title={isChat ? t('flowEditor.deleteChat') : t('flowEditor.deleteRun')}
                aria-label={isChat ? t('flowEditor.deleteChat') : t('flowEditor.deleteRun')}
                data-testid="flow-history-delete"
                className="shrink-0 rounded-md p-1 text-slate-300 transition hover:bg-rose-50 hover:text-rose-600"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export default FlowHistory;
