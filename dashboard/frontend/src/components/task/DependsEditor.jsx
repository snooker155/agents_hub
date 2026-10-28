/**
 * The modal that sets what a task depends on: the chosen tasks with a remove
 * button each, and a search over the workspace's other tasks to add one. The
 * server refuses a cycle; its answer is shown here and nothing is saved.
 */
import { useMemo, useState } from 'react';
import { Hourglass, X } from 'lucide-react';
import SearchSelect from '../SearchSelect';
import { StatusBadge } from './TaskFields';
import { updateTask } from '../../api';
import { useI18n } from '../../i18n';

/**
 * @param {object} props
 * @param {object} props.task        the task being edited.
 * @param {object[]} props.candidates the workspace's tasks (the task itself is left out here).
 * @param {function} props.onSaved   after the change is stored.
 * @param {function} props.onClose
 */
export default function DependsEditor({ task, candidates, onSaved, onClose }) {
  const { t } = useI18n();
  const [ids, setIds] = useState(() => [...(task.depends || [])]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const byId = useMemo(() => new Map(candidates.map((c) => [c.id, c])), [candidates]);
  const items = useMemo(() => candidates
    .filter((c) => c.id !== task.id && !ids.includes(c.id))
    .map((c) => ({ id: c.id, name: c.title, subtitle: [c.key, c.parent_id ? t('taskDetails.subtaskWord') : null].filter(Boolean).join(' · ') })),
  [candidates, ids, task.id, t]);

  const changed = ids.length !== (task.depends || []).length
    || ids.some((id) => !(task.depends || []).includes(id));

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      await updateTask(task.id, { depends: ids });
      onSaved();
      onClose();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-lg max-w-md w-full p-6 shadow-xl">
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-lg font-bold text-gray-800">{t('taskDetails.dependsEditorTitle')}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label={t('taskDetails.cancel')}>
            <X className="w-5 h-5" />
          </button>
        </div>
        <p className="text-sm text-gray-500 mb-4">{t('taskDetails.dependsEditorHint')}</p>

        <ul className="mb-3 flex flex-col gap-1.5" data-testid="depends-list">
          {ids.length === 0 && <li className="text-sm text-gray-400 italic">{t('taskDetails.noDepends')}</li>}
          {ids.map((id) => {
            const dep = byId.get(id);
            return (
              <li key={id} className="flex items-center gap-2 px-2.5 py-1.5 rounded-md border border-gray-200">
                <Hourglass className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />
                {dep?.key && <span className="text-xs font-semibold text-gray-400 flex-shrink-0">{dep.key}</span>}
                <span className="text-sm text-gray-800 truncate flex-1 min-w-0">{dep?.title || id}</span>
                {dep && <StatusBadge status={dep.status} size="xs" />}
                <button
                  type="button"
                  onClick={() => setIds((list) => list.filter((x) => x !== id))}
                  className="p-0.5 text-gray-400 hover:text-red-600 rounded"
                  aria-label={t('taskDetails.removeDependency')}
                  title={t('taskDetails.removeDependency')}
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </li>
            );
          })}
        </ul>

        <label className="block text-sm font-medium text-gray-700 mb-1">{t('taskDetails.addDependency')}</label>
        <SearchSelect
          testId="depends-picker"
          items={items}
          value=""
          onChange={(id) => setIds((list) => (list.includes(id) ? list : [...list, id]))}
          placeholder={t('taskDetails.chooseTask')}
          searchPlaceholder={t('taskDetails.searchTasks')}
          noMatchText={t('taskBoard.noMatch')}
          emptyText={t('taskDetails.noOtherTasks')}
        />

        {error && <p className="text-sm text-red-600 mt-3">{error}</p>}
        <div className="flex justify-end gap-3 mt-5">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">{t('taskDetails.cancel')}</button>
          <button
            onClick={save}
            disabled={!changed || saving}
            className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50"
          >
            {saving ? t('taskDetails.saving') : t('taskDetails.save')}
          </button>
        </div>
      </div>
    </div>
  );
}
