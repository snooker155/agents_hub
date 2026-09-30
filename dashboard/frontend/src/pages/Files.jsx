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
 *
 * A file an agent writes into the workspace folder with its filesystem tools
 * is registered as it is written; "Sync from folder" registers the rest of
 * the folder (files from before the registry, or written by a shell) and
 * drops the records of files that are gone.
 *
 * Two views of the same list: a tree by the files' paths in the workspace
 * (folders collapsed until opened, so a project with hundreds of files stays
 * readable; a search opens every folder that has a match) and a flat table.
 * The choice is remembered per browser. A row opens the file's panel; the eye
 * button opens the file rendered (markdown, image, PDF, HTML, text) in a
 * modal. The list and the panel take the height left on the screen and
 * scroll on their own.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useSearchParams } from 'react-router-dom';
import {
  ChevronDown, ChevronRight, Copy, Download, Eye, FileText, Folder, FolderOpen, FolderSync, List, ListTree,
  Loader, RefreshCw, Search, Trash2, Upload, X,
} from 'lucide-react';
import { PageContainer, PageHeader } from '../components/PageLayout';
import MarkdownRenderer from '../components/MarkdownRenderer';
import CodeBlock from '../components/CodeBlock';
import { codeLanguageFor } from '../lib/codeLanguage';
import { useWorkspace } from '../components/workspace';
import { useI18n, useFormatters } from '../i18n';
import { useToast, errorDetail } from '../components/toast';
import {
  deleteWorkspaceFileObject, formatBytes, getWorkspaceFileBlob, getWorkspaceFileRecord,
  getWorkspaceFileText, getWorkspaceFileUsage, indexWorkspaceFiles, listWorkspaceFiles, saveBlobAs,
  uploadWorkspaceFileObject,
} from '../api/files';
import PageLoader from '../components/PageLoader';

const SOURCES = ['upload', 'chat', 'agent', 'memory', 'task', 'eval', 'api'];

const VIEW_KEY = 'files.view';

function readView() {
  try {
    const v = localStorage.getItem(VIEW_KEY);
    return v === 'list' || v === 'tree' ? v : 'tree';
  } catch {
    return 'tree';
  }
}

/** The files as folders by their workspace path (an upload with no path sits at the root). */
function buildTree(files) {
  const mk = (name, path) => ({ name, path, dirs: new Map(), files: [] });
  const root = mk('', '');
  for (const f of files) {
    const parts = String(f.meta?.path || f.name || '').split('/').filter(Boolean);
    let node = root;
    for (let i = 0; i < parts.length - 1; i += 1) {
      const seg = parts[i];
      if (!node.dirs.has(seg)) node.dirs.set(seg, mk(seg, node.path ? `${node.path}/${seg}` : seg));
      node = node.dirs.get(seg);
    }
    node.files.push({ ...f, leaf: parts[parts.length - 1] || f.name });
  }
  const finish = (node) => {
    node.children = [...node.dirs.values()].sort((a, b) => a.name.localeCompare(b.name)).map(finish);
    node.files.sort((a, b) => a.leaf.localeCompare(b.leaf));
    node.count = node.files.length + node.children.reduce((sum, c) => sum + c.count, 0);
    node.bytes = node.files.reduce((sum, f) => sum + (f.size || 0), 0)
      + node.children.reduce((sum, c) => sum + c.bytes, 0);
    return node;
  };
  return finish(root);
}

function allDirPaths(node, out = []) {
  for (const c of node.children) { out.push(c.path); allDirPaths(c, out); }
  return out;
}

function TreeFile({ file, depth, openId, openFile, onPreview, t, formatDate }) {
  const active = openId === file.file_id;
  return (
    <div
      className={`flex items-center gap-2 pr-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 ${active ? 'bg-indigo-50/60' : ''}`}
      style={{ paddingLeft: `${12 + depth * 18 + 18}px` }}
      data-testid="tree-file"
      onClick={() => openFile(file.file_id)}
    >
      <span className="flex-1 min-w-0 flex items-center gap-2 text-gray-800">
        <FileText className="w-4 h-4 text-gray-400 shrink-0" />
        <span className="truncate">{file.leaf}</span>
      </span>
      <span className="hidden sm:inline text-xs text-gray-500 w-16 truncate">
        {t(`files.sources.${file.source}`, { defaultValue: file.source })}
      </span>
      <span className="text-xs text-gray-500 w-16 text-right">{formatBytes(file.size)}</span>
      <span className="hidden md:inline text-xs text-gray-400 w-28 text-right truncate">{formatDate(file.created_at)}</span>
      <button type="button" onClick={(e) => { e.stopPropagation(); onPreview(file.file_id); }}
        className="p-1 text-gray-400 hover:text-indigo-600" title={t('files.preview')} aria-label={t('files.preview')}>
        <Eye className="w-4 h-4" />
      </button>
    </div>
  );
}

function TreeDir({ node, depth, expanded, toggle, openId, openFile, onPreview, t, formatDate }) {
  const open = expanded.has(node.path);
  return (
    <div>
      <button
        type="button"
        onClick={() => toggle(node.path)}
        aria-expanded={open}
        data-testid="tree-dir"
        className="w-full flex items-center gap-2 pr-3 py-1.5 text-sm text-left text-gray-700 hover:bg-gray-50"
        style={{ paddingLeft: `${12 + depth * 18}px` }}
      >
        {open ? <ChevronDown className="w-4 h-4 text-gray-400 shrink-0" /> : <ChevronRight className="w-4 h-4 text-gray-400 shrink-0" />}
        {open ? <FolderOpen className="w-4 h-4 text-amber-500 shrink-0" /> : <Folder className="w-4 h-4 text-amber-500 shrink-0" />}
        <span className="truncate font-medium">{node.name}</span>
        <span className="ml-auto text-xs text-gray-400 shrink-0">
          {t('files.tree.count', { count: node.count })} · {formatBytes(node.bytes)}
        </span>
      </button>
      {open && (
        <div>
          {node.children.map((c) => (
            <TreeDir key={c.path} node={c} depth={depth + 1} expanded={expanded} toggle={toggle}
              openId={openId} openFile={openFile} onPreview={onPreview} t={t} formatDate={formatDate} />
          ))}
          {node.files.map((f) => (
            <TreeFile key={f.file_id} file={f} depth={depth + 1} openId={openId} openFile={openFile}
              onPreview={onPreview} t={t} formatDate={formatDate} />
          ))}
        </div>
      )}
    </div>
  );
}

function FileTree({ files, searching, openId, openFile, onPreview, t, formatDate }) {
  const root = useMemo(() => buildTree(files), [files]);
  const [expanded, setExpanded] = useState(() => new Set());
  // A search already narrowed the list on the server: show every match.
  const shown = useMemo(() => (searching ? new Set(allDirPaths(root)) : expanded), [searching, root, expanded]);
  const toggle = (path) => setExpanded((prev) => {
    const next = new Set(searching ? allDirPaths(root) : prev);
    if (next.has(path)) next.delete(path); else next.add(path);
    return next;
  });
  const dirs = allDirPaths(root);
  return (
    <div className="py-1" data-testid="files-tree">
      {dirs.length > 0 && (
        <div className="flex justify-end gap-3 px-3 pb-1 text-[11px]">
          <button type="button" onClick={() => setExpanded(new Set(dirs))} className="text-gray-400 hover:text-indigo-600">
            {t('files.tree.expandAll')}
          </button>
          <button type="button" onClick={() => setExpanded(new Set())} className="text-gray-400 hover:text-indigo-600">
            {t('files.tree.collapseAll')}
          </button>
        </div>
      )}
      {root.children.map((c) => (
        <TreeDir key={c.path} node={c} depth={0} expanded={shown} toggle={toggle}
          openId={openId} openFile={openFile} onPreview={onPreview} t={t} formatDate={formatDate} />
      ))}
      {root.files.map((f) => (
        <TreeFile key={f.file_id} file={f} depth={0} openId={openId} openFile={openFile} onPreview={onPreview}
          t={t} formatDate={formatDate} />
      ))}
    </div>
  );
}

function isImage(rec) {
  return /^image\/(png|jpe?g|gif|webp|bmp)$/.test(rec?.mime_type || '');
}

const ext = (rec) => String(rec?.name || '').toLowerCase().split('.').pop();
const isMarkdown = (rec) => rec?.mime_type === 'text/markdown' || ['md', 'markdown'].includes(ext(rec));
const isPdf = (rec) => rec?.mime_type === 'application/pdf' || ext(rec) === 'pdf';
const isHtml = (rec) => rec?.mime_type === 'text/html' || ['html', 'htm'].includes(ext(rec));

const codeLanguage = (rec) => (isMarkdown(rec) || isHtml(rec) ? '' : codeLanguageFor(rec?.name));

const objectUrl = (blob, type) => {
  if (typeof URL.createObjectURL !== 'function') return '';
  return URL.createObjectURL(type && blob.type !== type ? new Blob([blob], { type }) : blob);
};

/** The file rendered the way a reader would see it, in a dialog over the page. */
function FilePreviewModal({ fileId, onClose }) {
  const { t } = useI18n();
  const [rec, setRec] = useState(null);
  const [text, setText] = useState(null);
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    let made = '';
    (async () => {
      try {
        const { data } = await getWorkspaceFileRecord(fileId);
        if (cancelled) return;
        setRec(data);
        if (isImage(data) || isPdf(data) || isHtml(data)) {
          const { data: blob } = await getWorkspaceFileBlob(fileId);
          if (cancelled) return;
          // HTML comes back as text/plain so it can never run as this origin;
          // here it renders inside a sandboxed frame from a blob of its own.
          made = objectUrl(blob, isPdf(data) ? 'application/pdf' : isHtml(data) ? 'text/html' : '');
          setUrl(made);
        } else {
          const { data: body } = await getWorkspaceFileText(fileId, 200000);
          if (!cancelled) setText(body);
        }
      } catch (e) {
        if (!cancelled) setError(errorDetail(e) || t('files.errors.preview'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [fileId, t]);

  let body;
  if (loading) {
    body = <PageLoader size="sm" />;
  } else if (error) {
    body = <p className="text-sm text-red-600">{error}</p>;
  } else if (isImage(rec) && url) {
    body = <img src={url} alt={rec?.name || ''} className="max-w-full max-h-[75vh] mx-auto rounded" />;
  } else if ((isPdf(rec) || isHtml(rec)) && url) {
    // A browser's PDF viewer does not load inside a sandboxed frame; HTML is
    // the one that must never run against this page, so only it is sandboxed.
    body = <iframe src={url} title={rec?.name || ''} sandbox={isHtml(rec) ? '' : undefined}
      className="w-full h-[75vh] rounded border border-gray-200 bg-white" />;
  } else if (text && text.text != null && isMarkdown(rec)) {
    body = <MarkdownRenderer content={text.text} className="text-sm text-gray-800" />;
  } else if (text && text.text != null && codeLanguage(rec)) {
    // Code the way the chat shows it: the language over the block, Copy on
    // the right, the grammar's colours in the body.
    body = <CodeBlock language={codeLanguage(rec)} code={text.text} bodyClassName="max-h-[75vh] overflow-auto" />;
  } else if (text && text.text != null) {
    body = (
      <>
        {text.kind === 'pdf' && <p className="text-[11px] text-gray-400 mb-1">{t('files.previewPdf')}</p>}
        <pre className="whitespace-pre-wrap break-words rounded bg-gray-50 p-3 text-xs text-gray-700">{text.text}</pre>
      </>
    );
  } else {
    body = <p className="text-sm text-gray-500">{t('files.previewBinary')}</p>;
  }

  return createPortal(
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4"
      onClick={(e) => { e.stopPropagation(); onClose(); }}
      role="dialog"
      aria-modal="true"
      aria-label={rec?.name || t('files.preview')}
      data-testid="file-preview-modal"
    >
      <div className="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[90vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-gray-100">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-gray-800 truncate">{rec?.name || fileId}</h3>
            {rec?.meta?.path && <p className="text-[11px] font-mono text-gray-400 truncate">{rec.meta.path}</p>}
          </div>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label={t('files.close')}>
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="px-5 py-4 overflow-y-auto">
          {body}
          {text?.truncated && <p className="text-[11px] text-gray-400 mt-2">{t('files.previewTruncated')}</p>}
        </div>
      </div>
    </div>,
    document.body,
  );
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

function FilePanel({ fileId, onClose, onDeleted, onPreview }) {
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
    <aside className="bg-white rounded-xl border border-gray-200 p-4 space-y-4 lg:min-h-0 lg:overflow-y-auto" data-testid="file-panel">
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
          <button type="button" onClick={() => onPreview(fileId)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-200 rounded-lg text-gray-700 hover:bg-gray-50">
            <Eye className="w-3.5 h-3.5" /> {t('files.preview')}
          </button>
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
          {rec.meta?.path && (<><dt className="text-gray-400">{t('files.path')}</dt><dd className="text-gray-700 font-mono break-all">{rec.meta.path}</dd></>)}
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
  const [view, setView] = useState(readView);
  const [previewId, setPreviewId] = useState('');
  const [files, setFiles] = useState([]);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [syncing, setSyncing] = useState(false);
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

  useEffect(() => {
    try { localStorage.setItem(VIEW_KEY, view); } catch { /* storage unavailable */ }
  }, [view]);

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

  const syncFolder = async () => {
    setSyncing(true);
    try {
      const { data } = await indexWorkspaceFiles(workspace);
      toast.success(t('files.synced', {
        added: data?.added || 0, updated: data?.updated || 0, removed: data?.removed || 0,
      }), data?.skipped?.length ? t('files.syncSkipped', { count: data.skipped.length }) : undefined);
      await load();
    } catch (e) {
      toast.error(t('files.errors.sync'), errorDetail(e));
    } finally {
      setSyncing(false);
    }
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
    <PageContainer fill className="gap-5">
      <PageHeader
        icon={FolderOpen}
        title={t('files.title')}
        description={t('files.pageDescription')}
        actions={<>
          <button type="button" onClick={load}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> {t('files.refresh')}
          </button>
          <button type="button" onClick={syncFolder} disabled={syncing} title={t('files.syncFolderHint')}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50">
            {syncing ? <Loader className="w-4 h-4 animate-spin" /> : <FolderSync className="w-4 h-4" />}
            {syncing ? t('files.syncing') : t('files.syncFolder')}
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
        <div className="relative flex-1 min-w-[12rem]">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('files.searchPlaceholder')}
            aria-label={t('files.searchPlaceholder')}
            className="w-full pl-9 pr-3 py-2 text-sm bg-white border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-100 focus:border-indigo-400" />
        </div>
        <select value={source} onChange={(e) => setSource(e.target.value)} aria-label={t('files.columns.source')}
          className="border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white">
          <option value="">{t('files.allSources')}</option>
          {SOURCES.map((s) => <option key={s} value={s}>{t(`files.sources.${s}`)}</option>)}
        </select>
        <div className="bg-white border border-gray-300 rounded-lg p-0.5 inline-flex items-center gap-0.5" role="group" aria-label={t('files.view.label')}>
          {[['tree', ListTree, t('files.view.tree')], ['list', List, t('files.view.list')]].map(([mode, Icon, label]) => (
            <button key={mode} type="button" onClick={() => setView(mode)} title={label} aria-label={label}
              aria-pressed={view === mode}
              className={`p-1.5 rounded-md ${view === mode ? 'bg-indigo-600 text-white' : 'text-gray-500 hover:bg-gray-50'}`}>
              <Icon className="w-4 h-4" />
            </button>
          ))}
        </div>
      </div>

      <div className={`grid gap-5 flex-1 lg:min-h-0 lg:grid-rows-[minmax(0,1fr)] ${openId ? 'lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]' : ''}`}>
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden lg:min-h-0 lg:overflow-y-auto">
          {loading && files.length === 0 ? (
            <PageLoader size="sm" />
          ) : files.length === 0 ? (
            <div className="text-center py-12">
              <FileText className="w-10 h-10 text-gray-300 mx-auto mb-3" />
              <p className="text-sm text-gray-500">{q || source ? t('files.noMatches') : t('files.empty')}</p>
            </div>
          ) : view === 'tree' ? (
            <FileTree files={files} searching={Boolean(q || source)} openId={openId} openFile={openFile}
              onPreview={setPreviewId} t={t} formatDate={formatDate} />
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
                    <tr key={f.file_id} onClick={() => openFile(f.file_id)} data-testid="list-row"
                      className={`cursor-pointer hover:bg-gray-50 ${openId === f.file_id ? 'bg-indigo-50/60' : ''}`}>
                      <td className="px-4 py-2.5">
                        <span className="flex items-center gap-2 text-gray-800">
                          <FileText className="w-4 h-4 text-gray-400 shrink-0" />
                          <span className="min-w-0">
                            <span className="block truncate max-w-[22rem]">{f.name}</span>
                            {f.meta?.path && f.meta.path !== f.name && (
                              <span className="block truncate max-w-[22rem] text-[11px] text-gray-400 font-mono">{f.meta.path}</span>
                            )}
                          </span>
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-xs text-gray-500">{t(`files.sources.${f.source}`, { defaultValue: f.source })}</td>
                      <td className="px-4 py-2.5 text-xs text-gray-500">{formatBytes(f.size)}</td>
                      <td className="px-4 py-2.5 text-xs text-gray-400">{formatDate(f.created_at)}</td>
                      <td className="px-4 py-2.5 text-right">
                        <button type="button" onClick={(e) => { e.stopPropagation(); setPreviewId(f.file_id); }}
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
            onPreview={setPreviewId}
          />
        )}
      </div>
      {previewId && <FilePreviewModal fileId={previewId} onClose={() => setPreviewId('')} />}
    </PageContainer>
  );
}
