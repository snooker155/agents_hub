/**
 * The task card's rows: key facts (TaskKeyRow) and links to other tasks (TaskLinksRow).
 */
import { ExternalLink, GitBranch, Hourglass, Lock, Pencil } from 'lucide-react';
import { Link } from 'react-router-dom';
import { PriorityBadge, StatusBadge } from './TaskFields';
import { executorLabel } from './taskUtils';
import { useI18n } from '../../i18n';

// One linked task as key, title and status.
function TaskLink({ task, linkState }) {
  return (
    <Link to={`/tasks/${task.id}`} state={linkState} className="flex items-center gap-2 group hover:no-underline min-w-0">
      {task.key && <span className="text-xs font-semibold text-gray-400 flex-shrink-0">{task.key}</span>}
      <span className="text-sm font-medium text-gray-700 group-hover:text-indigo-600 truncate transition-colors">
        {task.title}
      </span>
      <StatusBadge status={task.status} size="xs" />
    </Link>
  );
}

// The task's links to other tasks: its parent, what it depends on and what
// waits for it, each as title then status. `onEditDepends` adds the button
// that changes the dependencies, and keeps the "Depends on" line even while
// there are none. Renders nothing when there is nothing to show. `linkState`
// is the router state the links carry on (task/taskBack.js).
export function TaskLinksRow({ depTasks, dependents = [], parentTask, linkState, onEditDepends, className = '' }) {
  const { t } = useI18n();
  if (!parentTask && depTasks.length === 0 && dependents.length === 0 && !onEditDepends) return null;
  return (
    <div className={`${className} flex flex-col gap-2`}>
      {/* Parent task info */}
      {parentTask && (
        <Link
          to={`/tasks/${parentTask.id}`}
          state={linkState}
          className="flex items-center gap-3 group hover:no-underline"
        >
          <div className="flex items-center gap-1.5 text-xs text-gray-400 flex-shrink-0">
            <GitBranch className="w-3.5 h-3.5" />
            {t('taskDetails.parentTask')}
          </div>
          <div className="flex items-center gap-2 min-w-0">
            {parentTask.key && <span className="text-xs font-semibold text-gray-400 flex-shrink-0">{parentTask.key}</span>}
            <span className="text-sm font-medium text-gray-700 group-hover:text-indigo-600 truncate transition-colors">
              {parentTask.title}
            </span>
            <StatusBadge status={parentTask.status} size="xs" />
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
      {/* Dependencies: tasks that must complete before this one runs */}
      {(depTasks.length > 0 || onEditDepends) && (
        <div className="flex items-start gap-3" data-testid="depends-on">
          <div className="flex items-center gap-1.5 text-xs text-gray-400 flex-shrink-0 mt-0.5">
            <Hourglass className="w-3.5 h-3.5" />
            {t('taskDetails.dependsOn')}
          </div>
          <div className="flex flex-col gap-1 min-w-0">
            {depTasks.length === 0 && <span className="text-sm text-gray-400">{t('taskDetails.noDepends')}</span>}
            {depTasks.map(dep => <TaskLink key={dep.id} task={dep} linkState={linkState} />)}
          </div>
          {onEditDepends && (
            <button
              type="button"
              onClick={onEditDepends}
              className="ml-auto flex items-center gap-1 text-xs text-gray-400 hover:text-indigo-600 flex-shrink-0"
              title={t('taskDetails.editDepends')}
            >
              <Pencil className="w-3.5 h-3.5" /> {t('taskDetails.editDepends')}
            </button>
          )}
        </div>
      )}
      {/* The other way round: tasks that wait for this one */}
      {dependents.length > 0 && (
        <div className="flex items-start gap-3" data-testid="blocks">
          <div className="flex items-center gap-1.5 text-xs text-gray-400 flex-shrink-0 mt-0.5">
            <Lock className="w-3.5 h-3.5" />
            {t('taskDetails.blocks')}
          </div>
          <div className="flex flex-col gap-1 min-w-0">
            {dependents.map(d => <TaskLink key={d.id} task={d} linkState={linkState} />)}
          </div>
        </div>
      )}
    </div>
  );
}

// Key, id, creation time and the executor with its state: the first line of
// the task card.
export function TaskKeyRow({ task }) {
  const { t } = useI18n();
  return (
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
  );
}
