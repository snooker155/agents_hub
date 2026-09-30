import { Clock } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useAgentPage } from './context';

/** The tasks assigned to this agent. */
export default function TasksTab() {
  const { agentTasks, t } = useAgentPage();
  return (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-lg font-bold mb-4 flex items-center">
            <Clock className="w-5 h-5 mr-2 text-indigo-600" />
            {t('agentDetails.tasksAssignedToThisAgent')}
          </h3>
          {agentTasks.length === 0 ? (
            <p className="text-sm text-gray-500 italic">{t('agentDetails.noTasksAssignedToThis')}</p>
          ) : (
            <div className="space-y-3">
              {agentTasks
                .slice()
                .sort((a, b) => {
                  const aRunning = a.agent_state === 'running' ? 1 : 0;
                  const bRunning = b.agent_state === 'running' ? 1 : 0;
                  if (aRunning !== bRunning) return bRunning - aRunning;
                  const aTs = new Date(a.created_at || 0).getTime() || 0;
                  const bTs = new Date(b.created_at || 0).getTime() || 0;
                  return bTs - aTs;
                })
                .map((task) => (
                  <div key={task.id} className="border border-gray-100 rounded-lg p-3 bg-gray-50">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <Link to={`/tasks/${task.id}`} className="text-sm font-semibold text-indigo-700 hover:underline truncate block">
                          {task.title || task.id}
                        </Link>
                        <div className="text-xs text-gray-500 mt-1 truncate">{task.id}</div>
                        <div className="text-xs text-gray-500 mt-1">{t('agentDetails.workspace2')} <span className="">{task.workspace || '—'}</span></div>
                      </div>
                      <div className="flex flex-col items-end gap-1">
                        <span className={`text-xs px-2 py-0.5 rounded font-semibold capitalize ${
                          task.agent_state === 'running'
                            ? 'bg-blue-100 text-blue-700'
                            : 'bg-gray-200 text-gray-700'
                        }`}>
                          agent: {task.agent_state || 'unknown'}
                        </span>
                        <span className={`text-xs px-2 py-0.5 rounded font-semibold capitalize ${
                          task.status === 'done'
                            ? 'bg-green-100 text-green-700'
                            : task.status === 'blocked'
                              ? 'bg-red-100 text-red-700'
                              : 'bg-gray-200 text-gray-700'
                        }`}>
                          task: {task.status || 'unknown'}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
            </div>
          )}
        </div>
  );
}
