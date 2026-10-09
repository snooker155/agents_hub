/**
 * Panels of the task tabs: subtasks, execution flow, activity, results and logs.
 */
import { Plus, Terminal } from 'lucide-react';
import PageLoader from '../PageLoader';
import ProcessGraph, { TokenPill } from '../ProcessGraph';
import ResultBlock from './ResultBlock';
import { SubtaskRow } from './SubtaskParts';
import { useI18n } from '../../i18n';

export function SubtasksTab({ agents, deletingSubtasks, handleDeleteSubtask, openAssign, patch, setShowAddSubtask, subtasks }) {
  const { t } = useI18n();
  return (
    <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-base font-semibold text-gray-800">{t('taskDetails.subtasks')}</h3>
        <button
          onClick={() => setShowAddSubtask(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
        >
          <Plus className="w-4 h-4" /> {t('taskDetails.addSubtask')}
        </button>
      </div>

      {subtasks.length > 0 ? (
        <div className="space-y-2 mt-3">
          {subtasks.map(st => (
            <SubtaskRow
              key={st.id}
              st={st}
              agents={agents}
              onDelete={handleDeleteSubtask}
              onStatusChange={(stId, v) => patch({ status: v }, stId)}
              onAssign={(st) => openAssign(st)}
              deleting={!!deletingSubtasks[st.id]}
            />
          ))}
        </div>
      ) : (
        <p className="text-sm text-gray-400 italic mt-3">
          No subtasks yet. Add one manually or use "Decompose" to auto-generate them.
        </p>
      )}
    </div>
  );
}

export function ExecutionTab({ flowHeight, flowLoading, flowRuns, flowScrollRef }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-6">
      {/* Execution flow pane — same agent-process view as the Chat page */}
      <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
        <div className="flex items-center justify-between gap-2 mb-4 flex-wrap">
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
          <div
            ref={flowScrollRef}
            className="overflow-auto pr-1"
            style={{ height: flowHeight ? `${flowHeight}px` : 'calc(100vh - 240px)' }}
          >
            <ProcessGraph
              key={flowRuns.map((mr, idx) => `${mr.message_id || mr.run_id || idx}`).join('|')}
              messageRuns={flowRuns}
              titleByAgent
            />
          </div>
        ) : (
          <p className="text-sm text-gray-400 italic">{t('taskDetails.noAgentRunsRecordedYet')}</p>
        )}
      </div>
    </div>
  );
}

export function ActivityTab({ activityItems }) {
  const { t } = useI18n();
  return (
    <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
      <h3 className="text-base font-semibold text-gray-800 mb-4">{t('taskDetails.taskActivity')}</h3>
      {activityItems.length > 0 ? (
        <div className="space-y-3">
          {activityItems.map((item, idx) => {
            const ts = item?.timestamp ? new Date(item.timestamp).toLocaleString() : t('taskDetails.unknownTime');
            const text = item?.message || item?.type || t('taskDetails.activityUpdate');
            return (
              <div key={`${item?.timestamp || 't'}-${idx}`} className="border-l-2 border-indigo-200 pl-3 py-1">
                <div className="text-xs text-gray-400">{ts}</div>
                <div className="text-sm text-gray-700">{text}</div>
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

export function ResultsTab({ activeRunId, hasResults, taskResults }) {
  const { t } = useI18n();
  return (
    <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-base font-semibold text-gray-800">{t('taskDetails.agentResults')}</h3>
        {activeRunId && <span className="text-xs text-gray-400">{t('taskDetails.run')}: {activeRunId}</span>}
      </div>
      {hasResults ? (
        <div className="space-y-3">
          {[...taskResults].reverse().map((entry, idx) => (
            <ResultBlock key={entry.run_id || idx} entry={entry} />
          ))}
        </div>
      ) : (
        <p className="text-sm text-gray-400 italic">{t('taskDetails.noResultsCapturedForThis')}</p>
      )}
    </div>
  );
}

export function LogsTab({ activeRunId, logs }) {
  const { t } = useI18n();
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden flex flex-col h-[500px]">
      <div className="bg-gray-800 px-4 py-2.5 flex items-center justify-between">
        <span className="flex items-center gap-2 text-gray-300 text-sm font-medium">
          <Terminal className="w-4 h-4" /> {t('taskDetails.agentLogs')}
        </span>
        {activeRunId && (
          <span className="text-xs text-gray-500">{t('taskDetails.run')}: {activeRunId.slice(0, 8)}</span>
        )}
      </div>
      <div className="p-4 flex-1 overflow-auto text-xs text-green-400 bg-black leading-relaxed">
        {logs
          ? <pre className="whitespace-pre-wrap">{logs}</pre>
          : <p className="text-gray-600 italic">{t('taskDetails.noLogsAvailable')}</p>
        }
      </div>
    </div>
  );
}
