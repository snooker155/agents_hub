/**
 * Artifacts: what the agents produced and work with, in one browser
 * (docs/files.md, docs/views.md).
 *
 * Two stores, one page. The workspace's files (uploaded, saved by an agent,
 * kept from a chat; referenced by id from the chat composer, memory pools,
 * tasks and eval cases) and the views an agent built as answers (charts,
 * graphs, scenes, decks, documents). The views sit in a virtual folder at the
 * root, next to the real folders of the workspace, so the two are browsed
 * the same way; a view's exports are files, and an html view is built from
 * files, so they belong side by side.
 *
 * Three ways to look at the same list, remembered per browser: cards (a
 * folder card per folder, a file card per file, the live card per view, with
 * a breadcrumb to walk into a folder; the folder is in the URL as
 * `?folder=`), a tree by path (folders closed until opened; a search opens
 * every folder with a match) and a flat table. A file row or card opens the
 * file's panel (`?file=<id>`, where citations and chat links land): preview,
 * where used, download, delete. A view row, or the details button of its
 * card, opens the view's panel (`?view=<id>`): the live card as a column,
 * what made it, Studio, its page, delete.
 *
 * The list itself is the drop target. Dragging files or folders over it
 * turns it into a drop field for the folder that is open (the root in the
 * tree and the list): a dropped folder keeps its structure, and everything
 * lands in the workspace folder at that path, where the agents' file tools
 * see it. The Views folder takes no files: views are built by agents, so
 * over it the field says so and the drop does nothing.
 *
 * A file an agent writes into the workspace folder with its filesystem tools
 * is registered as it is written; "Sync from folder" registers the rest of
 * the folder and drops the records of files that are gone. Memory is not
 * here on purpose: it is what the agents know about the user, not something
 * they made.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Ban, Boxes, ChevronDown, ChevronRight, Copy, Download, ExternalLink, Eye, FileCode, FileImage, FileText, Folder,
  FolderOpen, FolderSync, Images, Info, LayoutGrid, List, ListTree, Loader, RefreshCw, Search, Trash2, Upload, X,
} from 'lucide-react';
import { PageContainer, PageHeader } from '../components/PageLayout';
import FileViewer from '../components/files/FileViewer';
import { frameType, isImageFile, needsBytes, objectUrl } from '../lib/fileKind';
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
import { deleteView, getView, listViews } from '../api';
import ViewCard from '../views/ViewCard';
import { ownerLink } from '../views/owner';

const SOURCES = ['upload', 'chat', 'agent', 'memory', 'task', 'eval', 'api'];
// The virtual folder the views sit in; `?folder=` and the tree use the same key.
const VIEWS_PATH = '__views__';
// A panel lies over the right of the list instead of taking a grid column:
// the list keeps its width, so opening or closing one reflows nothing.
const PANEL_CLASS = 'absolute inset-y-0 right-0 z-20 w-full sm:w-[28rem] lg:w-[40%] bg-white border-l border-gray-200 rounded-r-xl shadow-2xl p-4';
// The right side of a tree row, as columns shared by folders, files and
// views so they line up: source or kind, size, date, actions. Narrow windows
// keep size and actions only.
const TREE_COLS = 'grid grid-cols-[4rem_4.5rem] md:grid-cols-[7rem_4rem_9.5rem_4.5rem] items-center gap-2 shrink-0 text-xs';
// Kinds that open in the Studio (built via the op protocol / live runtimes).
const STUDIO_KINDS = new Set(['graph', 'scene3d', 'simulation', 'math', 'process', 'chart', 'table', 'html', 'diagram', 'latex', 'slides', 'document']);

const VIEW_KEY = 'files.view';

function readView() {
  try {
    const v = localStorage.getItem(VIEW_KEY);
    return v === 'list' || v === 'tree' || v === 'cards' ? v : 'cards';
  } catch {
    return 'cards';
  }
}

/** The files as folders by their workspace path (an upload with no path sits
 * at the root), plus the views in a virtual folder of their own at the root. */
function buildTree(files, views = [], viewsName = 'Views') {
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
  finish(root);
  if (views.length) {
    root.children.unshift({
      name: viewsName, path: VIEWS_PATH, virtual: true, dirs: new Map(), children: [], files: [],
      views: [...views].sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''))),
      count: views.length, bytes: 0,
    });
  }
  return root;
}

/** The node at `path` in the tree (the root for ''), or null when it is gone. */
function findDir(root, path) {
  if (!path) return root;
  let node = root;
  for (const seg of path.split('/').filter(Boolean)) {
    node = (node.children || []).find((c) => c.name === seg || c.path === path);
    if (!node) return null;
    if (node.path === path) return node;
  }
  return node;
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
      <span className={TREE_COLS}>
        <span className="hidden md:block text-gray-500 truncate">
          {t(`files.sources.${file.source}`, { defaultValue: file.source })}
        </span>
        <span className="text-gray-500 text-right">{formatBytes(file.size)}</span>
        <span className="hidden md:block text-gray-400 text-right truncate">{formatDate(file.created_at)}</span>
        <span className="flex justify-end">
          <button type="button" onClick={(e) => { e.stopPropagation(); onPreview(file.file_id); }}
            className="p-1 text-gray-400 hover:text-indigo-600" title={t('files.preview')} aria-label={t('files.preview')}>
            <Eye className="w-4 h-4" />
          </button>
        </span>
      </span>
    </div>
  );
}

/** A view in the tree and the list: its title, kind and age, with Studio and open buttons. */
function ViewRowActions({ view, navigate, t }) {
  return (
    <>
      {STUDIO_KINDS.has(view.kind) && (
        <button type="button" onClick={(e) => { e.stopPropagation(); navigate(`/studio/${view.view_id}`); }}
          className="p-1 text-gray-400 hover:text-indigo-600" title={t('views.openInStudio')} aria-label={t('views.openInStudio')}>
          <Boxes className="w-4 h-4" />
        </button>
      )}
      <button type="button" onClick={(e) => { e.stopPropagation(); navigate(`/views/${view.view_id}`); }}
        className="p-1 text-gray-400 hover:text-indigo-600" title={t('artifacts.openPage')} aria-label={t('artifacts.openPage')}>
        <ExternalLink className="w-4 h-4" />
      </button>
    </>
  );
}

function TreeView({ view, depth, active, onOpenView, navigate, t, formatDate }) {
  return (
    <div
      className={`flex items-center gap-2 pr-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 ${active ? 'bg-indigo-50/60' : ''}`}
      style={{ paddingLeft: `${12 + depth * 18 + 18}px` }}
      data-testid="tree-view"
      onClick={() => onOpenView(view.view_id)}
    >
      <span className="flex-1 min-w-0 flex items-center gap-2 text-gray-800">
        <Images className="w-4 h-4 text-indigo-400 shrink-0" />
        <span className="truncate">{view.title || view.view_id}</span>
      </span>
      <span className={TREE_COLS}>
        <span className="hidden md:block text-gray-500 truncate">{t('artifacts.kinds.view')} · {view.kind}</span>
        <span className="text-gray-500 text-right">{view.size_bytes != null ? formatBytes(view.size_bytes) : '—'}</span>
        <span className="hidden md:block text-gray-400 text-right truncate">{formatDate(view.created_at)}</span>
        <span className="flex justify-end">
          <ViewRowActions view={view} navigate={navigate} t={t} />
        </span>
      </span>
    </div>
  );
}

function TreeDir({ node, depth, expanded, toggle, openId, openViewId, openFile, onPreview, onOpenView, navigate, t, formatDate }) {
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
        {node.virtual
          ? <Images className="w-4 h-4 text-indigo-500 shrink-0" />
          : open ? <FolderOpen className="w-4 h-4 text-amber-500 shrink-0" /> : <Folder className="w-4 h-4 text-amber-500 shrink-0" />}
        <span className="truncate font-medium">{node.name}</span>
        <span className={`ml-auto ${TREE_COLS}`}>
          <span className="col-span-1 md:col-span-3 text-gray-400 text-right truncate">
            {node.virtual
              ? t('artifacts.viewCount', { count: node.count })
              : <>{t('files.tree.count', { count: node.count })} · {formatBytes(node.bytes)}</>}
          </span>
          <span />
        </span>
      </button>
      {open && (
        <div>
          {node.children.map((c) => (
            <TreeDir key={c.path} node={c} depth={depth + 1} expanded={expanded} toggle={toggle}
              openId={openId} openViewId={openViewId} openFile={openFile} onPreview={onPreview} onOpenView={onOpenView}
              navigate={navigate} t={t} formatDate={formatDate} />
          ))}
          {(node.views || []).map((v) => (
            <TreeView key={v.view_id} view={v} depth={depth + 1} active={openViewId === v.view_id}
              onOpenView={onOpenView} navigate={navigate} t={t} formatDate={formatDate} />
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

function FileTree({ files, views, viewsName, searching, openId, openViewId, openFile, onPreview, onOpenView, navigate, t, formatDate }) {
  const root = useMemo(() => buildTree(files, views, viewsName), [files, views, viewsName]);
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
          openId={openId} openViewId={openViewId} openFile={openFile} onPreview={onPreview} onOpenView={onOpenView}
          navigate={navigate} t={t} formatDate={formatDate} />
      ))}
      {root.files.map((f) => (
        <TreeFile key={f.file_id} file={f} depth={0} openId={openId} openFile={openFile} onPreview={onPreview}
          t={t} formatDate={formatDate} />
      ))}
    </div>
  );
}

// A file record as components/files/FileViewer takes it.
const viewed = (rec) => ({ name: rec?.name, mimeType: rec?.mime_type });
const isImage = (rec) => isImageFile(viewed(rec));

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
        if (needsBytes(viewed(data))) {
          const { data: blob } = await getWorkspaceFileBlob(fileId);
          if (cancelled) return;
          // HTML comes back as text/plain so it can never run as this origin;
          // here it renders inside a sandboxed frame from a blob of its own.
          made = objectUrl(blob, frameType(viewed(data)));
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
  } else {
    body = (
      <FileViewer name={rec?.name} mimeType={rec?.mime_type} url={url}
        text={text?.text ?? null} kind={text?.kind} truncated={Boolean(text?.truncated)} />
    );
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
    // No resets here: the panel is keyed by the file, so a new file starts from
    // the initial state (loading, nothing shown).
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
    <aside className={`${PANEL_CLASS} space-y-4 overflow-y-auto`} data-testid="file-panel">
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

function FileIcon({ file, className }) {
  const mime = String(file?.mime_type || '');
  if (mime.startsWith('image/')) return <FileImage className={className} />;
  if (codeLanguageFor(file?.name)) return <FileCode className={className} />;
  return <FileText className={className} />;
}

function FolderCard({ node, onOpen, t }) {
  return (
    <button type="button" onClick={() => onOpen(node.path)} data-testid="folder-card"
      className="text-left rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4 hover:border-indigo-300 hover:shadow-sm transition-colors flex flex-col gap-3 min-h-[9rem]">
      <span className={`inline-flex w-10 h-10 items-center justify-center rounded-lg ${node.virtual ? 'bg-indigo-50 text-indigo-500' : 'bg-amber-50 text-amber-500'}`}>
        {node.virtual ? <Images className="w-5 h-5" /> : <Folder className="w-5 h-5" />}
      </span>
      <span className="min-w-0">
        <span className="block font-medium text-gray-800 dark:text-gray-100 truncate">{node.name}</span>
        <span className="block text-xs text-gray-500 mt-0.5">
          {node.virtual
            ? t('artifacts.viewCount', { count: node.count })
            : <>{t('files.tree.count', { count: node.count })} · {formatBytes(node.bytes)}</>}
        </span>
      </span>
    </button>
  );
}

function FileCard({ file, active, openFile, onPreview, t, formatDate }) {
  return (
    <div role="button" tabIndex={0} onClick={() => openFile(file.file_id)} data-testid="file-card"
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openFile(file.file_id); } }}
      className={`text-left rounded-xl border bg-white dark:bg-gray-900 p-4 cursor-pointer hover:border-indigo-300 hover:shadow-sm transition-colors flex flex-col gap-3 min-h-[9rem] ${
        active ? 'border-indigo-400 ring-2 ring-indigo-100' : 'border-gray-200 dark:border-gray-700'}`}>
      <span className="flex items-start justify-between gap-2">
        <span className="inline-flex w-10 h-10 items-center justify-center rounded-lg bg-gray-50 dark:bg-gray-800 text-gray-500">
          <FileIcon file={file} className="w-5 h-5" />
        </span>
        <button type="button" onClick={(e) => { e.stopPropagation(); onPreview(file.file_id); }}
          className="p-1 text-gray-400 hover:text-indigo-600" title={t('files.preview')} aria-label={t('files.preview')}>
          <Eye className="w-4 h-4" />
        </button>
      </span>
      <span className="min-w-0 mt-auto">
        <span className="block font-medium text-gray-800 dark:text-gray-100 truncate">{file.leaf || file.name}</span>
        <span className="block text-xs text-gray-500 mt-0.5 truncate">
          <span>{t(`files.sources.${file.source}`, { defaultValue: file.source })}</span>
          {' · '}<span>{formatBytes(file.size)}</span>
          {' · '}<span>{formatDate(file.created_at)}</span>
        </span>
      </span>
    </div>
  );
}

function ViewGrid({ views, onOpenView, onDeleteView, navigate, t }) {
  return (
    <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 360px), 1fr))' }}
      data-testid="views-grid">
      {views.map((row) => (
        <div key={row.view_id} className="min-w-0">
          <ViewCard
            compact
            viewRef={{ view_id: row.view_id, view_kind: row.kind, title: row.title, summary: row.summary }}
            actions={(
              <>
                <button type="button" onClick={() => onOpenView(row.view_id)} title={t('artifacts.details')}
                  data-testid="view-details"
                  className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 hover:text-indigo-600">
                  <Info className="w-4 h-4" />
                </button>
                {STUDIO_KINDS.has(row.kind) && (
                  <button type="button" onClick={() => navigate(`/studio/${row.view_id}`)} title={t('views.openInStudio')}
                    className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 hover:text-indigo-600">
                    <Boxes className="w-4 h-4" />
                  </button>
                )}
                <button type="button" onClick={() => onDeleteView(row.view_id)} title={t('views.deleteView')}
                  className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 hover:text-red-600">
                  <Trash2 className="w-4 h-4" />
                </button>
              </>
            )}
          />
        </div>
      ))}
    </div>
  );
}

/** Cards: the folders, files and views of one folder, or every match of a search. */
function CardsBrowser({ files, views, viewsName, folder, setFolder, searching, openId, openFile, onPreview, onOpenView, onDeleteView, navigate, t, formatDate }) {
  const root = useMemo(() => buildTree(files, views, viewsName), [files, views, viewsName]);
  const node = searching ? null : findDir(root, folder);
  const crumbs = useMemo(() => {
    if (!folder) return [];
    if (folder === VIEWS_PATH) return [{ path: VIEWS_PATH, name: viewsName }];
    const parts = folder.split('/').filter(Boolean);
    return parts.map((name, i) => ({ name, path: parts.slice(0, i + 1).join('/') }));
  }, [folder, viewsName]);

  const grid = 'grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(min(100%,13rem),1fr))]';
  let body;
  if (searching) {
    // Flat: a search already picked the matches; the folders are not the question.
    body = (
      <div className="space-y-5">
        {views.length > 0 && <ViewGrid views={views} onOpenView={onOpenView} onDeleteView={onDeleteView} navigate={navigate} t={t} />}
        {files.length > 0 && (
          <div className={grid}>
            {files.map((f) => (
              <FileCard key={f.file_id} file={f} active={openId === f.file_id} openFile={openFile}
                onPreview={onPreview} t={t} formatDate={formatDate} />
            ))}
          </div>
        )}
      </div>
    );
  } else if (!node) {
    body = <p className="text-sm text-gray-500 py-6 text-center">{t('artifacts.folderGone')}</p>;
  } else if (node.virtual) {
    body = <ViewGrid views={node.views} onOpenView={onOpenView} onDeleteView={onDeleteView} navigate={navigate} t={t} />;
  } else {
    body = (
      <div className={grid}>
        {node.children.map((c) => <FolderCard key={c.path} node={c} onOpen={setFolder} t={t} />)}
        {node.files.map((f) => (
          <FileCard key={f.file_id} file={{ ...f, leaf: f.leaf }} active={openId === f.file_id} openFile={openFile}
            onPreview={onPreview} t={t} formatDate={formatDate} />
        ))}
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4" data-testid="artifacts-cards">
      {!searching && (
        <nav className="flex items-center gap-1 text-sm text-gray-500 flex-wrap" aria-label={t('artifacts.breadcrumb')}>
          <button type="button" onClick={() => setFolder('')}
            className={`px-1.5 py-0.5 rounded hover:bg-gray-100 ${folder ? 'text-indigo-600' : 'font-medium text-gray-800'}`}>
            {t('artifacts.allItems')}
          </button>
          {crumbs.map((c, i) => (
            <span key={c.path} className="flex items-center gap-1">
              <ChevronRight className="w-3.5 h-3.5 text-gray-300" />
              <button type="button" onClick={() => setFolder(c.path)}
                className={`px-1.5 py-0.5 rounded hover:bg-gray-100 ${i === crumbs.length - 1 ? 'font-medium text-gray-800' : 'text-indigo-600'}`}>
                {c.name}
              </button>
            </span>
          ))}
        </nav>
      )}
      {body}
    </div>
  );
}

/** The view as a column: the live card, what made it, and where to go with it. */
function ViewPanel({ viewId, onClose, onDeleteView }) {
  const { t } = useI18n();
  const { formatDate } = useFormatters();
  const navigate = useNavigate();
  const [view, setView] = useState(null);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  // Keyed by viewId where it is mounted, so a new view is a fresh panel and
  // nothing has to be reset here.
  useEffect(() => {
    let cancelled = false;
    getView(viewId)
      .then(({ data }) => { if (!cancelled) setView(data); })
      .catch((e) => { if (!cancelled) setError(errorDetail(e) || t('views.loadFailed')); });
    return () => { cancelled = true; };
  }, [viewId, t]);

  const copyId = async () => {
    try {
      await navigator.clipboard.writeText(viewId);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  };

  const owner = ownerLink(view);
  return (
    // A flex column: the details keep their height and the card takes the
    // rest, down to the bottom of the page, scrolling inside for a document.
    <aside className={`${PANEL_CLASS} flex flex-col gap-4 min-h-0`} data-testid="view-panel">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-gray-800 break-words">{view?.title || viewId}</h3>
          <button type="button" onClick={copyId}
            className="mt-0.5 inline-flex items-center gap-1 text-[11px] font-mono text-gray-400 hover:text-gray-600"
            title={t('files.copyId')}>
            {viewId} <Copy className="w-3 h-3" /> {copied && <span className="font-sans">{t('files.copied')}</span>}
          </button>
        </div>
        <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label={t('files.close')}>
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => navigate(`/views/${viewId}`)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-200 rounded-lg text-gray-700 hover:bg-gray-50">
          <ExternalLink className="w-3.5 h-3.5" /> {t('artifacts.openPage')}
        </button>
        {view && STUDIO_KINDS.has(view.kind) && (
          <button type="button" onClick={() => navigate(`/studio/${viewId}`)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-200 rounded-lg text-gray-700 hover:bg-gray-50">
            <Boxes className="w-3.5 h-3.5" /> {t('views.openInStudio')}
          </button>
        )}
        <button type="button" onClick={() => onDeleteView(viewId)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-200 rounded-lg text-red-600 hover:bg-red-50">
          <Trash2 className="w-3.5 h-3.5" /> {t('views.deleteView')}
        </button>
      </div>

      {view && (
        <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-gray-400">{t('artifacts.kind')}</dt><dd className="text-gray-700">{view.kind}</dd>
          {view.size_bytes != null && (<><dt className="text-gray-400">{t('files.columns.size')}</dt><dd className="text-gray-700">{formatBytes(view.size_bytes)}</dd></>)}
          {view.summary && (<><dt className="text-gray-400">{t('artifacts.summary')}</dt><dd className="text-gray-700">{view.summary}</dd></>)}
          <dt className="text-gray-400">{t('files.columns.created')}</dt><dd className="text-gray-700">{formatDate(view.created_at)}</dd>
          {owner && (
            <>
              <dt className="text-gray-400">{t('artifacts.madeBy')}</dt>
              <dd className="text-gray-700 break-all">
                {owner.to ? <Link className="text-indigo-600 hover:underline" to={owner.to}>{owner.label}</Link> : owner.label}
              </dd>
            </>
          )}
          {view.task_id && (
            <><dt className="text-gray-400">{t('files.usedTasks')}</dt>
            <dd><Link className="text-indigo-600 hover:underline" to={`/tasks/${view.task_id}`}>{view.task_id}</Link></dd></>
          )}
        </dl>
      )}

      {error ? (
        <p className="text-sm text-red-600">{error}</p>
      ) : (
        // The same card the gallery shows, as a column that takes the rest of
        // the panel: a fill kind (chart, scene, html) scales to it, a document
        // scrolls inside it.
        <ViewCard fill className="!mt-0" viewRef={{ view_id: viewId, view_kind: view?.kind, title: view?.title, summary: view?.summary }} />
      )}
    </aside>
  );
}

/** Join a folder and a relative name into a workspace path ('' at the root). */
const joinPath = (folder, rel) => [folder, rel].filter(Boolean).join('/');

/**
 * Everything dropped, as files with the path they had inside a dropped
 * folder (`rel`, `docs/plan.md`; the bare name for a loose file). Folders are
 * walked through the FileSystem entries the drag carries; a browser without
 * them, or a drop of plain files, falls back to the flat file list.
 */
async function collectDropped(dataTransfer) {
  const items = Array.from(dataTransfer?.items || []);
  const entries = items.map((it) => (typeof it.webkitGetAsEntry === 'function' ? it.webkitGetAsEntry() : null));
  if (!entries.length || entries.some((e) => !e)) {
    return Array.from(dataTransfer?.files || []).map((file) => ({ file, rel: file.name }));
  }
  const out = [];
  const readEntries = (reader) => new Promise((resolve, reject) => reader.readEntries(resolve, reject));
  const fileOf = (entry) => new Promise((resolve, reject) => entry.file(resolve, reject));
  const walk = async (entry, prefix) => {
    if (entry.isFile) {
      out.push({ file: await fileOf(entry), rel: joinPath(prefix, entry.name) });
      return;
    }
    if (entry.isDirectory) {
      const reader = entry.createReader();
      // readEntries returns the directory in batches; an empty batch ends it.
      for (;;) {
        const batch = await readEntries(reader);
        if (!batch.length) break;
        for (const child of batch) await walk(child, joinPath(prefix, entry.name));
      }
    }
  };
  for (const entry of entries) await walk(entry, '');
  return out;
}

export default function Artifacts() {
  const { t } = useI18n();
  const { formatDate } = useFormatters();
  const toast = useToast();
  const { selectedWorkspace } = useWorkspace() || {};
  const workspace = selectedWorkspace || 'default';
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const openId = searchParams.get('file') || '';
  const openViewId = searchParams.get('view') || '';
  const folder = searchParams.get('folder') || '';
  const [q, setQ] = useState('');
  const [source, setSource] = useState('');
  const [view, setView] = useState(readView);
  const [previewId, setPreviewId] = useState('');
  const [files, setFiles] = useState([]);
  const [views, setViews] = useState([]);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  // How many drag targets inside the list the pointer is over: enter/leave
  // fire for every child, so only a count says when the drag has really left.
  const [dragDepth, setDragDepth] = useState(0);
  const inputRef = useRef(null);

  const load = useCallback(async () => {
    setLoading(true);
    // 'view' is a pseudo-source of this page: the files are fetched unfiltered
    // and hidden client side, so the server never sees a source it has no row for.
    const fileSource = source === 'view' ? '' : source;
    const [filesResult, viewsResult] = await Promise.allSettled([
      listWorkspaceFiles(workspace, { q, source: fileSource, limit: 500 }),
      listViews(selectedWorkspace ? { workspace: selectedWorkspace } : {}),
    ]);
    if (filesResult.status === 'fulfilled') {
      const data = filesResult.value?.data;
      setFiles(data?.files || []);
      setMeta({ usage: data?.usage_bytes || 0, limits: data?.limits || {} });
    } else {
      setFiles([]);
      toast.error(t('files.errors.load'), errorDetail(filesResult.reason));
    }
    if (viewsResult.status === 'fulfilled') {
      setViews(viewsResult.value?.data?.views || []);
    } else {
      setViews([]);
      toast.error(t('views.loadFailed'), errorDetail(viewsResult.reason));
    }
    setLoading(false);
  }, [workspace, selectedWorkspace, q, source, toast, t]);

  useEffect(() => {
    const id = setTimeout(load, 200);
    return () => clearTimeout(id);
  }, [load]);

  useEffect(() => {
    try { localStorage.setItem(VIEW_KEY, view); } catch { /* storage unavailable */ }
  }, [view]);

  // One panel at a time: a file or a view, each by its own query key, so a
  // link to either keeps working and opening one closes the other.
  const openFile = (fileId) => {
    const next = new URLSearchParams(searchParams);
    next.delete('view');
    if (fileId) next.set('file', fileId); else next.delete('file');
    setSearchParams(next, { replace: !fileId });
  };
  const openView = (viewId) => {
    const next = new URLSearchParams(searchParams);
    next.delete('file');
    if (viewId) next.set('view', viewId); else next.delete('view');
    setSearchParams(next, { replace: !viewId });
  };

  // The folder the cards show, kept in the URL so a folder can be linked to
  // (the Views folder is `?folder=__views__`, where the old /views lands).
  const setFolder = (path) => {
    const next = new URLSearchParams(searchParams);
    if (path) next.set('folder', path); else next.delete('folder');
    setSearchParams(next);
  };

  const onOpenView = (viewId) => openView(viewId);

  const onDeleteView = async (viewId) => {
    if (!window.confirm(t('views.confirmDelete'))) return;
    try {
      await deleteView(viewId);
      setViews((rows) => rows.filter((v) => v.view_id !== viewId));
      if (openViewId === viewId) openView('');
    } catch (e) {
      toast.error(t('views.deleteFailed'), errorDetail(e));
    }
  };

  // The views the search and the source filter leave: the files are filtered
  // on the server, the views here, by title, summary and kind.
  const shownViews = useMemo(() => {
    if (source && source !== 'view') return [];
    const needle = q.trim().toLowerCase();
    if (!needle) return views;
    return views.filter((v) => [v.title, v.summary, v.kind, v.view_id].some(
      (x) => String(x || '').toLowerCase().includes(needle)));
  }, [views, q, source]);
  const shownFiles = source === 'view' ? [] : files;
  const viewsName = t('artifacts.viewsFolder');

  // Where an upload lands: the folder open in the cards, the root otherwise.
  // The Views folder is not a place for files (views are built by agents).
  const targetFolder = view === 'cards' ? folder : '';
  const canDrop = targetFolder !== VIEWS_PATH;

  // `picked` is `[{file, rel}]` (collectDropped) or a plain FileList. A loose
  // file at the root goes to the file store as before; anything with a path
  // (a file in an open folder, a file of a dropped folder) is written into
  // the workspace folder at that path.
  const uploadAll = async (picked) => {
    const items = Array.from(picked || []).map((x) => (x && x.file ? x : { file: x, rel: x?.name }));
    if (!items.length || !canDrop) return;
    setUploading(true);
    let lastId = '';
    for (const { file, rel } of items) {
      const path = joinPath(targetFolder, rel);
      try {
        const { data } = await uploadWorkspaceFileObject(workspace, file, path !== file.name ? { path } : {});
        lastId = data.file_id;
        if (data.deduplicated) toast.success(t('files.deduplicated', { name: file.name, existing: data.name }));
        else toast.success(t('files.uploaded', { name: path }));
      } catch (e) {
        toast.error(t('files.errors.upload', { name: file.name }), errorDetail(e));
      }
    }
    setUploading(false);
    await load();
    if (items.length === 1 && lastId) openFile(lastId);
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

  const isFileDrag = (e) => Array.from(e.dataTransfer?.types || []).includes('Files');
  const onDragEnter = (e) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    setDragDepth((d) => d + 1);
  };
  const onDragOver = (e) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = canDrop ? 'copy' : 'none';
  };
  const onDragLeave = (e) => {
    if (!isFileDrag(e)) return;
    setDragDepth((d) => Math.max(0, d - 1));
  };
  const onDrop = async (e) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    setDragDepth(0);
    if (!canDrop) return;
    uploadAll(await collectDropped(e.dataTransfer));
  };
  const dragging = dragDepth > 0;
  const dropLabel = !canDrop
    ? t('artifacts.dropRefused')
    : targetFolder ? t('artifacts.dropInto', { folder: targetFolder }) : t('artifacts.dropRoot');

  const limits = meta?.limits || {};
  const usageLine = useMemo(() => {
    if (!meta || !limits.max_workspace_bytes) return '';
    return t('files.usage', { used: formatBytes(meta.usage), limit: formatBytes(limits.max_workspace_bytes) });
  }, [meta, limits.max_workspace_bytes, t]);

  return (
    <PageContainer fill className="gap-5">
      <PageHeader
        icon={Images}
        title={t('artifacts.title')}
        description={t('artifacts.pageDescription')}
        actions={<>
          <button type="button" onClick={() => navigate('/studio')}
            className="flex items-center gap-2 px-3 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700">
            <Boxes className="w-4 h-4" /> {t('views.studio')}
          </button>
          <button type="button" onClick={load}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> {t('files.refresh')}
          </button>
          <button type="button" onClick={syncFolder} disabled={syncing} title={t('files.syncFolderHint')}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50">
            {syncing ? <Loader className="w-4 h-4 animate-spin" /> : <FolderSync className="w-4 h-4" />}
            {syncing ? t('files.syncing') : t('files.syncFolder')}
          </button>
          <button type="button" onClick={() => inputRef.current?.click()} disabled={uploading || !canDrop}
            title={canDrop ? dropLabel : t('artifacts.dropRefused')}
            className="flex items-center gap-2 px-3 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
            {uploading ? <Loader className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
            {uploading ? t('files.uploading') : t('files.upload')}
          </button>
        </>}
      />
      <input ref={inputRef} type="file" multiple className="hidden" data-testid="files-upload-input"
        onChange={(e) => { uploadAll(e.target.files); e.target.value = ''; }} />

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[12rem]">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('files.searchPlaceholder')}
            aria-label={t('files.searchPlaceholder')}
            className="w-full pl-9 pr-3 py-2 text-sm bg-white border border-gray-300 rounded-lg focus:outline-none" />
        </div>
        <select value={source} onChange={(e) => setSource(e.target.value)} aria-label={t('files.columns.source')}
          className="border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white">
          <option value="">{t('files.allSources')}</option>
          {[...SOURCES, 'view'].map((s) => <option key={s} value={s}>{t(`files.sources.${s}`)}</option>)}
        </select>
        {(limits.max_file_bytes || usageLine) && (
          <span className="text-xs text-gray-400 hidden md:inline">
            {limits.max_file_bytes ? t('files.maxFile', { size: formatBytes(limits.max_file_bytes) }) : ''}
            {usageLine ? ` · ${usageLine}` : ''}
          </span>
        )}
        <div className="bg-white border border-gray-300 rounded-lg p-0.5 inline-flex items-center gap-0.5" role="group" aria-label={t('files.view.label')}>
          {[['cards', LayoutGrid, t('files.view.cards')], ['tree', ListTree, t('files.view.tree')], ['list', List, t('files.view.list')]].map(([mode, Icon, label]) => (
            <button key={mode} type="button" onClick={() => setView(mode)} title={label} aria-label={label}
              aria-pressed={view === mode}
              className={`p-1.5 rounded-md ${view === mode ? 'bg-indigo-600 text-white' : 'text-gray-500 hover:bg-gray-50'}`}>
              <Icon className="w-4 h-4" />
            </button>
          ))}
        </div>
      </div>

      {/* The list, with a panel laid over its right side when one is open. The
          wrapper, not the scrolling list, positions the panel, so the panel
          stays put while the list scrolls under it. */}
      <div className="relative flex-1 lg:min-h-0 flex flex-col">
        <div
          className={`relative flex-1 bg-white rounded-xl border overflow-hidden lg:min-h-0 lg:overflow-y-auto ${
            dragging ? (canDrop ? 'border-indigo-400' : 'border-red-300') : 'border-gray-200'}`}
          onDragEnter={onDragEnter}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          data-testid="files-dropzone"
        >
          {dragging && (
            <div
              className={`absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 text-sm pointer-events-none ${
                canDrop ? 'bg-indigo-50/90 text-indigo-700' : 'bg-red-50/90 text-red-600'}`}
              data-testid="drop-field"
            >
              {canDrop ? <Upload className="w-8 h-8" /> : <Ban className="w-8 h-8" />}
              <p className="font-medium">{dropLabel}</p>
              {canDrop && limits.max_file_bytes && (
                <p className="text-xs opacity-80">{t('files.maxFile', { size: formatBytes(limits.max_file_bytes) })}</p>
              )}
            </div>
          )}
          {loading && shownFiles.length === 0 && shownViews.length === 0 ? (
            <PageLoader size="sm" />
          ) : shownFiles.length === 0 && shownViews.length === 0 ? (
            <div className="text-center py-12">
              <FileText className="w-10 h-10 text-gray-300 mx-auto mb-3" />
              <p className="text-sm text-gray-500">{q || source ? t('files.noMatches') : t('artifacts.empty')}</p>
            </div>
          ) : view === 'cards' ? (
            <CardsBrowser files={shownFiles} views={shownViews} viewsName={viewsName} folder={folder} setFolder={setFolder}
              searching={Boolean(q || source)} openId={openId} openFile={openFile} onPreview={setPreviewId}
              onOpenView={onOpenView} onDeleteView={onDeleteView} navigate={navigate} t={t} formatDate={formatDate} />
          ) : view === 'tree' ? (
            <FileTree files={shownFiles} views={shownViews} viewsName={viewsName} searching={Boolean(q || source)}
              openId={openId} openViewId={openViewId} openFile={openFile} onPreview={setPreviewId} onOpenView={onOpenView}
              navigate={navigate} t={t} formatDate={formatDate} />
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
                  {shownViews.map((v) => (
                    <tr key={v.view_id} onClick={() => onOpenView(v.view_id)} data-testid="list-view-row"
                      className={`cursor-pointer hover:bg-gray-50 ${openViewId === v.view_id ? 'bg-indigo-50/60' : ''}`}>
                      <td className="px-4 py-2.5">
                        <span className="flex items-center gap-2 text-gray-800">
                          <Images className="w-4 h-4 text-indigo-400 shrink-0" />
                          <span className="min-w-0">
                            <span className="block truncate max-w-[22rem]">{v.title || v.view_id}</span>
                            {v.summary && <span className="block truncate max-w-[22rem] text-[11px] text-gray-400">{v.summary}</span>}
                          </span>
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-xs text-gray-500">{t('artifacts.kinds.view')} · {v.kind}</td>
                      <td className="px-4 py-2.5 text-xs text-gray-500">{v.size_bytes != null ? formatBytes(v.size_bytes) : '—'}</td>
                      <td className="px-4 py-2.5 text-xs text-gray-400">{formatDate(v.created_at)}</td>
                      <td className="px-4 py-2.5 text-right whitespace-nowrap">
                        <ViewRowActions view={v} navigate={navigate} t={t} />
                      </td>
                    </tr>
                  ))}
                  {shownFiles.map((f) => (
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
        {!openId && openViewId && (
          <ViewPanel key={openViewId} viewId={openViewId} onClose={() => openView('')} onDeleteView={onDeleteView} />
        )}
      </div>
      {previewId && <FilePreviewModal fileId={previewId} onClose={() => setPreviewId('')} />}
    </PageContainer>
  );
}

/** `/views`, `/files` and `/artifacts/files` moved here; the query comes along
 * (`?file=<id>` keeps opening that file), and the old views gallery lands in
 * the Views folder. */
export function ArtifactsRedirect({ folder = '' }) {
  const { search, hash } = useLocation();
  const params = new URLSearchParams(search);
  if (folder) params.set('folder', folder);
  const query = params.toString();
  return <Navigate to={{ pathname: '/artifacts', search: query ? `?${query}` : '', hash }} replace />;
}
