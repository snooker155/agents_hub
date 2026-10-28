/**
 * Pick workspace files (docs/files.md) by id: the one dialog the chat
 * composer, the memory page, a task and an eval case open to reuse a file
 * that is already stored, instead of uploading it again. A new file can be
 * uploaded from here too; it lands in the workspace files and is selected.
 * The files show as a list or as a tree of their workspace folders, where a
 * whole folder can be picked at once; the choice is remembered per browser.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Check, ChevronDown, ChevronRight, FileText, Folder, FolderOpen, FolderTree, List, Loader, Minus, Search, Upload, X,
} from 'lucide-react';
import { useI18n } from '../../i18n';
import { formatBytes, listWorkspaceFiles, uploadWorkspaceFileObject } from '../../api/files';
import { errorDetail } from '../toast';
import PageLoader from '../PageLoader';
import { allDirPaths, buildFileTree, filesUnder } from './fileTree';

const VIEW_KEY = 'files.picker.view';
// A tree is only true to the folders when it holds them all: it asks for
// more files at once than the list does.
const LIMIT = { list: 200, tree: 1000 };

function readView() {
  try {
    return localStorage.getItem(VIEW_KEY) === 'tree' ? 'tree' : 'list';
  } catch {
    return 'list';
  }
}

/** A box that is ticked, half ticked (`partial`) or empty. */
function Tick({ on, partial = false }) {
  return (
    <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${
      on || partial ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-gray-300'
    }`}>
      {on ? <Check className="w-3 h-3" /> : partial ? <Minus className="w-3 h-3" /> : null}
    </span>
  );
}

/** One file: its box, name and size; in the tree its leaf name, indented. */
function FileRow({ rec, depth = null, isSelected, isExcluded, onToggle, t }) {
  const folder = depth === null ? String(rec.meta?.path || '').split('/').slice(0, -1).join('/') : '';
  return (
    <li>
      <button
        type="button"
        onClick={() => onToggle(rec)}
        disabled={isExcluded}
        data-testid="picker-file"
        className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left transition-colors ${
          isSelected ? 'bg-indigo-50' : 'hover:bg-gray-50'
        } disabled:opacity-50 disabled:cursor-not-allowed`}
        style={depth !== null ? { paddingLeft: `${12 + depth * 18 + 18}px` } : undefined}
      >
        <Tick on={isSelected} />
        <FileText className="w-4 h-4 text-gray-400 shrink-0" />
        <span className="flex-1 min-w-0">
          <span className="block text-sm text-gray-800 truncate">{depth !== null ? rec.leaf : rec.name}</span>
          <span className="block text-[11px] text-gray-400 truncate">
            {formatBytes(rec.size)} · {t(`files.sources.${rec.source}`, { defaultValue: rec.source })}
            {folder ? ` · ${folder}/` : ''}
            {isExcluded ? ` · ${t('files.picker.alreadyAdded')}` : ''}
          </span>
        </span>
      </button>
    </li>
  );
}

/** A folder: open or closed, and (picking many) a box for all its files. */
function DirRow({ node, depth, expanded, onExpand, multiple, isSelected, isExcluded, onToggleMany, onToggle, t }) {
  const open = expanded.has(node.path);
  const pickable = filesUnder(node).filter((r) => !isExcluded(r));
  const picked = pickable.filter(isSelected).length;
  return (
    <li>
      <div className="flex items-center gap-1 pr-3 rounded-lg hover:bg-gray-50" style={{ paddingLeft: `${12 + depth * 18}px` }}>
        <button
          type="button"
          onClick={() => onExpand(node.path)}
          aria-expanded={open}
          data-testid="picker-dir"
          className="flex-1 min-w-0 flex items-center gap-2 py-2 text-left text-sm text-gray-700"
        >
          {open ? <ChevronDown className="w-4 h-4 text-gray-400 shrink-0" /> : <ChevronRight className="w-4 h-4 text-gray-400 shrink-0" />}
          {open ? <FolderOpen className="w-4 h-4 text-amber-500 shrink-0" /> : <Folder className="w-4 h-4 text-amber-500 shrink-0" />}
          <span className="truncate font-medium">{node.name}</span>
          <span className="ml-auto pl-2 text-[11px] text-gray-400 shrink-0">
            {t('files.tree.count', { count: node.count })} · {formatBytes(node.bytes)}
          </span>
        </button>
        {multiple && pickable.length > 0 && (
          <button
            type="button"
            onClick={() => onToggleMany(pickable, picked < pickable.length)}
            className="p-1"
            title={t('files.picker.pickFolder')}
            aria-label={t('files.picker.pickFolderNamed', { name: node.name })}
          >
            <Tick on={picked === pickable.length} partial={picked > 0 && picked < pickable.length} />
          </button>
        )}
      </div>
      {open && (
        <ul>
          {node.children.map((c) => (
            <DirRow key={c.path} node={c} depth={depth + 1} expanded={expanded} onExpand={onExpand}
              multiple={multiple} isSelected={isSelected} isExcluded={isExcluded}
              onToggleMany={onToggleMany} onToggle={onToggle} t={t} />
          ))}
          {node.files.map((rec) => (
            <FileRow key={rec.file_id} rec={rec} depth={depth + 1} isSelected={isSelected(rec)}
              isExcluded={isExcluded(rec)} onToggle={onToggle} t={t} />
          ))}
        </ul>
      )}
    </li>
  );
}

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
  const [view, setView] = useState(readView);
  const [expanded, setExpanded] = useState(() => new Set());
  const inputRef = useRef(null);
  const excluded = new Set(excludeIds || []);

  const load = useCallback(async (query) => {
    if (!workspace) return;
    setLoading(true);
    try {
      const { data } = await listWorkspaceFiles(workspace, { q: query, limit: LIMIT[view] });
      setFiles(data?.files || []);
      setError('');
    } catch (e) {
      setError(errorDetail(e) || t('files.errors.load'));
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }, [workspace, view, t]);

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

  const isSelected = (rec) => selected.some((r) => r.file_id === rec.file_id);
  const isExcluded = (rec) => excluded.has(rec.file_id);
  // A folder's box: every file under it in, or every one out.
  const toggleMany = (recs, on) => setSelected((prev) => (on
    ? [...prev, ...recs.filter((r) => !prev.some((p) => p.file_id === r.file_id))]
    : prev.filter((p) => !recs.some((r) => r.file_id === p.file_id))));

  const tree = useMemo(() => (view === 'tree' ? buildFileTree(files) : null), [view, files]);
  // A search already narrowed the files on the server: every folder that
  // holds a match is open.
  const searching = !!q.trim();
  const shownDirs = useMemo(
    () => (tree && searching ? new Set(allDirPaths(tree)) : expanded),
    [tree, searching, expanded],
  );
  const expand = (path) => setExpanded((prev) => {
    const next = new Set(searching && tree ? allDirPaths(tree) : prev);
    if (next.has(path)) next.delete(path); else next.add(path);
    return next;
  });
  const switchView = (next) => {
    setView(next);
    try { localStorage.setItem(VIEW_KEY, next); } catch { /* storage unavailable */ }
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
              <div className="flex rounded-lg border border-gray-200 p-0.5" role="group" aria-label={t('files.view.label')}>
                {[
                  { value: 'list', icon: List, label: t('files.view.list') },
                  { value: 'tree', icon: FolderTree, label: t('files.view.tree') },
                ].map(({ value, icon: Icon, label }) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => switchView(value)}
                    aria-pressed={view === value}
                    title={label}
                    aria-label={label}
                    className={`p-1.5 rounded-md ${view === value ? 'bg-indigo-50 text-indigo-600' : 'text-gray-400 hover:text-gray-600'}`}
                  >
                    <Icon className="w-4 h-4" />
                  </button>
                ))}
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
              ) : tree ? (
                <ul data-testid="picker-tree">
                  {tree.children.map((c) => (
                    <DirRow key={c.path} node={c} depth={0} expanded={shownDirs} onExpand={expand}
                      multiple={multiple} isSelected={isSelected} isExcluded={isExcluded}
                      onToggleMany={toggleMany} onToggle={toggle} t={t} />
                  ))}
                  {tree.files.map((rec) => (
                    <FileRow key={rec.file_id} rec={rec} depth={0} isSelected={isSelected(rec)}
                      isExcluded={isExcluded(rec)} onToggle={toggle} t={t} />
                  ))}
                </ul>
              ) : (
                <ul>
                  {files.map((rec) => (
                    <FileRow key={rec.file_id} rec={rec} isSelected={isSelected(rec)}
                      isExcluded={isExcluded(rec)} onToggle={toggle} t={t} />
                  ))}
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
