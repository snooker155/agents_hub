import React, { useState, useEffect } from 'react';
import { X } from 'lucide-react';
import { createTask, getProjects } from '../api';
import { useI18n } from '../i18n';

// Selectable initial statuses (mirrors the task board, minus the system-only
// "pending"/waiting-approval state which can't be set manually).
const STATUS_OPTIONS = [
  { value: 'todo' },
  { value: 'ready' },
  { value: 'in_progress' },
  { value: 'blocked' },
  { value: 'stopped' },
  { value: 'resolved' },
  { value: 'reviewing' },
  { value: 'reviewed' },
  { value: 'done' },
];

// Shared "Create New Task" modal used by the Tasks page and the Flow editor.
// onCreated receives the created task so callers that need its id (e.g. to
// attach it to a flow) can use it; callers that only refetch can ignore it.
export default function CreateTaskModal({ defaultStatus, selectedWorkspace, defaultProjectId, lockProject, onClose, onCreated }) {
  const { t } = useI18n();
  const [form, setForm] = useState({
    title: '', description: '',
    should_decompose: false,
    status: defaultStatus || 'todo',
    project_id: defaultProjectId || '',
  });
  const [loading, setLoading] = useState(false);
  const [projects, setProjects] = useState([]);

  // Scope the project list to the current workspace. The default workspace
  // (or an unset workspace) lists projects from every workspace.
  useEffect(() => {
    const wsParam = selectedWorkspace && selectedWorkspace !== 'default' ? selectedWorkspace : undefined;
    getProjects(wsParam)
      .then(r => setProjects(r.data))
      .catch(() => setProjects([]));
  }, [selectedWorkspace]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const resp = await createTask({ ...form, project_id: form.project_id || null, workspace_name: selectedWorkspace || null });
      onCreated?.(resp.data);
      onClose();
    } catch (err) {
      console.error('Error creating task:', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-lg max-w-md w-full p-6 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-xl font-bold text-gray-800">{t('createTaskModal.createNewTask')}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X className="w-5 h-5" /></button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('createTaskModal.title')}</label>
            <input
              type="text" required
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
              value={form.title}
              onChange={e => setForm({ ...form, title: e.target.value })}
            />
          </div>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('createTaskModal.description')}</label>
            <textarea
              rows="3"
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
              value={form.description}
              onChange={e => setForm({ ...form, description: e.target.value })}
            />
          </div>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('createTaskModal.initialStatus')}</label>
            <select
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
              value={form.status}
              onChange={e => setForm({ ...form, status: e.target.value })}
            >
              {STATUS_OPTIONS.map(({ value }) => (
                <option key={value} value={value}>{t(`taskStatus.${value}`)}</option>
              ))}
            </select>
          </div>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('createTaskModal.projectOptional')}</label>
            <select
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500 disabled:bg-gray-100 disabled:text-gray-500"
              value={form.project_id}
              disabled={lockProject}
              onChange={e => setForm({ ...form, project_id: e.target.value })}
            >
              <option value="">{t('createTaskModal.noProject')}</option>
              {projects.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div className="mb-6 flex items-center">
            <input
              id="decompose" type="checkbox"
              className="h-4 w-4 text-indigo-600 border-gray-300 rounded"
              checked={form.should_decompose}
              onChange={e => setForm({ ...form, should_decompose: e.target.checked })}
            />
            <label htmlFor="decompose" className="ml-2 text-sm text-gray-700">
              {t('createTaskModal.automaticallyDecomposeThisTask')}
            </label>
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">{t('createTaskModal.cancel')}</button>
            <button
              type="submit" disabled={loading}
              className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50"
            >
              {loading ? 'Creating…' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
