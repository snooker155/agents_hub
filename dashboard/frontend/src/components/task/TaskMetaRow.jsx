/**
 * Parent, dependencies and key facts row of the task card.
 */
import { ExternalLink, GitBranch } from 'lucide-react';
import { Link } from 'react-router-dom';
import { PriorityBadge, StatusBadge } from './TaskFields';
import { executorLabel } from './taskUtils';
import { useI18n } from '../../i18n';

export function TaskMetaRow({ depTasks, parentTask, task }) {
  const { t } = useI18n();
  return (
    <div className="mt-4 pt-4 border-t border-gray-100 flex flex-col gap-2">
      {/* Parent task info */}
      {parentTask && (
        <Link
          to={`/tasks/${parentTask.id}`}
          className="flex items-center gap-3 group hover:no-underline"
        >
          <div className="flex items-center gap-1.5 text-xs text-gray-400 flex-shrink-0">
            <GitBranch className="w-3.5 h-3.5" />
            {t('taskDetails.parentTask')}
          </div>
          <div className="flex items-center gap-2 min-w-0">
            <StatusBadge status={parentTask.status} size="xs" />
            <span className="text-sm font-medium text-gray-700 group-hover:text-indigo-600 truncate transition-colors">
              {parentTask.title}
            </span>
            {parentTask.priority && <PriorityBadge priority={parentTask.priority} />}
            {parentTask.assigned_agent_type && (
              <span className="flex items-center gap-1 text-xs text-gray-400 flex-shrink-0">
                {(() => { const { Icon } = executorLabel(parentTask); return <Icon className="w-3 h-3" />; })()}
                {executorLabel(parentTask).id}
              </span>
            )}
          </div>
          <ExternalLink className="w-3.5 h-3.5 text-gray-300 group-hover:text-indigo-500 ml-auto flex-shrink-0 transition-colors" />
        </Link>
      )}
      {/* Dependencies — tasks that must complete before this one runs */}
      {depTasks.length > 0 && (
        <div className="flex items-start gap-3">
          <div className="flex items-center gap-1.5 text-xs text-gray-400 flex-shrink-0 mt-0.5">
            <GitBranch className="w-3.5 h-3.5" />
            {t('taskDetails.dependsOn')}
          </div>
          <div className="flex flex-col gap-1 min-w-0">
            {depTasks.map(dep => (
              <Link key={dep.id} to={`/tasks/${dep.id}`} className="flex items-center gap-2 group hover:no-underline min-w-0">
                <StatusBadge status={dep.status} size="xs" />
                {dep.key && <span className="text-xs font-semibold text-gray-400 flex-shrink-0">{dep.key}</span>}
                <span className="text-sm font-medium text-gray-700 group-hover:text-indigo-600 truncate transition-colors">
                  {dep.title}
                </span>
              </Link>
            ))}
          </div>
        </div>
      )}
      <div className="flex items-center gap-4 flex-wrap">
      {task.key && <span className="text-xs text-gray-400">{t('taskDetails.key')} <span className="font-semibold text-gray-500">{task.key}</span></span>}
      <span className="text-xs text-gray-400">{t('taskDetails.id')} <span className="">{task.id}</span></span>
      <span className="text-xs text-gray-400">{t('taskDetails.createdAt')}: {new Date(task.created_at).toLocaleString()}</span>
      {task.assigned_agent_type && (
        <span className="flex items-center gap-1 text-xs text-gray-500">
          {(() => { const { Icon } = executorLabel(task); return <Icon className="w-3.5 h-3.5" />; })()} {executorLabel(task).id}
          {executorLabel(task).kind !== 'agent' && (
            <span className="px-1 py-0.5 rounded text-[10px] font-medium bg-indigo-50 text-indigo-600 border border-indigo-100">
              {executorLabel(task).kind}
            </span>
          )}
          <span className={`ml-1 px-1.5 py-0.5 rounded text-xs font-medium ${
            task.agent_state === 'running' ? 'bg-blue-100 text-blue-700' :
            task.agent_state === 'completed' ? 'bg-green-100 text-green-700' :
            task.agent_state === 'pending_approval' ? 'bg-amber-100 text-amber-700' :
            task.agent_state === 'pending' ? 'bg-yellow-100 text-yellow-700' :
            'bg-gray-100 text-gray-600'
          }`}>{task.agent_state === 'pending_approval' ? t('taskDetails.awaitingApproval') : task.agent_state}</span>
        </span>
      )}
      </div>
    </div>
  );
}
