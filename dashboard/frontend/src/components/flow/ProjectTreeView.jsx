import React, { useState, useEffect } from 'react';
import { getWorkspace } from '../../api';
import { useStream } from '../stream';
import { useI18n } from '../../i18n';

function ProjectTreeView({ workspace }) {
  const { t } = useI18n();
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(false);
  const { on } = useStream();

  useEffect(() => {
    if (!workspace) return;

    const fetchData = async () => {
      setLoading(true);
      try {
        const res = await getWorkspace(workspace);
        setTasks(res.data.tasks || []);
      } catch {
        // console.error("Failed to fetch project data", e);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    return on('app', (ev) => { if (ev.type === 'tasks.changed') fetchData(); });
  }, [workspace, on]);

  const groupedTasks = tasks.reduce((acc, task) => {
    const assignee = task.assigned_agent_type || 'Unassigned';
    if (!acc[assignee]) acc[assignee] = [];
    acc[assignee].push(task);
    return acc;
  }, {});

  if (!workspace) {
    return <div className="text-gray-500 italic">{t('flowProjectTreeView.selectAWorkspaceToSee')}</div>;
  }

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-medium text-gray-900 border-b pb-2">{t('flowProjectTreeView.projectOverview')}</h3>
      <div className="space-y-4">
        {Object.entries(groupedTasks).map(([assignee, assigneeTasks]) => (
          <div key={assignee} className="space-y-2">
            <div className="text-sm font-bold text-indigo-600 uppercase tracking-wider">{assignee}</div>
            <ul className="space-y-2 pl-4 border-l-2 border-gray-100">
              {assigneeTasks.map(task => (
                <li key={task.id} className="text-sm">
                  <div className="flex justify-between">
                    <span className="font-medium">{task.title}</span>
                    <span className={`text-xs px-2 py-0.5 rounded-full ${
                      task.status === 'done' ? 'bg-green-100 text-green-800' :
                      task.status === 'in_progress' ? 'bg-blue-100 text-blue-800' : 'bg-gray-100 text-gray-800'
                    }`}>
                      {task.status}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ))}
        {tasks.length === 0 && !loading && (
          <p className="text-sm text-gray-500 italic">{t('flowProjectTreeView.noTasksFoundInThis')}</p>
        )}
      </div>
    </div>
  );
}

export default ProjectTreeView;
