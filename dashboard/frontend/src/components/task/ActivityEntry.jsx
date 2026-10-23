/**
 * One task activity entry in plain words: a sentence for its type, the
 * statuses as badges, and the agent that was working on the task.
 */
import { Bot } from 'lucide-react';
import { StatusBadge } from './TaskFields';
import { gradedAgent } from './taskUtils';
import { useI18n } from '../../i18n';

// Entries written before statuses were stored by value read "TaskStatus.todo".
const bareStatus = (s) => String(s || '').replace(/^TaskStatus\./, '');

// The sentence for each entry type; a type not listed shows its own message.
const SENTENCES = {
  created: 'created',
  agent_assigned: 'agentAssigned',
  agent_cleared: 'agentCleared',
  container_agent: 'containerAgent',
  container_activated: 'containerActivated',
  container_completed: 'containerCompleted',
  container_subtask_done: 'subtaskDone',
  outcome_retry: 'outcomeRetry',
  run_failed: 'runFailed',
  run_retry: 'runRetry',
  paused: 'paused',
  resumed: 'resumed',
  awaiting_input: 'awaitingInput',
  awaiting_approval: 'awaitingApproval',
  auto_review: 'reviewStarted',
  review_passed: 'reviewPassed',
  dependency_block: 'dependencyBlock',
  dependency_release: 'dependencyRelease',
  history_cleared: 'historyCleared',
};

// The text of an entry, without its time: what happened, the agent beside it.
export function ActivityText({ entry, agent }) {
  const { t } = useI18n();
  const type = entry?.type;
  let body;
  if (type === 'status_change' && entry.from && entry.to) {
    body = (
      <span className="inline-flex items-center gap-1.5 flex-wrap">
        {t('taskDetails.activityEntry.statusChanged')}
        <StatusBadge status={bareStatus(entry.from)} size="xs" />
        <span className="text-gray-400">→</span>
        <StatusBadge status={bareStatus(entry.to)} size="xs" />
      </span>
    );
  } else if (type === 'outcome_graded') {
    const score = Math.round(Number(entry.score || 0) * 100);
    const failed = /grader failed/.test(entry.message || '');
    const of = gradedAgent(entry);
    body = (
      <span className="inline-flex items-center gap-1.5 flex-wrap">
        {of ? t('taskDetails.activityEntry.gradedWorkOf', { agent: of }) : t('taskDetails.activityEntry.graded')}
        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
          failed ? 'bg-gray-100 text-gray-600'
            : entry.passed ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
        }`}>
          {failed ? t('taskDetails.activityEntry.graderFailed')
            : t(entry.passed ? 'taskDetails.activityEntry.met' : 'taskDetails.activityEntry.notMet', { score })}
        </span>
      </span>
    );
  } else if (type === 'person_checks' && Array.isArray(entry.checks)) {
    // Checks the decomposer left for a person: no agent here could make them.
    body = (
      <span className="inline-flex flex-col gap-0.5">
        <span>{t('taskDetails.activityEntry.personChecks')}</span>
        <ul className="list-disc pl-5 text-gray-600">
          {entry.checks.map((c) => <li key={c}>{c}</li>)}
        </ul>
      </span>
    );
  } else if (type === 'self_reported_blocked') {
    body = t('taskDetails.activityEntry.selfBlocked', { reason: entry.reason || '' });
  } else if (SENTENCES[type]) {
    body = t(`taskDetails.activityEntry.${SENTENCES[type]}`);
    // Some sentences go on from the agent's name ("swe_agent took the
    // task"); without one they start the line.
    if (!agent) body = body.charAt(0).toUpperCase() + body.slice(1);
  } else {
    body = bareStatus(entry?.message || type || t('taskDetails.activityUpdate')).replace(/TaskStatus\./g, '');
  }
  return (
    <span className="inline-flex items-center gap-2 flex-wrap min-w-0">
      {agent && (
        <span className="inline-flex items-center gap-1 text-xs font-medium text-gray-700 bg-gray-100 rounded px-1.5 py-0.5">
          <Bot className="w-3 h-3 text-gray-400" /> {agent}
        </span>
      )}
      <span>{body}</span>
    </span>
  );
}
