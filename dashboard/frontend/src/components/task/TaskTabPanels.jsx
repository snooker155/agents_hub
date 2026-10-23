/**
 * Panels of the task tabs: subtasks, execution flow, activity, results and logs.
 */
import { Plus, Terminal } from 'lucide-react';
import PageLoader from '../PageLoader';
import ProcessGraph, { TokenPill } from '../ProcessGraph';
import { ActivityText } from './ActivityEntry';
import { confirmStatusChange, entryAgent } from './taskUtils';
import ResultBlock from './ResultBlock';
import { SubtaskRow } from './SubtaskParts';
import { useI18n } from '../../i18n';

// One card around the heading row and the list, like the Execution tab's.
export function SubtasksTab({ agents, deletingSubtasks, handleDeleteSubtask, openAssign, patch, setShowAddSubtask, subtasks, tasksById }) {
  const { t } = useI18n();
  return (
    <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
      {/* As tall as the heading alone (h-6), so the title sits where the other
          tabs' titles sit; the taller button overhangs into the card padding. */}
      <div className="flex items-center justify-between mb-4 h-6">
        <h3 className="text-base font-semibold text-gray-800">{t('taskDetails.subtasks')}</h3>
        <button
          onClick={() => setShowAddSubtask(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
        >
          <Plus className="w-4 h-4" /> {t('taskDetails.addSubtask')}
        </button>
      </div>

      {subtasks.length > 0 ? (
        <ul className="border-t border-gray-100 divide-y divide-gray-100">
          {subtasks.map(st => (
            <SubtaskRow
              key={st.id}
              st={st}
              tasksById={tasksById}
              agents={agents}
              onDelete={handleDeleteSubtask}
              onStatusChange={(stId, v) => { if (confirmStatusChange(t, st, v)) patch({ status: v }, stId); }}
              onAssign={(st) => openAssign(st)}
              deleting={!!deletingSubtasks[st.id]}
            />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-gray-400 italic">{t('taskDetails.noSubtasksYet')}</p>
      )}
    </div>
  );
}

// With runs to show it fills the tab area: the card takes its height and the
// flow scrolls inside the card, so neither the tab area nor the page scrolls
// around it. Empty, it is as tall as its message, like the other tabs' cards.
export function ExecutionTab({ flowLoading, flowRuns }) {
  const { t } = useI18n();
  const fill = flowLoading || flowRuns.length > 0;
  return (
    <div className={`${fill ? 'flex-1' : ''} flex flex-col bg-white shadow-sm border border-gray-200 rounded-xl p-6`}>
      {/* Execution flow pane — same agent-process view as the Chat page */}
      <div className="shrink-0 flex items-center justify-between gap-2 mb-4 flex-wrap">
        <h3 className="text-base font-semibold text-gray-800">{t('taskDetails.executionFlow')}</h3>
        {flowRuns.length > 0 && (
          <div className="flex flex-wrap gap-1">
            <TokenPill
              label={t('taskDetails.taskIn')}
              value={flowRuns.reduce((s, mr) => s + (Number(mr.inbound_tokens) || 0), 0)}
            />
            <TokenPill
              label={t('taskDetails.taskOut')}
              value={flowRuns.reduce((s, mr) => s + (Number(mr.outbound_tokens) || 0), 0)}
            />
            <TokenPill
              label={t('taskDetails.taskTotal')}
              value={flowRuns.reduce((s, mr) => s + (Number(mr.total_tokens) || ((Number(mr.inbound_tokens) || 0) + (Number(mr.outbound_tokens) || 0))), 0)}
            />
          </div>
        )}
      </div>
      {flowLoading ? (
        <PageLoader size="sm" label={t('taskDetails.loadingExecutionFlow')} />
      ) : flowRuns.length > 0 ? (
        // The page scrolls past the graph: a scroll box of its own here (with
        // overscroll-contain) swallowed the wheel over it.
        <div className="flex-1 min-h-[240px] pr-1">
          <ProcessGraph
            key={flowRuns.map((mr, idx) => `${mr.message_id || mr.run_id || idx}`).join('|')}
            messageRuns={flowRuns}
            titleByAgent
            followLast={false}
            newestFirst
          />
        </div>
      ) : (
        <p className="text-sm text-gray-400 italic">{t('taskDetails.noAgentRunsRecordedYet')}</p>
      )}
    </div>
  );
}

// `runs` (the task's execution log) name the agent of an entry written
// before entries recorded it (task/ActivityEntry.jsx entryAgent).
export function ActivityTab({ activityItems, runs = [] }) {
  const { t } = useI18n();
  return (
    <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
      <h3 className="text-base font-semibold text-gray-800 mb-4">{t('taskDetails.taskActivity')}</h3>
      {activityItems.length > 0 ? (
        <div className="space-y-3">
          {activityItems.map((item, idx) => {
            const ts = item?.timestamp ? new Date(item.timestamp).toLocaleString() : t('taskDetails.unknownTime');
            return (
              <div key={`${item?.timestamp || 't'}-${idx}`} className="border-l-2 border-gray-200 pl-3 py-1">
                <div className="text-xs text-gray-400 mb-0.5">{ts}</div>
                <div className="text-sm text-gray-700">
                  <ActivityText entry={item} agent={entryAgent(item, runs)} />
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-sm text-gray-400 italic">{t('taskDetails.noActivityEntriesYet')}</p>
      )}
    </div>
  );
}

export function ResultsTab({ hasResults, taskResults, onOpenFile }) {
  const { t } = useI18n();
  return (
    <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
      <h3 className="text-base font-semibold text-gray-800 mb-4">{t('taskDetails.agentResults')}</h3>
      {hasResults ? (
        // Each agent's result in a frame of its own, newest first.
        <div className="space-y-3">
          {[...taskResults].reverse().map((entry, idx) => (
            <ResultBlock key={`${entry.subtask?.id || ''}${entry.run_id || idx}`} entry={entry} onOpenFile={onOpenFile} />
          ))}
        </div>
      ) : (
        <p className="text-sm text-gray-400 italic">{t('taskDetails.noResultsCapturedForThis')}</p>
      )}
    </div>
  );
}

// `runs` are the task's runs that have a process log, newest first (the
// subtasks' too, marked); picking one shows its log.
export function LogsTab({ activeRunId, logs, runs = [], onSelectRun }) {
  const { t } = useI18n();
  const runLabel = (r) => [
    r.agent_id || 'agent',
    r.subtask ? (r.subtask.key || r.subtask.title) : '',
    r.started_at ? new Date(r.started_at).toLocaleString() : '',
    String(r.run_id).slice(0, 8),
  ].filter(Boolean).join(' · ');
  return (
    // As tall as the log: the page scrolls, not the block.
    <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden flex flex-col">
      <div className="bg-gray-800 px-4 py-2.5 flex items-center justify-between gap-3">
        <span className="flex items-center gap-2 text-gray-300 text-sm font-medium shrink-0">
          <Terminal className="w-4 h-4" /> {t('taskDetails.agentLogs')}
        </span>
        {runs.length > 1 && onSelectRun ? (
          <select
            value={activeRunId || ''}
            onChange={(e) => onSelectRun(e.target.value)}
            aria-label={t('taskDetails.run')}
            className="min-w-0 max-w-full truncate rounded-md border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-300 focus:outline-none"
          >
            {runs.map((r) => <option key={r.run_id} value={r.run_id}>{runLabel(r)}</option>)}
          </select>
        ) : activeRunId && (
          <span className="text-xs text-gray-500">{t('taskDetails.run')}: {activeRunId.slice(0, 8)}</span>
        )}
      </div>
      <div className="p-4 overflow-x-auto text-xs text-green-400 bg-black leading-relaxed">
        {logs
          ? <pre className="whitespace-pre-wrap">{logs}</pre>
          : <p className="text-gray-600 italic">{t('taskDetails.noLogsAvailable')}</p>
        }
      </div>
    </div>
  );
}
