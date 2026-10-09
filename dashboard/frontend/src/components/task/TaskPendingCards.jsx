/**
 * Cards shown while a task waits: an answer, a tool call approval or a budget decision.
 */
import { DollarSign, HelpCircle, ShieldQuestion } from 'lucide-react';
import { fmtUsd } from './taskUtils';
import { useI18n } from '../../i18n';

export function AwaitingInputCard({ answerDraft, answerSubmitting, handleAnswerTask, setAnswerDraft, task }) {
  const { t } = useI18n();
  return (
    <div className="mt-4 p-4 bg-amber-50 border border-amber-200 rounded-lg">
      <p className="text-sm text-amber-800 font-semibold flex items-center gap-1.5">
        <HelpCircle className="w-4 h-4" /> {t('taskDetails.theAgentNeedsYourInput')}
      </p>
      <p className="text-sm text-amber-900 mt-1 whitespace-pre-wrap">
        {task.pending_question?.question || t('taskDetails.waitingForAnswer')}
      </p>
      {Array.isArray(task.pending_question?.choices) && task.pending_question.choices.length > 0 && (
        <div className="flex flex-wrap gap-2 mt-3">
          {task.pending_question.choices.map((c, i) => (
            <button
              key={i}
              type="button"
              disabled={answerSubmitting}
              onClick={() => { setAnswerDraft(c); }}
              className={`px-3 py-1 rounded-full border text-sm transition-colors disabled:opacity-50 ${
                answerDraft === c
                  ? 'border-amber-500 bg-amber-200 text-amber-900'
                  : 'border-amber-300 bg-white text-amber-800 hover:bg-amber-100'
              }`}
            >
              {c}
            </button>
          ))}
        </div>
      )}
      <div className="flex items-end gap-2 mt-3">
        <textarea
          value={answerDraft}
          onChange={(e) => setAnswerDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); handleAnswerTask(); }
          }}
          rows={2}
          placeholder={t('taskDetails.typeYourAnswerCtrlEnter')}
          className="flex-1 px-3 py-2 text-sm border border-amber-300 rounded-lg focus:outline-none resize-y"
        />
        <button
          type="button"
          disabled={answerSubmitting || !answerDraft.trim()}
          onClick={handleAnswerTask}
          className="px-4 py-2 rounded-lg bg-amber-500 text-white text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
        >
          {answerSubmitting ? t('taskDetails.sending') : t('taskDetails.sendAndResume')}
        </button>
      </div>
    </div>
  );
}

export function BudgetPauseCard({ approvalSubmitting, budgetCapDraft, handleBudgetDecision, setBudgetCapDraft, task }) {
  const { t } = useI18n();
  return (
    <div className="mt-4 p-4 bg-amber-50 border border-amber-200 rounded-lg">
      <p className="text-sm text-amber-800 font-semibold flex items-center gap-1.5">
        <DollarSign className="w-4 h-4" /> {t('taskDetails.pausedAtMoneyCap')}
      </p>
      <p className="text-sm text-amber-900 mt-1">
        {t('taskDetails.budgetPauseDetail', {
          spent: fmtUsd(task.pending_approval?.spent_usd),
          limit: fmtUsd(task.pending_approval?.limit_usd),
        })}
      </p>
      {task.pending_approval?.reason && (
        <p className="text-sm text-amber-900 mt-1 whitespace-pre-wrap">{task.pending_approval.reason}</p>
      )}
      <div className="flex items-center gap-2 mt-3">
        <label className="text-xs font-medium text-amber-700 uppercase tracking-wide">{t('taskDetails.newCapUsd')}</label>
        <input
          type="number"
          min="0"
          step="0.01"
          value={budgetCapDraft}
          onChange={(e) => setBudgetCapDraft(e.target.value)}
          className="w-28 px-2 py-1.5 text-sm border border-amber-300 rounded-lg focus:outline-none"
        />
        <button
          type="button"
          disabled={approvalSubmitting || !budgetCapDraft || Number(budgetCapDraft) <= Number(task.pending_approval?.spent_usd || 0)}
          onClick={() => handleBudgetDecision(true)}
          className="px-4 py-2 rounded-lg bg-amber-500 text-white text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
        >
          {approvalSubmitting ? t('taskDetails.sending') : t('taskDetails.continueTask')}
        </button>
        <button
          type="button"
          disabled={approvalSubmitting}
          onClick={() => handleBudgetDecision(false)}
          className="px-4 py-2 rounded-lg border border-amber-300 bg-white text-amber-800 text-sm font-semibold hover:bg-amber-100 disabled:opacity-50"
        >
          {t('taskDetails.stopTask')}
        </button>
      </div>
    </div>
  );
}

export function ToolApprovalCard({ approvalNote, approvalSubmitting, handleApprovalDecision, setApprovalNote, task }) {
  const { t } = useI18n();
  return (
    <div className="mt-4 p-4 bg-amber-50 border border-amber-200 rounded-lg">
      <p className="text-sm text-amber-800 font-semibold flex items-center gap-1.5">
        <ShieldQuestion className="w-4 h-4" /> {t('taskDetails.theAgentNeedsApproval')}
      </p>
      <p className="text-sm text-amber-900 mt-1">
        <code className="px-1.5 py-0.5 rounded bg-amber-100 font-mono text-xs">
          {task.pending_approval?.tool || '—'}
        </code>
      </p>
      {task.pending_approval?.reason && (
        <p className="text-sm text-amber-900 mt-1 whitespace-pre-wrap">{task.pending_approval.reason}</p>
      )}
      <pre className="mt-2 p-2 bg-white border border-amber-200 rounded text-xs text-gray-800 overflow-x-auto">
        {JSON.stringify(task.pending_approval?.input ?? {}, null, 2)}
      </pre>
      <div className="flex items-center gap-2 mt-3">
        <input
          type="text"
          value={approvalNote}
          onChange={(e) => setApprovalNote(e.target.value)}
          placeholder={t('taskDetails.approvalNotePlaceholder')}
          className="flex-1 px-3 py-2 text-sm border border-amber-300 rounded-lg focus:outline-none"
        />
        <button
          type="button"
          disabled={approvalSubmitting}
          onClick={() => handleApprovalDecision(true)}
          className="px-4 py-2 rounded-lg bg-amber-500 text-white text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
        >
          {approvalSubmitting ? t('taskDetails.sending') : t('taskDetails.approveCall')}
        </button>
        <button
          type="button"
          disabled={approvalSubmitting}
          onClick={() => handleApprovalDecision(false)}
          className="px-4 py-2 rounded-lg border border-amber-300 bg-white text-amber-800 text-sm font-semibold hover:bg-amber-100 disabled:opacity-50"
        >
          {t('taskDetails.denyCall')}
        </button>
      </div>
    </div>
  );
}
