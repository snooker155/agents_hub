/**
 * A list of workspace file ids as chips, with "add from workspace files"
 * (WorkspaceFilePicker) and a remove button on each. Controlled: `value` is
 * the id list, `onChange` gets the new one. Used by the task form, the task
 * page and the eval case editor, which all store just the ids.
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { FileText, Paperclip, X } from 'lucide-react';
import { useI18n } from '../../i18n';
import { formatBytes, getWorkspaceFileRecord } from '../../api/files';
import WorkspaceFilePicker from './WorkspaceFilePicker';

export default function FileIdsField({ workspace, value = [], onChange, disabled = false, uploadSource }) {
  const { t } = useI18n();
  const [records, setRecords] = useState({});
  const [pickerOpen, setPickerOpen] = useState(false);
  const ids = value || [];
  // A string key, so a caller passing a fresh array each render does not
  // re-run the lookup below.
  const idsKey = ids.join('\n');

  // Names for ids that arrived without a record (an existing task's files).
  useEffect(() => {
    const missing = (idsKey ? idsKey.split('\n') : []).filter((id) => !(id in records));
    if (!missing.length) return undefined;
    let cancelled = false;
    Promise.all(missing.map((id) => getWorkspaceFileRecord(id)
      .then(({ data }) => [id, data])
      .catch(() => [id, null])))
      .then((pairs) => {
        if (cancelled) return;
        setRecords((prev) => ({ ...prev, ...Object.fromEntries(pairs) }));
      });
    return () => { cancelled = true; };
  }, [idsKey, records]);

  const add = (picked) => {
    setPickerOpen(false);
    setRecords((prev) => ({ ...prev, ...Object.fromEntries(picked.map((r) => [r.file_id, r])) }));
    const next = [...ids];
    for (const rec of picked) if (!next.includes(rec.file_id)) next.push(rec.file_id);
    onChange(next);
  };

  return (
    <div data-testid="file-ids-field">
      {ids.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {ids.map((id) => {
            const rec = records[id];
            return (
              <span
                key={id}
                className={`inline-flex items-center gap-1.5 max-w-full rounded-full border px-2 py-0.5 text-[11px] ${
                  rec === null ? 'border-red-200 bg-red-50 text-red-700' : 'border-gray-200 bg-gray-50 text-gray-700'
                }`}
                title={id}
              >
                <FileText className="w-3 h-3 shrink-0" />
                <Link to={`/files?file=${encodeURIComponent(id)}`} className="truncate max-w-[14rem] hover:underline">
                  {rec ? rec.name : rec === null ? `${id} (${t('files.field.missing')})` : id}
                </Link>
                {rec && <span className="text-gray-400">{formatBytes(rec.size)}</span>}
                {!disabled && (
                  <button
                    type="button"
                    onClick={() => onChange(ids.filter((x) => x !== id))}
                    className="text-gray-400 hover:text-red-600"
                    title={t('files.field.remove')}
                    aria-label={t('files.field.remove')}
                  >
                    <X className="w-3 h-3" />
                  </button>
                )}
              </span>
            );
          })}
        </div>
      )}
      {!disabled && (
        <button
          type="button"
          onClick={() => setPickerOpen(true)}
          className="inline-flex items-center gap-1.5 text-xs text-indigo-600 hover:text-indigo-800"
        >
          <Paperclip className="w-3.5 h-3.5" />
          {t('files.field.addFromWorkspace')}
        </button>
      )}
      {pickerOpen && (
        <WorkspaceFilePicker
          workspace={workspace}
          excludeIds={ids}
          uploadSource={uploadSource}
          onPick={add}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}
