/**
 * Pick workspace files (docs/files.md) by id: the one dialog the chat
 * composer, the memory page, a task and an eval case open to reuse a file
 * that is already stored, instead of uploading it again. A new file can be
 * uploaded from here too; it lands in the workspace files and is selected.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, FileText, Loader, Search, Upload, X } from 'lucide-react';
import { useI18n } from '../../i18n';
import { formatBytes, listWorkspaceFiles, uploadWorkspaceFileObject } from '../../api/files';
import { errorDetail } from '../toast';
import PageLoader from '../PageLoader';

export default function WorkspaceFilePicker({
  workspace, onPick, onClose, multiple = true, excludeIds = [], allowUpload = true, uploadSource,
}) {
  const { t } = useI18n();
  const [q, setQ] = useState('');
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState([]);
  const inputRef = useRef(null);
  const excluded = new Set(excludeIds || []);

  const load = useCallback(async (query) => {
    if (!workspace) return;
    setLoading(true);
    try {
      const { data } = await listWorkspaceFiles(workspace, { q: query, limit: 200 });
      setFiles(data?.files || []);
      setError('');
    } catch (e) {
      setError(errorDetail(e) || t('files.errors.load'));
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }, [workspace, t]);

  // A short pause after typing, so each keystroke does not hit the server.
  useEffect(() => {
    const id = setTimeout(() => load(q), 200);
    return () => clearTimeout(id);
  }, [q, load]);

  const toggle = (rec) => {
    if (excluded.has(rec.file_id)) return;
    if (!multiple) {
      onPick([rec]);
      return;
    }
    setSelected((prev) => (prev.some((r) => r.file_id === rec.file_id)
      ? prev.filter((r) => r.file_id !== rec.file_id)
      : [...prev, rec]));
  };

  const onUpload = async (e) => {
    const picked = Array.from(e.target.files || []);
    e.target.value = '';
    if (!picked.length || !workspace) return;
    setUploading(true);
    setError('');
    const added = [];
    for (const file of picked) {
      try {
        const { data } = await uploadWorkspaceFileObject(workspace, file, { source: uploadSource });
        added.push(data);
      } catch (err) {
        setError(errorDetail(err) || t('files.errors.upload', { name: file.name }));
      }
    }
    setUploading(false);
    if (!added.length) return;
    if (!multiple) {
      onPick([added[0]]);
      return;
    }
    setSelected((prev) => [...prev, ...added.filter((r) => !prev.some((p) => p.file_id === r.file_id))]);
    load(q);
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50" role="dialog"
      aria-label={t('files.picker.title')}>
      <div className="bg-white rounded-xl max-w-lg w-full shadow-xl flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <h3 className="text-base font-semibold text-gray-800">{t('files.picker.title')}</h3>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-600"
            aria-label={t('files.picker.cancel')}>
            <X className="w-5 h-5" />
          </button>
        </div>

        {!workspace ? (
          <p className="px-5 py-8 text-sm text-gray-500 text-center">{t('files.picker.noWorkspace')}</p>
        ) : (
          <>
            <div className="px-5 pt-4 flex items-center gap-2">
              <div className="flex-1 flex items-center gap-2 border border-gray-300 rounded-lg px-3 py-1.5">
                <Search className="w-4 h-4 text-gray-400" />
                <input
                  autoFocus
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder={t('files.picker.search')}
                  className="flex-1 text-sm focus:outline-none bg-transparent"
                />
              </div>
              {allowUpload && (
                <>
                  <input ref={inputRef} type="file" multiple={multiple} className="hidden" onChange={onUpload}
                    data-testid="picker-upload-input" />
                  <button
                    type="button"
                    onClick={() => inputRef.current?.click()}
                    disabled={uploading}
                    title={t('files.picker.uploadNew')}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-gray-200 rounded-lg text-gray-600 hover:bg-gray-50 disabled:opacity-50"
                  >
                    {uploading ? <Loader className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                    <span className="hidden sm:inline">{t('files.upload')}</span>
                  </button>
                </>
              )}
            </div>
            {error && <p className="px-5 pt-2 text-xs text-red-600">{error}</p>}
            <div className="flex-1 overflow-y-auto px-2 py-3 min-h-[8rem]">
              {loading && files.length === 0 ? (
                <PageLoader size="sm" />
              ) : files.length === 0 ? (
                <p className="text-sm text-gray-500 text-center py-8">{t('files.picker.empty')}</p>
              ) : (
                <ul>
                  {files.map((rec) => {
                    const isExcluded = excluded.has(rec.file_id);
                    const isSelected = selected.some((r) => r.file_id === rec.file_id);
                    return (
                      <li key={rec.file_id}>
                        <button
                          type="button"
                          onClick={() => toggle(rec)}
                          disabled={isExcluded}
                          className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left transition-colors ${
                            isSelected ? 'bg-indigo-50' : 'hover:bg-gray-50'
                          } disabled:opacity-50 disabled:cursor-not-allowed`}
                        >
                          <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${
                            isSelected ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-gray-300'
                          }`}>
                            {isSelected && <Check className="w-3 h-3" />}
                          </span>
                          <FileText className="w-4 h-4 text-gray-400 shrink-0" />
                          <span className="flex-1 min-w-0">
                            <span className="block text-sm text-gray-800 truncate">{rec.name}</span>
                            <span className="block text-[11px] text-gray-400">
                              {formatBytes(rec.size)} · {t(`files.sources.${rec.source}`, { defaultValue: rec.source })}
                              {isExcluded ? ` · ${t('files.picker.alreadyAdded')}` : ''}
                            </span>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </>
        )}

        {multiple && (
          <div className="flex justify-end gap-3 px-5 py-3 border-t border-gray-100">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">
              {t('files.picker.cancel')}
            </button>
            <button
              type="button"
              disabled={selected.length === 0}
              onClick={() => onPick(selected)}
              className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50"
            >
              {selected.length ? t('files.picker.addCount', { count: selected.length }) : t('files.picker.add')}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
