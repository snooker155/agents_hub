import React, { useState, useMemo, useEffect } from 'react';
import { Search, Folder } from 'lucide-react';
import { getProjects } from '../../api';
import { useI18n } from '../../i18n';

function FlowTaskList({ tasks, taskId, onPick, workspace }) {
  const { t } = useI18n();
  const [searchQuery, setSearchQuery] = useState('');
  const [projects, setProjects] = useState([]);

  useEffect(() => {
    if (!workspace) return;
    getProjects(workspace)
      .then((response) => {
        setProjects(response.data || []);
      })
      .catch(() => {
        // Silently ignore errors loading projects
      });
  }, [workspace]);

  const projectsById = useMemo(
    () => Object.fromEntries(projects.map((p) => [p.id, p.name])),
    [projects]
  );

  const needle = searchQuery.trim().toLowerCase();
  const filteredTasks = useMemo(() => {
    if (!needle) return tasks;
    return tasks.filter((task) => {
      const key = task.key ? String(task.key).toLowerCase() : '';
      const title = String(task.title).toLowerCase();
      const projectName = (task.project_id && projectsById[task.project_id]) || task.project || null;
      const project = projectName ? String(projectName).toLowerCase() : '';
      return key.includes(needle) || title.includes(needle) || project.includes(needle);
    });
  }, [tasks, needle, projectsById]);

  const getProjectName = (task) => (task.project_id && projectsById[task.project_id]) || task.project || null;

  if (tasks.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-center text-sm text-slate-400">
        {t('flowEditor.noTasksYetCreateOne')}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400 pointer-events-none" />
        <input
          type="search"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder={t('flowEditor.searchTasks')}
          aria-label={t('flowEditor.searchTasks')}
          className="w-full border border-gray-300 rounded-md pl-8 pr-2 py-1.5 text-sm"
        />
      </div>

      {filteredTasks.length === 0 ? (
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-4 text-center text-sm text-slate-400">
          {t('flowEditor.noTasksMatch')}
        </div>
      ) : (
        // Rows across the whole column (past its padding), one under the
        // other with a rule between, each title in full. Only colours the
        // theme maps for dark mode too (src/theme.css), so hover and the
        // rules read the same in both.
        <ul className="-mx-3 divide-y divide-gray-200 border-y border-gray-200" data-testid="flow-task-rows">
          {filteredTasks.map((task) => {
            const isAttached = String(task.id) === String(taskId);
            const projectName = getProjectName(task);
            return (
              <li key={task.id}>
                <button
                  type="button"
                  onClick={() => onPick(task)}
                  title={task.description || task.title}
                  aria-pressed={isAttached}
                  className={`w-full border-l-[3px] px-3 py-2 text-left transition-colors ${
                    // The theme maps gray-100 to a shade a step from the dark
                    // column's own, so dark mode gets its own hover and
                    // selection tints.
                    isAttached
                      ? 'border-cyan-500 bg-cyan-50 dark:bg-cyan-400/25'
                      : 'border-transparent hover:bg-gray-100 dark:hover:bg-white/15'
                  }`}
                >
                  <div className={`text-sm leading-5 break-words ${isAttached ? 'text-cyan-800 dark:text-cyan-200' : 'text-gray-800 dark:text-gray-100'}`}>
                    {task.key && (
                      <span className="mr-1.5 text-xs font-semibold text-gray-400 dark:text-gray-400">{task.key}</span>
                    )}
                    {task.title}
                  </div>
                  {projectName && (
                    <span className="mt-1 inline-flex max-w-full items-center gap-0.5 rounded px-1.5 py-0.5 text-xs bg-indigo-50 text-indigo-700 border border-indigo-100">
                      <Folder className="w-3 h-3 flex-shrink-0" />
                      <span className="truncate">{projectName}</span>
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default FlowTaskList;
