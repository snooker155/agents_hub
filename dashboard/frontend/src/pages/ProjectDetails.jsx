import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useParams, Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  getProject, updateProject, deleteProject, getProjectTasks,
  cloneProjectRepo, getProjectGitStatus, pullProjectRepo,
  getProjectFiles, getProjectFileContent, getProjectFileBlob, getProjectFileId,
  syncProjectIssues, publishProjectBranch,
} from '../api';
import ImportRepoModal from '../components/ImportRepoModal';
import DeployPanel from '../components/projects/DeployPanel';
import TrackerCard from '../components/projects/TrackerCard';
import ProjectGraph from '../components/flow/ProjectGraph';
import PlannerChat from '../components/flow/PlannerChat';
import TaskBoard from '../components/TaskBoard';
import FileViewer from '../components/files/FileViewer';
import { frameType, hasSourceView, isHtmlFile, isPdfFile, needsBytes, objectUrl } from '../lib/fileKind';
import { saveBlobAs } from '../api/files';
import { useWorkspace } from '../components/workspace';
import {
  FolderGit2, Globe, Server, GitBranch, Github, Gitlab, ChevronLeft,
  RefreshCw, Play, Download, ExternalLink, CheckSquare, AlertCircle,
  Edit3, Save, X, Send, Code2, BookOpen, FileText, Tag, Clock,
  ArrowUpDown, Terminal, Folder, FolderOpen, ChevronRight, ChevronDown,
  Search, ChevronUp, Zap, Link2, PanelRightOpen, Trash2, Eye,
} from 'lucide-react';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import { useToast, errorDetail } from '../components/toast';
import PageLoader from '../components/PageLoader';
const TABS = ['Overview', 'Tasks', 'Architecture', 'Files'];

// ── File tree helpers ──────────────────────────────────────────
const buildFileTree = (paths) => {
  const root = { type: 'dir', children: {} };
  (paths || []).forEach((rawPath) => {
    const parts = String(rawPath || '').trim().split('/').filter(Boolean);
    let node = root;
    parts.forEach((part, idx) => {
      if (!node.children[part]) {
        node.children[part] = idx === parts.length - 1
          ? { type: 'file', name: part, path: parts.join('/') }
          : { type: 'dir', name: part, children: {} };
      }
      node = node.children[part];
    });
  });
  const toArray = (node, parentPath = '') =>
    Object.keys(node.children || {})
      .sort((a, b) => {
        const aT = node.children[a].type; const bT = node.children[b].type;
        if (aT !== bT) return aT === 'dir' ? -1 : 1;
        return a.localeCompare(b);
      })
      .map((name) => {
        const child = node.children[name];
        const fullPath = parentPath ? `${parentPath}/${name}` : name;
        return child.type === 'dir'
          ? { type: 'dir', name, path: fullPath, children: toArray(child, fullPath) }
          : { type: 'file', name, path: child.path || fullPath };
      });
  return toArray(root);
};

const STATUS_COLORS = {
  todo: 'bg-gray-100 text-gray-600',
  ready: 'bg-blue-100 text-blue-700',
  in_progress: 'bg-yellow-100 text-yellow-700',
  blocked: 'bg-red-100 text-red-700',
  done: 'bg-green-100 text-green-700',
  stopped: 'bg-gray-100 text-gray-500',
};

const METHOD_COLORS = {
  GET: 'bg-blue-100 text-blue-700',
  POST: 'bg-green-100 text-green-700',
  PUT: 'bg-yellow-100 text-yellow-700',
  PATCH: 'bg-orange-100 text-orange-700',
  DELETE: 'bg-red-100 text-red-700',
};

export default function ProjectDetails() {
  const { t } = useI18n();
  const toast = useToast();
  const { id } = useParams();
  const navigate = useNavigate();
  const { liveUpdates, selectedWorkspace } = useWorkspace();
  // The project's workspace is redundant when a specific workspace is selected in
  // the header — only surface it in the default (all-workspaces) view.
  const onDefaultWorkspace = !selectedWorkspace || selectedWorkspace === 'default';
  const [project, setProject] = useState(null);
  const [loading, setLoading] = useState(true);
  // ?file=<file id> opens that file on the Files tab; the address names a
  // file by its registry id, a path from an old link still opens.
  const [searchParams, setSearchParams] = useSearchParams();
  const linkedFile = searchParams.get('file') || '';
  const [activeTab, setActiveTab] = useState(linkedFile ? 'Files' : 'Overview');
  const [taskToolbar, setTaskToolbar] = useState(null);
  const [plannerToolbar, setPlannerToolbar] = useState(null);
  const [showPlanner, setShowPlanner] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState({});
  const [saving, setSaving] = useState(false);

  // Tasks tab
  const [tasks, setTasks] = useState([]);
  const [tasksLoading, setTasksLoading] = useState(false);

  // Files tab
  const [files, setFiles] = useState([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [filesError, setFilesError] = useState('');
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [selectedFilePath, setSelectedFilePath] = useState('');
  // { text, kind, mimeType, truncated } from file-content, and for an image,
  // a PDF or an HTML page an object URL of its bytes (components/files/FileViewer).
  const [selectedFile, setSelectedFile] = useState(null);
  const [selectedFileUrl, setSelectedFileUrl] = useState('');
  const [selectedFileSize, setSelectedFileSize] = useState(0);
  const [fileSourceView, setFileSourceView] = useState(false);
  const fileUrlRef = useRef('');
  const fileRequest = useRef(0);
  // Project-relative path to registry id (GET /projects/{id}/files).
  const fileIdsRef = useRef({});
  const [fileContentLoading, setFileContentLoading] = useState(false);
  const [fileContentError, setFileContentError] = useState('');

  // Repo tab
  const [gitStatus, setGitStatus] = useState(null);
  const [gitLoading, setGitLoading] = useState(false);
  const [cloning, setCloning] = useState(false);
  const [pulling, setPulling] = useState(false);
  const [gitMsg, setGitMsg] = useState('');
  // Publish: commit, push a branch and open a PR/MR. The same refusals the
  // git_publish tool obeys apply, so the modal only collects the wording.
  const [showPublish, setShowPublish] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState('');
  const [prUrl, setPrUrl] = useState('');
  // Set instead of prUrl when the repo has a remote but no GitHub/GitLab
  // provider configured: publish still commits and pushes, it just has no
  // provider API to ask for a pull/merge request afterwards.
  const [pushResult, setPushResult] = useState(null);
  const [publishForm, setPublishForm] = useState({ title: '', body: '', branch: '', base: '', draft: true });
  const [showConnect, setShowConnect] = useState(false);
  const [syncingIssues, setSyncingIssues] = useState(false);


  const fetchProject = useCallback(async () => {
    try {
      const resp = await getProject(id);
      setProject(resp.data);
      setEditForm({
        name: resp.data.name,
        description: resp.data.description || '',
        status: resp.data.status,
        type: resp.data.type,
        tags: (resp.data.tags || []).join(', '),
        repo_url: resp.data.repo?.url || '',
        repo_branch: resp.data.repo?.branch || 'main',
      });
    } catch {
      navigate('/projects');
    } finally {
      setLoading(false);
    }
  }, [id, navigate]);

  useEffect(() => { fetchProject(); }, [fetchProject, id]);

  const replaceFileUrl = useCallback((next) => {
    if (fileUrlRef.current) URL.revokeObjectURL?.(fileUrlRef.current);
    fileUrlRef.current = next;
    setSelectedFileUrl(next);
  }, []);
  useEffect(() => () => { if (fileUrlRef.current) URL.revokeObjectURL?.(fileUrlRef.current); }, []);

  // The registry id of a project file, asked for once when the list had none.
  const fileIdOf = useCallback(async (path) => {
    if (fileIdsRef.current[path]) return fileIdsRef.current[path];
    try {
      const { data } = await getProjectFileId(id, path);
      if (!data?.file_id) return null;
      fileIdsRef.current = { ...fileIdsRef.current, [path]: data.file_id };
      return data.file_id;
    } catch {
      return null;
    }
  }, [id]);
  const fileRef = (path) => (fileIdsRef.current[path] ? { fileId: fileIdsRef.current[path] } : path);

  // Opens a file; ``link`` puts its id in the address, for a file the user
  // picked or a link opened, not for the first one shown.
  const loadFileContent = useCallback(async (path, { link = false } = {}) => {
    if (!path) return;
    // A click on another file while this one loads wins: older answers are dropped.
    const request = ++fileRequest.current;
    setSelectedFilePath(path);
    setFileContentLoading(true);
    setFileContentError('');
    setSelectedFile(null);
    setSelectedFileSize(0);
    setFileSourceView(false);
    replaceFileUrl('');
    const fileId = await fileIdOf(path);
    if (request !== fileRequest.current) return;
    if (link) {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (fileId) next.set('file', fileId);
        else next.delete('file');
        return next;
      }, { replace: true });
    }
    const ref = fileId ? { fileId } : path;
    try {
      const { data } = await getProjectFileContent(id, ref);
      if (request !== fileRequest.current) return;
      const viewed = { name: path, mimeType: data.mime_type };
      setSelectedFile({
        text: data.content ?? null, kind: data.kind || 'text',
        mimeType: data.mime_type || '', truncated: Boolean(data.truncated),
      });
      setSelectedFileSize(data.size || 0);
      if (needsBytes(viewed)) {
        const { data: blob } = await getProjectFileBlob(id, ref);
        if (request !== fileRequest.current) return;
        replaceFileUrl(objectUrl(blob, frameType(viewed)));
      }
    } catch (e) {
      if (request !== fileRequest.current) return;
      setFileContentError(e.response?.data?.detail || t('projectDetails.errors.loadFile'));
    } finally {
      if (request === fileRequest.current) setFileContentLoading(false);
    }
  }, [id, t, replaceFileUrl, fileIdOf, setSearchParams]);

  const downloadSelectedFile = async () => {
    try {
      const { data } = await getProjectFileBlob(id, fileRef(selectedFilePath));
      saveBlobAs(data, selectedFilePath.split('/').pop());
    } catch (e) {
      toast.error(t('projectDetails.errors.downloadFile'), errorDetail(e));
    }
  };

  const loadFiles = useCallback(async () => {
    setFilesLoading(true);
    setFilesError('');
    try {
      const resp = await getProjectFiles(id);
      const list = resp.data.files || [];
      const ids = resp.data.ids || {};
      fileIdsRef.current = ids;
      setFiles(list);
      if (list.length) {
        const byId = Object.keys(ids).find((p) => ids[p] === linkedFile);
        const linked = byId || (list.includes(linkedFile) ? linkedFile : '');
        const first = linked || list[0];
        const top = new Set();
        list.forEach((p) => { if (p.includes('/')) top.add(p.split('/')[0]); });
        first.split('/').slice(0, -1).forEach((_, i, parts) => top.add(parts.slice(0, i + 1).join('/')));
        setExpandedFolders(top);
        // A link with a path is rewritten to the file's id.
        loadFileContent(first, { link: Boolean(linked) && !byId });
      }
    } catch (e) {
      setFilesError(e.response?.data?.detail || e.message || t('projectDetails.errors.loadFiles'));
    } finally {
      setFilesLoading(false);
    }
  }, [id, loadFileContent, t, linkedFile]);

  useEffect(() => {
    if (activeTab === 'Tasks' || activeTab === 'Overview') loadTasks();
    if (activeTab === 'Files') loadFiles();
    if (activeTab === 'Repository') loadGitStatus();
  }, [activeTab]); // eslint-disable-line react-hooks/exhaustive-deps -- loaders and backendBase are declared below; naming them here would hit the TDZ

  const loadTasks = async () => {
    setTasksLoading(true);
    try {
      const resp = await getProjectTasks(id);
      setTasks(resp.data);
    } catch (e) {
      toast.error(t('projectDetails.errors.loadTasks'), errorDetail(e));
    }
    finally { setTasksLoading(false); }
  };

  const loadGitStatus = async () => {
    setGitLoading(true);
    setGitMsg('');
    try {
      const resp = await getProjectGitStatus(id);
      setGitStatus(resp.data);
    } catch (e) {
      setGitStatus(null);
      setGitMsg(e.response?.data?.detail || t('projectDetails.errors.gitStatus'));
    } finally { setGitLoading(false); }
  };

  const handleClone = async () => {
    setCloning(true);
    setGitMsg('');
    try {
      const resp = await cloneProjectRepo(id);
      setGitMsg(`Cloned successfully to ${resp.data.path}`);
      loadGitStatus();
    } catch (e) {
      setGitMsg(e.response?.data?.detail || t('projectDetails.errors.clone'));
    } finally { setCloning(false); }
  };

  const handlePull = async () => {
    setPulling(true);
    setGitMsg('');
    try {
      const resp = await pullProjectRepo(id);
      setGitMsg(resp.data.output || t('projectDetails.pulled'));
      loadGitStatus();
    } catch (e) {
      setGitMsg(e.response?.data?.detail || t('projectDetails.errors.pull'));
    } finally { setPulling(false); }
  };

  const handleSyncIssues = async () => {
    setSyncingIssues(true);
    setGitMsg('');
    try {
      const resp = await syncProjectIssues(id);
      const r = resp.data || {};
      setGitMsg(`Issues synced: ${r.imported || 0} new, ${r.updated || 0} updated (of ${r.total || 0})`);
      loadTasks();
    } catch (e) {
      setGitMsg(e.response?.data?.detail || t('projectDetails.errors.issueSync'));
    } finally { setSyncingIssues(false); }
  };

  const handleConnected = (result) => {
    setShowConnect(false);
    const issues = result?.issues;
    let msg = result?.cloned ? t('projectDetails.repoConnectedCloned') : t('projectDetails.repoConnected');
    if (issues?.error) msg += ` — issue import failed: ${issues.error}`;
    else if (issues) msg += ` — ${issues.imported} issue${issues.imported === 1 ? '' : 's'} imported as tasks`;
    setGitMsg(msg);
    fetchProject();
    loadGitStatus();
  };

  const handlePublish = async (e) => {
    e.preventDefault();
    setPublishing(true);
    setPublishError('');
    setPrUrl('');
    setPushResult(null);
    try {
      const { data } = await publishProjectBranch(id, {
        title: publishForm.title,
        body: publishForm.body,
        branch: publishForm.branch || null,
        base: publishForm.base || null,
        draft: publishForm.draft,
      });
      if (data.pr_url) {
        setPrUrl(data.pr_url);
      } else if (data.pushed) {
        // Either open_pr was false, or there was no provider to ask for a
        // pull/merge request at all: either way, show what actually landed.
        setPushResult({ branch: data.branch, remote: data.remote });
        setGitMsg(data.message || t('projectDetails.publish.pushed'));
      }
      loadGitStatus();
    } catch (e2) {
      setPublishError(e2.response?.data?.detail || t('projectDetails.errors.publish'));
    } finally {
      setPublishing(false);
    }
  };

  const isConnectedRepo = ['github', 'gitlab', 'bitbucket', 'gitea'].includes(project?.repo?.type) && project?.repo?.remote_id;
  // A repo attached with a remote but no GitHub/GitLab provider still has
  // somewhere to push; it just cannot open a pull/merge request, so the
  // publish modal offers a reduced, push-only path instead of hiding the
  // button entirely.
  const hasRepoRemote = !!project?.repo?.url;
  const canPublishBranch = isConnectedRepo || hasRepoRemote;

  const [deleting, setDeleting] = useState(false);
  const handleDelete = async () => {
    if (!window.confirm(t('projectDetails.deleteConfirm', { name: project?.name || '' }))) return;
    setDeleting(true);
    try {
      await deleteProject(id);
      toast.success(t('projectDetails.deleted'));
      navigate('/projects');
    } catch (e) {
      toast.error(t('projectDetails.deleteFailed'), errorDetail(e));
    } finally {
      setDeleting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateProject(id, {
        name: editForm.name,
        description: editForm.description,
        status: editForm.status,
        type: editForm.type,
        tags: editForm.tags ? editForm.tags.split(',').map(t => t.trim()).filter(Boolean) : [],
        repo: {
          url: editForm.repo_url || null,
          branch: editForm.repo_branch || 'main',
        },
      });
      setEditing(false);
      fetchProject();
    } catch (e) {
      console.error(e);
    } finally { setSaving(false); }
  };


  const fileTree = useMemo(() => buildFileTree(files), [files]);
  const viewedFile = { name: selectedFilePath, mimeType: selectedFile?.mimeType };
  // A PDF or an HTML page fills the panel edge to edge in its frame.
  const framed = Boolean(selectedFileUrl) && !fileSourceView && (isPdfFile(viewedFile) || isHtmlFile(viewedFile));

  const toggleFolder = (path) => setExpandedFolders((prev) => {
    const next = new Set(prev);
    next.has(path) ? next.delete(path) : next.add(path);
    return next;
  });

  const renderFileNodes = (nodes, depth = 0) => nodes.map((node) => {
    if (node.type === 'dir') {
      const open = expandedFolders.has(node.path);
      return (
        <div key={node.path}>
          <button
            type="button"
            onClick={() => toggleFolder(node.path)}
            className="flex items-center gap-1 w-full text-left px-2 py-1 hover:bg-gray-50 rounded text-sm text-gray-700"
            style={{ paddingLeft: `${depth * 12 + 8}px` }}
          >
            {open ? <ChevronDown className="w-3 h-3 text-gray-400 shrink-0" /> : <ChevronRight className="w-3 h-3 text-gray-400 shrink-0" />}
            {open ? <FolderOpen className="w-4 h-4 text-indigo-400 shrink-0" /> : <Folder className="w-4 h-4 text-indigo-300 shrink-0" />}
            <span className="truncate">{node.name}</span>
          </button>
          {open && renderFileNodes(node.children, depth + 1)}
        </div>
      );
    }
    return (
      <button
        key={node.path}
        type="button"
        onClick={() => loadFileContent(node.path, { link: true })}
        className={`flex items-center gap-1 w-full text-left px-2 py-1 rounded text-sm truncate ${selectedFilePath === node.path ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-600 hover:bg-gray-50'}`}
        style={{ paddingLeft: `${depth * 12 + 20}px` }}
      >
        <FileText className="w-3.5 h-3.5 shrink-0 text-gray-400" />
        <span className="truncate">{node.name}</span>
      </button>
    );
  });

  if (loading) {
    return <PageLoader size="lg" label={t('projectDetails.loading')} />;
  }
  if (!project) return null;

  return (
    <PageContainer fill={activeTab === 'Tasks' || activeTab === 'Files'}>
      <PageHeader
        icon={FolderGit2}
        title={project.name}
        description={!editing ? project.description : undefined}
        backTo="/projects"
        backLabel={t('projectDetails.projects')}
        badges={!editing && (
              <>
                <span className="shrink-0 text-xs px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 font-medium">{project.type}</span>
                <span className={`shrink-0 text-xs px-2 py-0.5 rounded-full font-medium ${
                  project.status === 'active' ? 'bg-green-100 text-green-700' :
                  project.status === 'completed' ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'
                }`}>{project.status}</span>
                {onDefaultWorkspace && (
                  <span className="shrink-0 text-xs text-gray-400">{t('projectDetails.workspace')} <strong>{project.workspace}</strong></span>
                )}
                {project.repo?.type && project.repo.type !== 'none' && (
                  <span className="flex shrink-0 items-center gap-1 text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                    <GitBranch className="w-3 h-3" /> {project.repo.type}
                    {project.repo.url && <a href={project.repo.url} target="_blank" rel="noreferrer" className="ml-1 underline">{t('projectDetails.repo')}</a>}
                  </span>
                )}
                {project.tags?.map(tag => (
                  <span key={tag} className="shrink-0 text-xs text-gray-500 bg-gray-50 border border-gray-200 px-1.5 py-0.5 rounded">
                    {tag}
                  </span>
                ))}
              </>
            )}
        actions={!editing && (
          <button onClick={() => setEditing(true)}
            className="flex items-center gap-1.5 px-3 py-2 border border-gray-200 rounded-lg text-sm text-gray-600 hover:bg-gray-50">
            <Edit3 className="w-4 h-4" /> {t('projectDetails.edit')}
          </button>
        )}
      />

      <div className={editing ? 'mb-6' : ''}>
        {editing && (
          <div className="space-y-4 rounded-xl border border-gray-200 bg-white p-6">
            <div className="grid grid-cols-2 gap-4">
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-500 mb-1">{t('projectDetails.name')}</label>
                <input
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                  value={editForm.name}
                  onChange={e => setEditForm(f => ({ ...f, name: e.target.value }))}
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-500 mb-1">{t('projectDetails.description')}</label>
                <textarea
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-none"
                  rows={2}
                  value={editForm.description}
                  onChange={e => setEditForm(f => ({ ...f, description: e.target.value }))}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">{t('projectDetails.status')}</label>
                <select className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                  value={editForm.status} onChange={e => setEditForm(f => ({ ...f, status: e.target.value }))}>
                  <option value="active">{t('projectDetails.active')}</option>
                  <option value="archived">{t('projectDetails.archived')}</option>
                  <option value="completed">{t('projectDetails.completed')}</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">{t('projectDetails.type')}</label>
                <select className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                  value={editForm.type} onChange={e => setEditForm(f => ({ ...f, type: e.target.value }))}>
                  <option value="general">{t('projectDetails.general')}</option>
                  <option value="code">{t('projectDetails.code')}</option>
                  <option value="research">{t('projectDetails.research')}</option>
                  <option value="documentation">{t('projectDetails.documentation')}</option>
                </select>
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-500 mb-1">{t('projectDetails.tagsCommaSeparated')}</label>
                <input className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                  value={editForm.tags} onChange={e => setEditForm(f => ({ ...f, tags: e.target.value }))} />
              </div>
            </div>
            {/* Repo */}
            <div className="border border-gray-100 rounded-xl p-4 space-y-2">
              <h4 className="text-xs font-semibold text-gray-600 flex items-center gap-1"><GitBranch className="w-3.5 h-3.5" /> {t('projectDetails.repository')}</h4>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">URL</label>
                  <input className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                    value={editForm.repo_url} onChange={e => setEditForm(f => ({ ...f, repo_url: e.target.value }))} placeholder="https://github.com/org/repo.git" />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">{t('projectDetails.branch')}</label>
                  <input className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                    value={editForm.repo_branch} onChange={e => setEditForm(f => ({ ...f, repo_branch: e.target.value }))} />
                </div>
              </div>
            </div>
            <div className="flex items-center justify-between gap-2">
              <div className="flex gap-2">
                <button onClick={handleSave} disabled={saving}
                  className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700 disabled:opacity-50 font-medium">
                  <Save className="w-3.5 h-3.5" /> {saving ? 'Saving…' : 'Save'}
                </button>
                <button onClick={() => setEditing(false)}
                  className="px-4 py-2 text-sm border border-gray-200 rounded-lg hover:bg-gray-50">
                  {t('projectDetails.cancel')}
                </button>
              </div>
              {/* Deleting a project lives here, in its settings, not on the card list. */}
              <button onClick={handleDelete} disabled={deleting}
                className="flex items-center gap-1.5 px-3 py-2 text-sm text-red-600 border border-red-200 rounded-lg hover:bg-red-50 disabled:opacity-50">
                <Trash2 className="w-3.5 h-3.5" /> {t('projectDetails.deleteProject')}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200 shrink-0 flex items-end justify-between gap-3">
        <nav className="flex flex-wrap gap-2 -mb-px">
          {[
            ...TABS,
            ...(project.type === 'code' || (project.repo?.type && project.repo.type !== 'none') ? ['Repository'] : []),
            'Deploy',
          ].map(tab => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={`inline-flex items-center px-4 py-2 first:pl-0 text-sm font-semibold border-b-2 transition-colors ${
                activeTab === tab
                  ? 'border-indigo-600 text-indigo-700'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
              aria-current={activeTab === tab ? 'page' : undefined}
            >
              {t(`projectDetails.tabs.${tab.toLowerCase()}`)}
            </button>
          ))}
        </nav>
        {activeTab === 'Tasks' && (
          <div className="flex items-center gap-2 shrink-0">
            <div ref={setPlannerToolbar} className="flex items-center" />
            <div ref={setTaskToolbar} className="flex items-center" />
            {!showPlanner && (
              <button onClick={() => setShowPlanner(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium text-emerald-600 hover:bg-emerald-50"
                title={t('projectDetails.showTheTaskPlanner')}>
                <PanelRightOpen className="w-4 h-4" /> {t('projectDetails.planner')}
              </button>
            )}
          </div>
        )}
      </div>

      {/* Tab Content */}
      {activeTab === 'Overview' && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
              <CheckSquare className="w-4 h-4 text-indigo-500" /> {t('projectDetails.tasks')}
            </h3>
            <div className="text-3xl font-bold text-gray-900">{project.tasks_count ?? 0}</div>
            <Link to={`/tasks?project=${project.id}`} className="text-xs text-indigo-600 hover:underline mt-1 block">
              {t('projectDetails.viewAllTasks')}
            </Link>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
              <GitBranch className="w-4 h-4 text-gray-500" /> {t('projectDetails.repository')}
            </h3>
            {project.repo?.type !== 'none' ? (
              <>
                <p className="text-sm text-gray-600 font-medium capitalize">{project.repo?.type}</p>
                {project.repo?.url && <p className="text-xs text-gray-400 truncate">{project.repo.url}</p>}
                <p className="text-xs text-gray-400">{t('projectDetails.branch')}: {project.repo?.branch}</p>
              </>
            ) : (
              <p className="text-sm text-gray-400 italic">{t('projectDetails.noRepoConfigured')}</p>
            )}
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
              <Clock className="w-4 h-4 text-gray-400" /> {t('projectDetails.info')}
            </h3>
            <p className="text-xs text-gray-500">{t('projectDetails.created')}</p>
            <p className="text-sm text-gray-700">{new Date(project.created_at).toLocaleDateString()}</p>
            <p className="text-xs text-gray-500 mt-2">{t('projectDetails.updated')}</p>
            <p className="text-sm text-gray-700">{new Date(project.updated_at).toLocaleDateString()}</p>
          </div>
        </div>
      )}

      {activeTab === 'Tasks' && (
        <div className="flex-1 min-h-0 flex gap-3">
          <div className="flex-1 min-w-0 min-h-0 overflow-y-auto">
            <TaskBoard
              workspace={project.workspace}
              projectId={project.id}
              selectedWorkspace={project.workspace}
              showWorkspaceColumn={false}
              liveUpdates={liveUpdates}
              toolbarTarget={taskToolbar}
            />
          </div>
          {showPlanner && (
            <PlannerChat projectId={project.id} onGenerated={loadTasks}
              onClose={() => setShowPlanner(false)} toolbarTarget={plannerToolbar} />
          )}
        </div>
      )}

      {activeTab === 'Overview' && (() => {
        const statusOrder = ['done', 'in_progress', 'ready', 'blocked', 'stopped', 'todo'];
        const statusLabels = {
          done: t('projectDetails.taskStatuses.done'),
          in_progress: t('projectDetails.taskStatuses.in_progress'),
          ready: t('projectDetails.taskStatuses.ready'),
          blocked: t('projectDetails.taskStatuses.blocked'),
          stopped: t('projectDetails.taskStatuses.stopped'),
          todo: t('projectDetails.taskStatuses.todo'),
        };
        const statusColors = {
          done: { bar: 'bg-green-500', badge: 'bg-green-100 text-green-700' },
          in_progress: { bar: 'bg-yellow-400', badge: 'bg-yellow-100 text-yellow-700' },
          ready: { bar: 'bg-blue-400', badge: 'bg-blue-100 text-blue-700' },
          blocked: { bar: 'bg-red-500', badge: 'bg-red-100 text-red-700' },
          stopped: { bar: 'bg-gray-400', badge: 'bg-gray-100 text-gray-500' },
          todo: { bar: 'bg-gray-300', badge: 'bg-gray-100 text-gray-500' },
        };
        const total = tasks.length;
        const doneCnt = tasks.filter(t => t.status === 'done').length;
        const pct = total > 0 ? Math.round((doneCnt / total) * 100) : 0;
        const byCounts = {};
        tasks.forEach(t => { byCounts[t.status] = (byCounts[t.status] || 0) + 1; });

        return (
          <div className="space-y-6 mt-6">
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <h3 className="text-base font-semibold text-gray-800 mb-4 flex items-center gap-2">
                {t('projectDetails.projectProgress')}
              </h3>
              {tasksLoading ? (
                <PageLoader size="sm" label={t('projectDetails.loading')} />
              ) : total === 0 ? (
                <div className="text-center py-8 text-gray-400">
                  <CheckSquare className="w-10 h-10 mx-auto mb-2 opacity-30" />
                  <p className="text-sm">{t('projectDetails.noTasksInThisProject')}</p>
                </div>
              ) : (
                <>
                  <div className="flex items-end justify-between mb-2">
                    <span className="text-3xl font-bold text-gray-900">{pct}%</span>
                    <span className="text-sm text-gray-500">{t('projectDetails.tasksDone', { done: doneCnt, total })}</span>
                  </div>
                  <div className="w-full bg-gray-100 rounded-full h-3 mb-6">
                    <div className="bg-green-500 h-3 rounded-full transition-all" style={{ width: `${pct}%` }} />
                  </div>
                  <div className="flex flex-wrap items-center gap-2 mb-6">
                    {statusOrder.map(s => {
                      const cnt = byCounts[s] || 0;
                      if (!cnt) return null;
                      const color = statusColors[s] || { badge: 'bg-gray-100 text-gray-500' };
                      return (
                        <div key={s} className="inline-flex items-center gap-2 px-2.5 py-1 rounded-lg border border-gray-100 bg-gray-50">
                          <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${color.badge}`}>
                            {statusLabels[s] || s}
                          </span>
                          <span className="text-sm font-bold text-gray-700">{cnt}</span>
                        </div>
                      );
                    })}
                  </div>

                  {/* Task list */}
                  <div className="border-t border-gray-100 pt-4 mb-6">
                    <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">{t('projectDetails.tasks')}</h4>
                    {/* Small cards: a task here is its name and where it stands. */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2">
                      {tasks.map(task => {
                        const color = statusColors[task.status] || { bar: 'bg-gray-300', badge: 'bg-gray-100 text-gray-500' };
                        return (
                          <Link
                            key={task.id}
                            to={`/tasks/${task.id}`}
                            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-gray-200 bg-white hover:border-indigo-300 hover:shadow-sm transition-all min-w-0"
                          >
                            <span className={`w-1 self-stretch rounded-full shrink-0 ${color.bar}`} />
                            <span className="text-sm text-gray-800 truncate flex-1" title={task.title}>{task.title}</span>
                            <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium shrink-0 ${color.badge}`}>
                              {statusLabels[task.status] || task.status}
                            </span>
                          </Link>
                        );
                      })}
                    </div>
                  </div>

                  {/* Activity log */}
                  {(() => {
                    const logEntries = tasks.flatMap(t =>
                      (t.activity_log || []).map(e => ({ ...e, taskTitle: t.title, taskId: t.id }))
                    ).sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
                    if (!logEntries.length) return null;
                    const typeStyle = {
                      created: { dot: 'bg-indigo-400', text: 'text-indigo-600' },
                      status_change: { dot: 'bg-yellow-400', text: 'text-yellow-700' },
                      agent_assigned: { dot: 'bg-blue-400', text: 'text-blue-700' },
                      agent_state: { dot: 'bg-purple-400', text: 'text-purple-700' },
                      agent_cleared: { dot: 'bg-gray-400', text: 'text-gray-500' },
                    };
                    return (
                      <div className="border-t border-gray-100 pt-4">
                        <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">{t('projectDetails.activityLog')}</h4>
                        <div className="space-y-0 max-h-80 overflow-y-auto">
                          {logEntries.map((e, i) => {
                            const style = typeStyle[e.type] || { dot: 'bg-gray-300', text: 'text-gray-600' };
                            const ts = new Date(e.timestamp);
                            return (
                              <div key={i} className="flex gap-3 pb-3">
                                <div className="flex flex-col items-center">
                                  <span className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${style.dot}`} />
                                  {i < logEntries.length - 1 && <span className="w-px flex-1 bg-gray-100 mt-1" />}
                                </div>
                                <div className="pb-1 min-w-0">
                                  <div className="flex items-baseline gap-2 flex-wrap">
                                    <span className={`text-xs font-medium ${style.text}`}>{e.message}</span>
                                    <Link to={`/tasks/${e.taskId}`} className="text-xs text-gray-400 hover:text-indigo-500 truncate max-w-xs">{e.taskTitle}</Link>
                                  </div>
                                  <span className="text-[10px] text-gray-400">{ts.toLocaleString()}</span>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })()}
                </>
              )}
            </div>
          </div>
        );
      })()}

      {activeTab === 'Architecture' && (
        <ProjectGraph projectId={project.id} />
      )}

      {activeTab === 'Files' && (
        /* The whole height of the page: the tree and the file each scroll
           on their own inside it. */
        <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6 flex-1 min-h-0 flex flex-col">
          <h3 className="text-base font-semibold text-gray-800 mb-4 flex items-center gap-2 shrink-0">
            <FileText className="w-5 h-5 text-indigo-500" /> {t('projectDetails.projectFiles')}
          </h3>
          {filesLoading ? (
            <p className="text-sm text-gray-400">{t('projectDetails.loadingFiles')}</p>
          ) : filesError ? (
            <p className="text-sm text-red-500">{filesError}</p>
          ) : files.length ? (
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 flex-1 min-h-0">
              <div className="lg:col-span-4 border border-gray-200 rounded-lg p-2 min-h-0 overflow-y-auto">
                {renderFileNodes(fileTree)}
              </div>
              <div className="lg:col-span-8 border border-gray-200 rounded-lg overflow-hidden min-h-0 flex flex-col">
                <div className="px-4 py-2 border-b bg-gray-50 shrink-0 flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-xs text-gray-500">{t('projectDetails.selectedFile')}</div>
                    <div className="text-sm text-gray-700 truncate">{selectedFilePath || '-'}</div>
                    {selectedFileSize > 0 && (
                      <div className="text-xs text-gray-400 mt-0.5">{t('projectDetails.bytes', { count: selectedFileSize })}</div>
                    )}
                  </div>
                  {selectedFilePath && selectedFile && (
                    <div className="flex items-center gap-2 shrink-0">
                      {hasSourceView(viewedFile) && (
                        <div className="flex rounded-lg border border-gray-200 overflow-hidden text-xs font-semibold">
                          <button
                            type="button"
                            onClick={() => setFileSourceView(false)}
                            className={`inline-flex items-center gap-1 px-2.5 py-1 transition-colors ${!fileSourceView ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                          >
                            <Eye className="w-3.5 h-3.5" /> {t('projectDetails.fileView.rendered')}
                          </button>
                          <button
                            type="button"
                            onClick={() => setFileSourceView(true)}
                            className={`inline-flex items-center gap-1 px-2.5 py-1 transition-colors border-l border-gray-200 ${fileSourceView ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                          >
                            <Code2 className="w-3.5 h-3.5" /> {t(isPdfFile(viewedFile) ? 'projectDetails.fileView.text' : 'projectDetails.fileView.source')}
                          </button>
                        </div>
                      )}
                      <button
                        type="button"
                        onClick={downloadSelectedFile}
                        title={t('projectDetails.fileView.download')}
                        aria-label={t('projectDetails.fileView.download')}
                        className="inline-flex items-center p-1.5 text-gray-600 border border-gray-200 rounded-lg hover:bg-white"
                      >
                        <Download className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}
                </div>
                <div className={`${framed ? '' : 'p-4'} flex-1 min-h-0 overflow-auto`}>
                  {fileContentLoading ? (
                    <p className="text-sm text-gray-500 p-4">{t('projectDetails.loading')}</p>
                  ) : fileContentError ? (
                    <p className="text-sm text-red-600 p-4">{fileContentError}</p>
                  ) : selectedFilePath && selectedFile ? (
                    <FileViewer
                      name={selectedFilePath} mimeType={selectedFile.mimeType} url={selectedFileUrl}
                      text={selectedFile.text} kind={selectedFile.kind} truncated={selectedFile.truncated}
                      source={fileSourceView} frameClassName="w-full h-full border-0 bg-white"
                      codeClassName=""
                    />
                  ) : (
                    <p className="text-sm text-gray-500">{t('projectDetails.selectAFileToPreview')}</p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-400">{t('projectDetails.noFilesFoundInThis')}</p>
          )}
        </div>
      )}

      {activeTab === 'Repository' && (
        <div className="space-y-4">
          {/* Actions */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <div className="flex items-center gap-3 flex-wrap">
              <h3 className="text-sm font-semibold text-gray-700 flex-1">
                <GitBranch className="w-4 h-4 inline mr-1.5 text-gray-500" />
                {project.repo?.type !== 'none' ? `${project.repo?.type} — ${project.repo?.url || t('projectDetails.noUrl')}` : t('projectDetails.noRepoConfigured')}
              </h3>
              {!isConnectedRepo && (
                <button
                  onClick={() => setShowConnect(true)}
                  className="flex items-center gap-1.5 px-3 py-1.5 border border-indigo-200 text-indigo-700 text-xs font-medium rounded-lg hover:bg-indigo-50"
                >
                  <Link2 className="w-3.5 h-3.5" /> {t('projectDetails.connectRepository')}
                </button>
              )}
              {canPublishBranch && (
                <button
                  onClick={() => { setShowPublish(true); setPublishError(''); setPrUrl(''); setPushResult(null); }}
                  className="flex items-center gap-1.5 px-3 py-1.5 border border-indigo-200 text-indigo-700 text-xs font-medium rounded-lg hover:bg-indigo-50"
                >
                  <Send className="w-3.5 h-3.5" /> {t('projectDetails.publish.button')}
                </button>
              )}
              {isConnectedRepo && (
                <button
                  onClick={handleSyncIssues} disabled={syncingIssues}
                  className="flex items-center gap-1.5 px-3 py-1.5 border border-indigo-200 text-indigo-700 text-xs font-medium rounded-lg hover:bg-indigo-50 disabled:opacity-50"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${syncingIssues ? 'animate-spin' : ''}`} /> {syncingIssues ? t('projectDetails.syncing') : t('projectDetails.syncIssues')}
                </button>
              )}
              {project.repo?.url && (
                <>
                  <button
                    onClick={handleClone} disabled={cloning}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 text-white text-xs font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                  >
                    <Download className="w-3.5 h-3.5" /> {cloning ? 'Cloning…' : 'Clone'}
                  </button>
                  <button
                    onClick={handlePull} disabled={pulling}
                    className="flex items-center gap-1.5 px-3 py-1.5 border border-gray-200 text-xs font-medium rounded-lg hover:bg-gray-50 disabled:opacity-50"
                  >
                    <ArrowUpDown className="w-3.5 h-3.5" /> {pulling ? 'Pulling…' : 'Pull'}
                  </button>
                </>
              )}
              <button onClick={loadGitStatus} className="p-1.5 text-gray-400 hover:text-gray-600 rounded">
                <RefreshCw className="w-4 h-4" />
              </button>
            </div>
            {gitMsg && (
              <div className={`mt-3 p-3 rounded-lg text-xs whitespace-pre-wrap ${
                gitMsg.toLowerCase().includes('fail') || gitMsg.toLowerCase().includes('error')
                  ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700'
              }`}>{gitMsg}</div>
            )}
          </div>

          <TrackerCard projectId={project.id} />

          {gitLoading ? (
            <PageLoader size="sm" label={t('projectDetails.loadingGitStatus')} />
          ) : gitStatus ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
                  {t('projectDetails.branch')}: <span className="text-gray-900 normal-case font-bold">{gitStatus.branch || t('common.unknown')}</span>
                </h4>
                <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">{t('projectDetails.status')}</h4>
                <pre className="text-xs bg-gray-50 rounded-lg p-3 whitespace-pre-wrap text-gray-700 max-h-48 overflow-y-auto">
                  {gitStatus.status || t('projectDetails.cleanWorkingTree')}
                </pre>
              </div>
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">{t('projectDetails.recentCommits')}</h4>
                <pre className="text-xs bg-gray-50 rounded-lg p-3 whitespace-pre-wrap text-gray-700 max-h-48 overflow-y-auto">
                  {gitStatus.recent_commits || t('projectDetails.noCommits')}
                </pre>
              </div>
            </div>
          ) : (
            <div className="text-center py-8 text-gray-400">
              <Terminal className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-sm">{t('projectDetails.noLocalRepoFoundClone')}</p>
            </div>
          )}
        </div>
      )}

      {activeTab === 'Deploy' && (
        <DeployPanel project={project} />
      )}

      {/* Publish branch modal */}
      {showPublish && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-lg bg-white rounded-xl shadow-lg border border-gray-200">
            <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100">
              <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-2">
                <Send className="w-4 h-4 text-indigo-600" /> {t('projectDetails.publish.title')}
              </h3>
              <button onClick={() => setShowPublish(false)} className="text-gray-400 hover:text-gray-600">
                <X className="w-4 h-4" />
              </button>
            </div>
            <form onSubmit={handlePublish} className="p-5 space-y-4">
              {!isConnectedRepo && (
                <p className="text-xs text-gray-600 bg-gray-50 border border-gray-200 rounded-lg p-2">
                  {t('projectDetails.publish.noProviderNotice')}
                </p>
              )}
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  {isConnectedRepo ? t('projectDetails.publish.prTitle') : t('projectDetails.publish.commitMessage')}
                </label>
                <input
                  required
                  value={publishForm.title}
                  onChange={e => setPublishForm(f => ({ ...f, title: e.target.value }))}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
              </div>
              {isConnectedRepo && (
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">{t('projectDetails.publish.body')}</label>
                  <textarea
                    rows={4}
                    value={publishForm.body}
                    onChange={e => setPublishForm(f => ({ ...f, body: e.target.value }))}
                    placeholder={t('projectDetails.publish.bodyPlaceholder')}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                  />
                </div>
              )}
              <div className={isConnectedRepo ? 'grid grid-cols-2 gap-3' : ''}>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">{t('projectDetails.publish.branch')}</label>
                  <input
                    value={publishForm.branch}
                    onChange={e => setPublishForm(f => ({ ...f, branch: e.target.value }))}
                    placeholder={t('projectDetails.publish.optional')}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                  />
                </div>
                {isConnectedRepo && (
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">{t('projectDetails.publish.base')}</label>
                    <input
                      value={publishForm.base}
                      onChange={e => setPublishForm(f => ({ ...f, base: e.target.value }))}
                      placeholder={t('projectDetails.publish.optional')}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                    />
                  </div>
                )}
              </div>
              {isConnectedRepo && (
                <label className="flex items-center gap-2 text-sm text-gray-700">
                  <input
                    type="checkbox"
                    checked={publishForm.draft}
                    onChange={e => setPublishForm(f => ({ ...f, draft: e.target.checked }))}
                    className="accent-indigo-600"
                  />
                  {t('projectDetails.publish.draft')}
                </label>
              )}

              {publishError && (
                <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-2">{publishError}</p>
              )}
              {prUrl && (
                <p className="text-xs text-green-700 bg-green-50 border border-green-200 rounded-lg p-2">
                  {t('projectDetails.publish.opened')}{' '}
                  <a href={prUrl} target="_blank" rel="noreferrer" className="underline font-medium">{prUrl}</a>
                </p>
              )}
              {pushResult && (
                <p className="text-xs text-green-700 bg-green-50 border border-green-200 rounded-lg p-2">
                  {t('projectDetails.publish.pushedBranch', { branch: pushResult.branch, remote: pushResult.remote })}
                </p>
              )}

              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setShowPublish(false)}
                  className="px-3 py-1.5 text-xs font-medium border border-gray-200 rounded-lg hover:bg-gray-50"
                >
                  {t('common.close')}
                </button>
                <button
                  type="submit"
                  disabled={publishing || !publishForm.title.trim()}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 text-white text-xs font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                >
                  <Send className="w-3.5 h-3.5" />
                  {publishing ? t('projectDetails.publish.publishing') : t('projectDetails.publish.button')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Connect repository modal */}
      {showConnect && (
        <ImportRepoModal
          mode="connect"
          project={project}
          onClose={() => setShowConnect(false)}
          onDone={handleConnected}
        />
      )}
    </PageContainer>
  );
}
