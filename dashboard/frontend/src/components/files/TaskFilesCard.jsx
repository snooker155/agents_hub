/**
 * TaskFilesCard: the workspace files a task works from (Task.file_ids). Each
 * run copies them into its working directory (task_files/) and names them in
 * the prompt; subtasks inherit them. Editing replaces the list through
 * PATCH /api/tasks/{id}.
 */
import { useState } from 'react';
import { Paperclip } from 'lucide-react';
import { useI18n } from '../../i18n';
import { setTaskFileIds } from '../../api/files';
import { errorDetail } from '../toast';
import FileIdsField from './FileIdsField';

export default function TaskFilesCard({ task, onChanged }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (!task) return null;

  const save = async (ids) => {
    setBusy(true);
    setError('');
    try {
      await setTaskFileIds(task.id, ids);
      if (onChanged) onChanged();
    } catch (e) {
      setError(errorDetail(e) || t('files.task.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-white border border-gray-200 rounded-xl px-4 py-3 mb-6" data-testid="task-files-card">
      <div className="flex items-center gap-2 mb-1">
        <Paperclip className="w-4 h-4 text-gray-400 shrink-0" />
        <span className="text-sm font-semibold text-gray-800">{t('files.task.title')}</span>
        {busy && <span className="text-xs text-gray-400">…</span>}
      </div>
      <p className="text-xs text-gray-500 mb-2">{t('files.task.hint')}</p>
      {!(task.file_ids || []).length && <p className="text-xs text-gray-400 mb-1">{t('files.field.none')}</p>}
      <FileIdsField
        workspace={task.workspace}
        value={task.file_ids || []}
        onChange={save}
        disabled={busy}
        uploadSource="task"
      />
      {error && <p className="text-xs text-red-600 mt-1">{error}</p>}
    </div>
  );
}
