import { MessageSquare, RotateCcw, SquareTerminal, Workflow } from 'lucide-react';
import { useI18n } from '../../i18n';
import { RUN_STATUS_STYLES, fmtDateTime } from './runFormat';

export function FlowRunMessages({ run, messages = [], onClose }) {
  const { t } = useI18n();
  const isChat = run?.kind === 'chat';
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
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg px-2.5 py-1 text-xs font-semibold text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
        >
          {t('flowEditor.close')}
        </button>
      </div>

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
                <div className="flex shrink-0 flex-col items-center gap-1">
                  <div
                    className={`flex h-7 w-7 items-center justify-center rounded-full text-white ${
                      isUser ? 'bg-indigo-600' : 'bg-gray-800'
                    }`}
                  >
                    {isUser ? <SquareTerminal className="h-3.5 w-3.5" /> : <Workflow className="h-3.5 w-3.5" />}
                  </div>
                  {!isUser && msg.agent_label && (
                    <span className="max-w-[52px] break-words text-center text-[9px] font-medium leading-tight text-gray-400">
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

export function FlowHistory({ runs = [], instances = {}, onResume, resumingRun, selectedRunGroup, onSelect }) {
  const { t } = useI18n();
  if (!runs.length) {
    return (
      <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
        {t('flowEditor.noPreviousRunsYetRun')}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {runs.map((run) => {
        const isActive = run.run_group === selectedRunGroup;
        const isChat = run.kind === 'chat';
        const record = instances[run.run_group] || {};
        const canResume = RESUMABLE_STATUSES.has(record.status) && !!record.checkpoint;
        const status = RUN_STATUS_STYLES[record.status] || RUN_STATUS_STYLES[run.status] || RUN_STATUS_STYLES.running;
        const nodeCount = (run.events || []).filter((e) => e.type === 'agent_start').length;
        return (
          <div
            key={run.run_group}
            className={`w-full rounded-2xl border px-4 py-3 text-left transition ${
              isActive
                ? 'border-cyan-300 bg-cyan-50'
                : 'border-slate-200 bg-slate-50 hover:border-cyan-300 hover:bg-cyan-50'
            }`}
          >
          <button type="button" onClick={() => onSelect(run)} className="w-full text-left">
            <div className="flex items-center justify-between gap-2">
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${
                  isChat ? 'bg-indigo-100 text-indigo-700' : 'bg-emerald-100 text-emerald-700'
                }`}
              >
                {isChat ? <MessageSquare className="h-3 w-3" /> : <Workflow className="h-3 w-3" />}
                {isChat ? t('flowEditor.chat') : t('flowEditor.taskRun')}
              </span>
              <span className={`inline-flex items-center gap-1 text-[11px] font-medium ${status.text}`}>
                <span className={`h-1.5 w-1.5 rounded-full ${status.dot}`} />
                {status.label}
              </span>
            </div>
            <div className="mt-1.5 truncate text-sm font-semibold text-slate-900">
              {run.title || (isChat ? t('flowEditor.flowChat') : t('flowEditor.flowRun'))}
            </div>
            <div className="mt-0.5 text-[11px] text-slate-400">
              {fmtDateTime(run.started_at)} · {t('flowEditor.stepCount', { count: nodeCount })}
            </div>
          </button>
          {canResume ? (
            <button
              type="button"
              onClick={() => onResume(run.run_group)}
              disabled={resumingRun === run.run_group}
              className="mt-2 inline-flex items-center gap-1.5 rounded-xl border border-amber-200 bg-amber-50 px-3 py-1.5 text-[11px] font-semibold text-amber-700 transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RotateCcw className="h-3 w-3" />
              {resumingRun === run.run_group ? t('flowEditor.resuming') : t('flowEditor.resume')}
            </button>
          ) : null}
          </div>
        );
      })}
    </div>
  );
}

export default FlowHistory;
