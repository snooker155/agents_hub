import { useState } from 'react';
import { FlaskConical, HelpCircle, MessageSquare, RotateCcw, Send, Trash2, Workflow } from 'lucide-react';
import { useI18n } from '../../i18n';
import { RUN_STATUS_STYLES, fmtDateTime, rerunBubbleText, routeText } from './runFormat';
import AgentTranscript from '../chat/AgentTranscript';
import SaveAsEvalCaseDialog from '../evals/SaveAsEvalCaseDialog';

const NO_LIVE = [];

// The question a run parked on (a human_interrupt node), while it waits: the
// last line of its log. A run on a task is answered on the task too; a run
// without one only here.
function pendingQuestion(run, status) {
  if (status !== 'awaiting_input') return null;
  const last = (run?.events || []).at(-1);
  return last?.type === 'flow_interrupt' ? last : null;
}

function AnswerBox({ question, onAnswer }) {
  const { t } = useI18n();
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const send = async (value) => {
    const answer = String(value ?? text).trim();
    if (!answer || sending) return;
    setSending(true);
    try {
      await onAnswer(answer);
      setText('');
    } finally {
      setSending(false);
    }
  };
  return (
    <div className="mx-auto mb-4 max-w-3xl rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3" data-testid="flow-run-question">
      <div className="mb-2 flex items-start gap-2 text-sm text-amber-900">
        <HelpCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
        <span className="whitespace-pre-wrap">{question.question || question.content}</span>
      </div>
      {(question.choices || []).length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {question.choices.map((c) => (
            <button key={c} type="button" disabled={sending} onClick={() => send(c)}
              className="rounded-lg border border-amber-300 bg-white px-2.5 py-1 text-xs font-medium text-amber-800 transition hover:bg-amber-100 disabled:opacity-50">
              {c}
            </button>
          ))}
        </div>
      )}
      <div className="flex items-end gap-2">
        <textarea
          rows={2} value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder={t('flowEditor.answerPlaceholder')}
          className="min-w-0 flex-1 resize-none rounded-xl border border-amber-200 bg-white px-3 py-2 text-sm text-gray-800 focus:outline-none"
        />
        <button type="button" onClick={() => send()} disabled={!text.trim() || sending}
          title={t('flowEditor.sendAnswer')} aria-label={t('flowEditor.sendAnswer')}
          className="rounded-xl bg-amber-600 p-2 text-white transition hover:bg-amber-700 disabled:opacity-50">
          <Send className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

// `live`: the nodes of a running run still at work, as replies being written
// (components/runs/liveTurns.js); a run's stored messages only have the
// nodes that ended.
export function FlowRunMessages({ run, messages: given = [], live = NO_LIVE, onClose, workspace, status = null, onAnswer = null }) {
  const { t } = useI18n();
  const isChat = run?.kind === 'chat';
  const [caseDialogOpen, setCaseDialogOpen] = useState(false);
  // A run again from a node has no message of its own (runFormat.js).
  // A switch's bubble says by name where it sent the run (routeText).
  const messages = given.map((m) => {
    if (m.rerun && !m.content) return { ...m, content: rerunBubbleText(t, m.rerun) };
    if (m.route && !m.content) return { ...m, content: routeText(t, m.route) };
    return m;
  });
  const liveShown = live.map((m) => (m.route && !m.content ? { ...m, content: routeText(t, m.route) } : m));
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
        {onAnswer && pendingQuestion(run, status) ? (
          <AnswerBox question={pendingQuestion(run, status)} onAnswer={onAnswer} />
        ) : null}
        {messages.length === 0 && !live.length ? (
          <div className="flex h-full min-h-[200px] flex-col items-center justify-center text-center text-sm text-slate-500">
            {t('flowEditor.thisRunProducedNoMessages')}
          </div>
        ) : (
          // Drawn as every chat is (AgentTranscript): Markdown, files, and the
          // node still working as a reply being written.
          <AgentTranscript
            messages={liveShown.length ? [...messages, ...liveShown] : messages}
            viewMode="chat"
            agentNameFor={(m) => m.agent_label || ''}
          />
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
      <div className="p-3">
        <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
          {t('flowEditor.noPreviousRunsYetRun')}
        </div>
      </div>
    );
  }
  // A compact list, one row per record across the whole column (no card
  // around it): the kind as an icon, the title and the status dot on top,
  // the kind, time and step count under it.
  return (
    <ul className="divide-y divide-slate-100 border-b border-slate-100 bg-white">
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
            className={`flex items-center gap-1 pr-2 transition ${isActive ? 'bg-cyan-50' : 'hover:bg-slate-50'}`}
          >
            <button type="button" onClick={() => onSelect(run)} className="flex min-w-0 flex-1 items-start gap-2 px-3 py-2 text-left">
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
