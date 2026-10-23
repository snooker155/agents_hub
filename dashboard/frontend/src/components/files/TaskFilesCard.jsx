/**
 * TaskFilesCard: the workspace files a task works from (Task.file_ids). Each
 * run copies them into its working directory (task_files/) and names them in
 * the prompt; subtasks inherit them. Editing replaces the list through
 * PATCH /api/tasks/{id}.
 */
import { useState } from 'react';
import { Paperclip, Plus } from 'lucide-react';
import { useI18n } from '../../i18n';
import { setTaskFileIds } from '../../api/files';
import { errorDetail } from '../toast';
import FileIdsField from './FileIdsField';
import { InfoHint } from '../PageLayout';

// `bare` drops the card frame and the "no files" note, for the task page's
// Files tab: there the header and the attached files sit on top of the one
// file tree, and the tab says once when there are no files at all.
export default function TaskFilesCard({ task, onChanged, bare = false }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  if (!task) return null;
  const ids = task.file_ids || [];

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

  // Laid out like the definition of done card: title, ⓘ for what the files
  // are for, and the add button on the right.
  return (
    <div className={bare ? '' : 'bg-white shadow-sm border border-gray-200 rounded-xl px-4 py-3 mb-6'} data-testid="task-files-card">
      <div className="flex items-center gap-3 flex-wrap">
        <Paperclip className="w-4 h-4 text-gray-400 shrink-0" />
        <span className={`${bare ? 'text-base' : 'text-sm'} font-semibold text-gray-800 inline-flex items-center gap-1`}>
          {t('files.task.title')}
          <InfoHint text={t('files.task.hint')} label={t('files.task.about')} className="font-normal" />
        </span>
        {!ids.length && !bare && <span className="text-xs text-gray-500">{t('files.field.none')}</span>}
        {busy && <span className="text-xs text-gray-400">…</span>}
        <button
          type="button"
          onClick={() => setPickerOpen(true)}
          disabled={busy}
          className="ml-auto inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >
          <Plus className="w-3.5 h-3.5" /> {t('files.task.add')}
        </button>
      </div>
      <div className={ids.length ? 'mt-2' : ''}>
        <FileIdsField
          workspace={task.workspace}
          value={ids}
          onChange={save}
          disabled={busy}
          uploadSource="task"
          showAdd={false}
          pickerOpen={pickerOpen}
          onPickerOpenChange={setPickerOpen}
        />
      </div>
      {error && <p className="text-xs text-red-600 mt-1">{error}</p>}
    </div>
  );
}
