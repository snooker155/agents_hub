/**
 * Files: the workspace files of the selected workspace (docs/files.md).
 *
 * A file uploaded here (or saved by an agent, or kept from a chat) gets an id
 * that the chat composer, the memory page, a task and an eval case all
 * reference, so it is stored once however many places use it. The page lists
 * them with search and a source filter, takes uploads by drag and drop,
 * previews text, PDFs (as their extracted text) and images, downloads, shows
 * where a file is used and deletes it after saying what the deletion leaves
 * pointing at nothing. `/files?file=<id>` opens that file's panel, which is
 * where citations and chat links land.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  Copy, Download, Eye, FileText, FolderOpen, Loader, RefreshCw, Search, Trash2, Upload, X,
} from 'lucide-react';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useWorkspace } from '../components/workspace';
import { useI18n, useFormatters } from '../i18n';
import { useToast, errorDetail } from '../components/toast';
import {
  deleteWorkspaceFileObject, formatBytes, getWorkspaceFileBlob, getWorkspaceFileRecord,
  getWorkspaceFileText, getWorkspaceFileUsage, listWorkspaceFiles, saveBlobAs,
  uploadWorkspaceFileObject,
} from '../api/files';

const SOURCES = ['upload', 'chat', 'agent', 'memory', 'task', 'eval', 'api'];

function isImage(rec) {
  return /^image\/(png|jpe?g|gif|webp|bmp)$/.test(rec?.mime_type || '');
}

function UsageList({ usage, t }) {
  if (!usage) return null;
  if (!usage.total && !usage.produced_by) {
    return <p className="text-xs text-gray-500">{t('files.notUsed')}</p>;
  }
  const groups = [
    ['usedChats', usage.chats, (c) => (
      <Link to={`/chat/${encodeURIComponent(c.conversation_id)}`} className="text-indigo-600 hover:underline">
        {c.title || t('files.untitledChat')}
      </Link>
    ), (c) => `chat:${c.conversation_id}`],
    ['usedMemory', usage.memory_pools, (p) => (
      <Link to="/memory" className="text-indigo-600 hover:underline">{p.name}</Link>
    ), (p) => `pool:${p.pool_id}`],
    ['usedTasks', usage.tasks, (task) => (
      <Link to={`/tasks/${task.task_id}`} className="text-indigo-600 hover:underline">
        {task.key ? `${task.key}: ` : ''}{task.title}
      </Link>
    ), (task) => `task:${task.task_id}`],
    ['usedEvals', usage.eval_cases, (c) => (
      <Link to={`/evals?set=${encodeURIComponent(c.eval_set_id)}`} className="text-indigo-600 hover:underline">
        {c.name}: {c.input || c.case_id}
      </Link>
    ), (c) => `case:${c.case_id}`],
  ];
  return (
    <div className="space-y-2">
      {usage.produced_by && (
        <p className="text-xs text-gray-600">
          {t('files.producedBy', { agent: usage.produced_by.agent_id || '—' })}
          {usage.produced_by.run_id && (
            <>
              {' · '}
              <Link to={`/messages/${usage.produced_by.run_id}`} className="text-indigo-600 hover:underline">
                {t('files.openRun')}
              </Link>
            </>
          )}
        </p>
      )}
      {groups.filter(([, items]) => (items || []).length).map(([label, items, render, key]) => (
        <div key={label}>
          <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">{t(`files.${label}`)}</div>
          <ul className="mt-0.5 space-y-0.5 text-xs">
            {items.map((item) => <li key={key(item)} className="truncate">{render(item)}</li>)}
          </ul>
        </div>
      ))}
    </div>
  );
}

function FilePanel({ fileId, onClose, onDeleted }) {
  const { t } = useI18n();
  const { formatDate } = useFormatters();
  const toast = useToast();
  const [rec, setRec] = useState(null);
  const [usage, setUsage] = useState(null);
  const [preview, setPreview] = useState(null);
  const [imageUrl, setImageUrl] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let objectUrl = '';
    setLoading(true);
    setError('');
    setPreview(null);
    setImageUrl('');
    setUsage(null);
    (async () => {
      try {
        const { data } = await getWorkspaceFileRecord(fileId);
        if (cancelled) return;
        setRec(data);
        getWorkspaceFileUsage(fileId)
          .then(({ data: u }) => { if (!cancelled) setUsage(u); })
          .catch(() => { if (!cancelled) setUsage(null); });
        if (isImage(data)) {
          const { data: blob } = await getWorkspaceFileBlob(fileId);
          if (cancelled) return;
          objectUrl = URL.createObjectURL(blob);
          setImageUrl(objectUrl);
        } else {
          const { data: text } = await getWorkspaceFileText(fileId, 20000);
          if (!cancelled) setPreview(text);
        }
      } catch (e) {
        if (!cancelled) setError(errorDetail(e) || t('files.errors.preview'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [fileId, t]);

  const download = async () => {
    try {
      const { data } = await getWorkspaceFileBlob(fileId);
      saveBlobAs(data, rec?.name);
    } catch (e) {
      toast.error(t('files.errors.download'), errorDetail(e));
    }
  };

  const remove = async () => {
    if (!rec) return;
    const total = usage?.total || 0;
    const question = total
      ? t('files.confirmDelete', { name: rec.name, count: total })
      : t('files.confirmDeleteUnused', { name: rec.name });
    if (!window.confirm(question)) return;
    try {
      await deleteWorkspaceFileObject(fileId);
      toast.success(t('files.deleted', { name: rec.name }));
      onDeleted(fileId);
    } catch (e) {
      toast.error(t('files.errors.delete'), errorDetail(e));
    }
  };

  const copyId = async () => {
    try {
      await navigator.clipboard.writeText(fileId);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  };

  return (
    <aside className="bg-white rounded-xl border border-gray-200 p-4 space-y-4" data-testid="file-panel">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-gray-800 break-all">{rec?.name || fileId}</h3>
          <button type="button" onClick={copyId}
            className="mt-0.5 inline-flex items-center gap-1 text-[11px] font-mono text-gray-400 hover:text-gray-600"
            title={t('files.copyId')}>
            {fileId} <Copy className="w-3 h-3" /> {copied && <span className="font-sans">{t('files.copied')}</span>}
          </button>
        </div>
        <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label={t('files.close')}>
          <X className="w-4 h-4" />
        </button>
      </div>

      {rec && (
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={download}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-200 rounded-lg text-gray-700 hover:bg-gray-50">
            <Download className="w-3.5 h-3.5" /> {t('files.download')}
          </button>
          <button type="button" onClick={remove}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-200 rounded-lg text-red-600 hover:bg-red-50">
            <Trash2 className="w-3.5 h-3.5" /> {t('files.delete')}
          </button>
        </div>
      )}

      {rec && (
        <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-gray-400">{t('files.mimeType')}</dt><dd className="text-gray-700 break-all">{rec.mime_type}</dd>
          <dt className="text-gray-400">{t('files.columns.size')}</dt><dd className="text-gray-700">{formatBytes(rec.size)}</dd>
          <dt className="text-gray-400">{t('files.columns.source')}</dt>
          <dd className="text-gray-700">{t(`files.sources.${rec.source}`, { defaultValue: rec.source })}</dd>
          <dt className="text-gray-400">{t('files.columns.created')}</dt><dd className="text-gray-700">{formatDate(rec.created_at)}</dd>
          {rec.created_by && (<><dt className="text-gray-400">{t('files.createdBy')}</dt><dd className="text-gray-700 break-all">{rec.created_by}</dd></>)}
          <dt className="text-gray-400">{t('files.sha256')}</dt><dd className="text-gray-500 font-mono break-all">{rec.sha256}</dd>
        </dl>
      )}

      <div>
        <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400 mb-1">{t('files.preview')}</div>
        {loading ? (
          <p className="text-xs text-gray-400 flex items-center gap-1.5"><Loader className="w-3.5 h-3.5 animate-spin" /> {t('files.loadingPreview')}</p>
        ) : error ? (
          <p className="text-xs text-red-600">{error}</p>
        ) : imageUrl ? (
          <img src={imageUrl} alt={rec?.name || ''} className="max-h-80 rounded border border-gray-100" />
        ) : preview && preview.text != null ? (
          <>
            {preview.kind === 'pdf' && <p className="text-[11px] text-gray-400 mb-1">{t('files.previewPdf')}</p>}
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-[11px] text-gray-700" data-testid="file-preview">
              {preview.text}
            </pre>
            {preview.truncated && <p className="text-[11px] text-gray-400 mt-1">{t('files.previewTruncated')}</p>}
          </>
        ) : (
          <p className="text-xs text-gray-500">{t('files.previewBinary')}</p>
        )}
      </div>

      <div>
        <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400 mb-1">{t('files.whereUsed')}</div>
        {usage ? <UsageList usage={usage} t={t} /> : <p className="text-xs text-gray-400">…</p>}
      </div>
    </aside>
  );
}

export default function Files() {
  const { t } = useI18n();
  const { formatDate } = useFormatters();
  const toast = useToast();
  const { selectedWorkspace } = useWorkspace() || {};
  const workspace = selectedWorkspace || 'default';
  const [searchParams, setSearchParams] = useSearchParams();
  const openId = searchParams.get('file') || '';
  const [q, setQ] = useState('');
  const [source, setSource] = useState('');
  const [files, setFiles] = useState([]);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await listWorkspaceFiles(workspace, { q, source, limit: 500 });
      setFiles(data?.files || []);
      setMeta({ usage: data?.usage_bytes || 0, limits: data?.limits || {} });
    } catch (e) {
      setFiles([]);
      toast.error(t('files.errors.load'), errorDetail(e));
    } finally {
      setLoading(false);
    }
  }, [workspace, q, source, toast, t]);

  useEffect(() => {
    const id = setTimeout(load, 200);
    return () => clearTimeout(id);
  }, [load]);

  const openFile = (fileId) => {
    const next = new URLSearchParams(searchParams);
    if (fileId) next.set('file', fileId); else next.delete('file');
    setSearchParams(next, { replace: !fileId });
  };

  const uploadAll = async (list) => {
    const picked = Array.from(list || []);
    if (!picked.length) return;
    setUploading(true);
    let lastId = '';
    for (const file of picked) {
      try {
        const { data } = await uploadWorkspaceFileObject(workspace, file);
        lastId = data.file_id;
        if (data.deduplicated) toast.success(t('files.deduplicated', { name: file.name, existing: data.name }));
        else toast.success(t('files.uploaded', { name: file.name }));
      } catch (e) {
        toast.error(t('files.errors.upload', { name: file.name }), errorDetail(e));
      }
    }
    setUploading(false);
    await load();
    if (picked.length === 1 && lastId) openFile(lastId);
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    uploadAll(e.dataTransfer?.files);
  };

  const limits = meta?.limits || {};
  const usageLine = useMemo(() => {
    if (!meta || !limits.max_workspace_bytes) return '';
    return t('files.usage', { used: formatBytes(meta.usage), limit: formatBytes(limits.max_workspace_bytes) });
  }, [meta, limits.max_workspace_bytes, t]);

  return (
    <PageContainer className="space-y-5">
      <PageHeader
        icon={FolderOpen}
        title={t('files.title')}
        description={t('files.pageDescription')}
        actions={<>
          <button type="button" onClick={load}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> {t('files.refresh')}
          </button>
          <button type="button" onClick={() => inputRef.current?.click()} disabled={uploading}
            className="flex items-center gap-2 px-3 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
            {uploading ? <Loader className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
            {uploading ? t('files.uploading') : t('files.upload')}
          </button>
        </>}
      />
      <input ref={inputRef} type="file" multiple className="hidden" data-testid="files-upload-input"
        onChange={(e) => { uploadAll(e.target.files); e.target.value = ''; }} />

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        data-testid="files-dropzone"
        className={`border-2 border-dashed rounded-xl px-6 py-6 text-center cursor-pointer transition-colors ${
          dragging ? 'border-indigo-400 bg-indigo-50' : 'border-gray-200 bg-white hover:border-indigo-300'
        }`}
      >
        <Upload className={`w-7 h-7 mx-auto mb-2 ${dragging ? 'text-indigo-500' : 'text-gray-300'}`} />
        <p className="text-sm text-gray-600">{t('files.dropHere')}</p>
        <p className="text-xs text-gray-400 mt-1">
          {limits.max_file_bytes ? t('files.maxFile', { size: formatBytes(limits.max_file_bytes) }) : ''}
          {usageLine ? ` · ${usageLine}` : ''}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex-1 min-w-[12rem] flex items-center gap-2 bg-white border border-gray-300 rounded-lg px-3 py-2">
          <Search className="w-4 h-4 text-gray-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('files.searchPlaceholder')}
            className="flex-1 text-sm bg-transparent focus:outline-none" />
        </div>
        <select value={source} onChange={(e) => setSource(e.target.value)} aria-label={t('files.columns.source')}
          className="border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white">
          <option value="">{t('files.allSources')}</option>
          {SOURCES.map((s) => <option key={s} value={s}>{t(`files.sources.${s}`)}</option>)}
        </select>
      </div>

      <div className={`grid gap-5 ${openId ? 'lg:grid-cols-[minmax(0,1fr)_24rem]' : ''}`}>
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          {loading && files.length === 0 ? (
            <div className="flex justify-center py-12"><Loader className="w-6 h-6 animate-spin text-indigo-500" /></div>
          ) : files.length === 0 ? (
            <div className="text-center py-12">
              <FileText className="w-10 h-10 text-gray-300 mx-auto mb-3" />
              <p className="text-sm text-gray-500">{q || source ? t('files.noMatches') : t('files.empty')}</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 text-xs text-gray-500 uppercase tracking-wider">
                    <th className="px-4 py-2 text-left">{t('files.columns.name')}</th>
                    <th className="px-4 py-2 text-left">{t('files.columns.source')}</th>
                    <th className="px-4 py-2 text-left">{t('files.columns.size')}</th>
                    <th className="px-4 py-2 text-left">{t('files.columns.created')}</th>
                    <th className="px-4 py-2 text-right">{t('files.columns.actions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {files.map((f) => (
                    <tr key={f.file_id} className={`hover:bg-gray-50 ${openId === f.file_id ? 'bg-indigo-50/60' : ''}`}>
                      <td className="px-4 py-2.5">
                        <button type="button" onClick={() => openFile(f.file_id)}
                          className="flex items-center gap-2 text-left text-gray-800 hover:text-indigo-700">
                          <FileText className="w-4 h-4 text-gray-400 shrink-0" />
                          <span className="truncate max-w-[22rem]">{f.name}</span>
                        </button>
                      </td>
                      <td className="px-4 py-2.5 text-xs text-gray-500">{t(`files.sources.${f.source}`, { defaultValue: f.source })}</td>
                      <td className="px-4 py-2.5 text-xs text-gray-500">{formatBytes(f.size)}</td>
                      <td className="px-4 py-2.5 text-xs text-gray-400">{formatDate(f.created_at)}</td>
                      <td className="px-4 py-2.5 text-right">
                        <button type="button" onClick={() => openFile(f.file_id)}
                          className="p-1 text-gray-400 hover:text-indigo-600" title={t('files.preview')} aria-label={t('files.preview')}>
                          <Eye className="w-4 h-4" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        {openId && (
          <FilePanel
            key={openId}
            fileId={openId}
            onClose={() => openFile('')}
            onDeleted={() => { openFile(''); load(); }}
          />
        )}
      </div>
    </PageContainer>
  );
}
