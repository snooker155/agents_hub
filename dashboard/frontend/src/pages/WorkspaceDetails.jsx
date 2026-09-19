import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch } from '../components/stream';
import { getWorkspace, getWorkspaceFilesByName, getWorkspaceFileContent, getAgents, addAgentToWorkspace, removeAgentFromWorkspace, deleteWorkspace, getProjects, getWorkspaceInstructions, updateWorkspaceInstructions, uploadWorkspaceFile, getWorkspaceFileRawUrl, deleteWorkspaceFile, listFlows, removeFlowFromWorkspace } from '../api';
import { ChevronDown, ChevronRight, Folder, FolderOpen, FileText, Users, ShoppingBag, Plus, Trash2, Shield, Search, CheckSquare, AlertTriangle, Lock, FolderGit2, Globe, Server, GitBranch, BarChart2, BookOpen, Save, Check, Upload, Eye, Code2, Workflow } from 'lucide-react';
import MarkdownRenderer from '../components/MarkdownRenderer';
import TaskBoard from '../components/TaskBoard';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
// Tab ids the page can open, so a ?tab= deep-link can be validated before use.
const WORKSPACE_TABS = ['files', 'agents', 'instructions', 'tasks', 'projects', 'progress'];

const isMarkdownPath = (p) => /\.(md|markdown|mdx)$/i.test(String(p || ''));

const buildFileTree = (paths, directories = []) => {
  const root = { type: 'dir', children: {} };
  (directories || []).forEach((rawPath) => {
    const cleanPath = String(rawPath || '').trim();
    if (!cleanPath) return;
    const parts = cleanPath.split('/').filter(Boolean);
    let node = root;
    parts.forEach((part) => {
      if (!node.children[part]) {
        node.children[part] = { type: 'dir', name: part, children: {} };
      }
      node = node.children[part];
    });
  });
  (paths || []).forEach((rawPath) => {
    const cleanPath = String(rawPath || '').trim();
    if (!cleanPath) return;
    const parts = cleanPath.split('/').filter(Boolean);
    let node = root;
    parts.forEach((part, idx) => {
      const isFile = idx === parts.length - 1;
      if (!node.children[part]) {
        node.children[part] = isFile
          ? { type: 'file', name: part, path: parts.join('/') }
          : { type: 'dir', name: part, children: {} };
      }
      node = node.children[part];
    });
  });

  const toArray = (node, parentPath = '') => (
    Object.keys(node.children || {})
      .sort((a, b) => {
        const aNode = node.children[a];
        const bNode = node.children[b];
        if (aNode.type !== bNode.type) return aNode.type === 'dir' ? -1 : 1;
        return a.localeCompare(b);
      })
      .map((name) => {
        const child = node.children[name];
        const fullPath = parentPath ? `${parentPath}/${name}` : name;
        if (child.type === 'dir') {
          return {
            type: 'dir',
            name,
            path: fullPath,
            children: toArray(child, fullPath),
          };
        }
        return {
          type: 'file',
          name,
          path: child.path || fullPath,
        };
      })
  );

  return toArray(root);
};

const parentDirPaths = (filePath) => {
  const parts = String(filePath || '').split('/').filter(Boolean);
  const dirs = [];
  for (let i = 1; i < parts.length; i += 1) {
    dirs.push(parts.slice(0, i).join('/'));
  }
  return dirs;
};

const WorkspaceDetails = () => {
  const { t } = useI18n();
  const { name } = useParams();
  const navigate = useNavigate();
  // Deep-link support: ?tab=<tab>&file=<workspace-relative path>. Chat replies
  // link a file the agent wrote straight to it (see common/entity_links.py).
  const [searchParams] = useSearchParams();
  const linkedTab = searchParams.get('tab') || '';
  const linkedFile = searchParams.get('file') || '';
  const { liveUpdates } = useWorkspace();
  const [ws, setWs] = useState(null);
  const [files, setFiles] = useState([]);
  const [folders, setFolders] = useState([]);
  const [allAgents, setAllAgents] = useState([]);
  const [allFlows, setAllFlows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState(
    WORKSPACE_TABS.includes(linkedTab) ? linkedTab : 'files');
  const [agentSearch, setAgentSearch] = useState('');
  const [taskToolbar, setTaskToolbar] = useState(null);
  const [wsProjects, setWsProjects] = useState([]);
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [selectedFilePath, setSelectedFilePath] = useState('');
  const [selectedFileContent, setSelectedFileContent] = useState('');
  const [selectedFileSize, setSelectedFileSize] = useState(0);
  const [selectedFileIsPdf, setSelectedFileIsPdf] = useState(false);
  const [pdfViewMode, setPdfViewMode] = useState('render');
  const [mdViewMode, setMdViewMode] = useState('rendered'); // 'rendered' | 'raw'
  const [fileContentLoading, setFileContentLoading] = useState(false);
  const [fileContentError, setFileContentError] = useState('');
  const [fileDeleting, setFileDeleting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = React.useRef(null);
  const [instructions, setInstructions] = useState('');
  const [instructionsDraft, setInstructionsDraft] = useState('');
  const [instructionsSaving, setInstructionsSaving] = useState(false);
  const [instructionsSaved, setInstructionsSaved] = useState(false);
  const instructionsLoadedRef = React.useRef(false);

  const fetchData = useCallback(async () => {
    try {
      // Fetch in parallel but handle partial failures so the page still opens
      const [wsRes, filesRes, agentsRes, projectsRes, instrRes, flowsRes] = await Promise.allSettled([
        getWorkspace(name),
        getWorkspaceFilesByName(name),
        getAgents(),
        getProjects(name),
        getWorkspaceInstructions(name),
        listFlows(),
      ]);

      if (wsRes.status === 'fulfilled') {
        setWs(wsRes.value.data);
      } else {
        console.error(t('workspaceDetails.errors.info'), wsRes.reason);
      }

      if (filesRes.status === 'fulfilled') {
        setFiles((filesRes.value.data.files || []).filter(
          (p) => !p.split('/').some((seg) => seg.startsWith('.'))
        ));
        setFolders((filesRes.value.data.directories || []).filter(
          (p) => !p.split('/').some((seg) => seg.startsWith('.'))
        ));
      } else {
        console.warn(t('workspaceDetails.errors.files'), filesRes.reason);
        setFiles([]);
        setFolders([]);
      }

      if (agentsRes.status === 'fulfilled') {
        setAllAgents(agentsRes.value.data || []);
      } else {
        console.warn(t('workspaceDetails.errors.agents'), agentsRes.reason);
        setAllAgents([]);
      }

      if (projectsRes.status === 'fulfilled') {
        setWsProjects(projectsRes.value.data || []);
      } else {
        setWsProjects([]);
      }

      if (instrRes.status === 'fulfilled') {
        const text = instrRes.value.data?.instructions || '';
        setInstructions(text);
        if (!instructionsLoadedRef.current) {
          setInstructionsDraft(text);
          instructionsLoadedRef.current = true;
        }
      }

      if (flowsRes.status === 'fulfilled') {
        setAllFlows(flowsRes.value.data || []);
      } else {
        setAllFlows([]);
      }
    } catch (e) {
      console.error(t('workspaceDetails.errors.unexpected'), e);
    } finally {
      setLoading(false);
    }
  }, [name, t]);

  useEffect(() => { fetchData(); }, [name, liveUpdates, fetchData]);
  useLiveRefetch(fetchData, { enabled: liveUpdates });

  const handleDelete = async () => {
    if (!window.confirm(`Delete workspace "${ws.name}"? This cannot be undone.`)) return;
    try {
      await deleteWorkspace(name);
      navigate('/workspaces');
    } catch {
      alert(t('workspaceDetails.errors.deleteWorkspace'));
    }
  };

  const handleAddAgent = async (agentId) => {
    try {
      await addAgentToWorkspace(name, agentId);
      fetchData();
    } catch {
      alert(t('workspaceDetails.errors.addAgent'));
    }
  };

  const handleRemoveAgent = async (agentId) => {
    try {
      await removeAgentFromWorkspace(name, agentId);
      fetchData();
    } catch {
      alert(t('workspaceDetails.errors.removeAgent'));
    }
  };

  const handleRemoveFlow = async (flowId) => {
    try {
      await removeFlowFromWorkspace(name, flowId);
      fetchData();
    } catch {
      alert(t('workspaceDetails.errors.removeFlow'));
    }
  };

  const loadFileContent = useCallback(async (path) => {
    if (!path) return;
    setSelectedFilePath(path);
    setFileContentLoading(true);
    setFileContentError('');
    try {
      const resp = await getWorkspaceFileContent(name, path);
      setSelectedFileContent(resp.data?.content || '');
      setSelectedFileSize(Number(resp.data?.size || 0));
      setSelectedFileIsPdf(!!resp.data?.is_pdf);
      setPdfViewMode('render');
      setMdViewMode('rendered');
    } catch (e) {
      const detail = e?.response?.data?.detail || t('workspaceDetails.errors.fileContent');
      setFileContentError(detail);
      setSelectedFileContent('');
      setSelectedFileSize(0);
      setSelectedFileIsPdf(false);
    } finally {
      setFileContentLoading(false);
    }
  }, [name, t]);

  const handleUploadFiles = async (fileList) => {
    const selected = Array.from(fileList || []);
    if (!selected.length) return;
    setUploading(true);
    let lastPath = '';
    try {
      for (const f of selected) {
        const resp = await uploadWorkspaceFile(name, f);
        lastPath = resp.data?.path || lastPath;
      }
      await fetchData();
      if (lastPath) loadFileContent(lastPath);
    } catch (e) {
      const detail = e?.response?.data?.detail || t('workspaceDetails.errors.upload');
      alert(detail);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleDeleteWorkspacePath = async (path, type = 'file') => {
    if (!path || fileDeleting) return;
    const label = type === 'dir' ? t('workspaceDetails.folder') : t('workspaceDetails.file');
    const warning = type === 'dir' ? ` ${t('workspaceDetails.deleteFolderWarning')}` : '';
    if (!window.confirm(t('workspaceDetails.confirmDeletePath', { label, path, workspace: name }) + warning)) return;
    setFileDeleting(true);
    setFileContentError('');
    try {
      await deleteWorkspaceFile(name, path);
      if (type === 'dir') {
        const prefix = `${path}/`;
        setFiles(prev => prev.filter(p => p !== path && !p.startsWith(prefix)));
        setFolders(prev => prev.filter(p => p !== path && !p.startsWith(prefix)));
        if (selectedFilePath === path || selectedFilePath.startsWith(prefix)) {
          setSelectedFilePath('');
          setSelectedFileContent('');
          setSelectedFileSize(0);
          setSelectedFileIsPdf(false);
        }
      } else {
        setFiles(prev => prev.filter(p => p !== path));
        if (selectedFilePath === path) {
          setSelectedFilePath('');
          setSelectedFileContent('');
          setSelectedFileSize(0);
          setSelectedFileIsPdf(false);
        }
      }
      await fetchData();
    } catch (e) {
      const detail = e?.response?.data?.detail || `Failed to delete ${label}`;
      setFileContentError(detail);
    } finally {
      setFileDeleting(false);
    }
  };

  const handleDeleteSelectedFile = async () => {
    await handleDeleteWorkspacePath(selectedFilePath, 'file');
  };

  const toggleFolder = (folderPath) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folderPath)) next.delete(folderPath);
      else next.add(folderPath);
      return next;
    });
  };

  const handleSaveInstructions = async () => {
    setInstructionsSaving(true);
    setInstructionsSaved(false);
    try {
      await updateWorkspaceInstructions(name, instructionsDraft);
      setInstructions(instructionsDraft);
      setInstructionsSaved(true);
      setTimeout(() => setInstructionsSaved(false), 2000);
    } catch {
      alert(t('workspaceDetails.errors.instructions'));
    } finally {
      setInstructionsSaving(false);
    }
  };

  // Keep in sync with WORKSPACE_TABS (the ?tab= allowlist).
  const tabs = [
    { id: 'files', label: 'Files', icon: FileText },
    { id: 'agents', label: 'Agents', icon: Users },
    { id: 'instructions', label: 'Instructions', icon: BookOpen },
    { id: 'tasks', label: 'Tasks', icon: CheckSquare },
    { id: 'projects', label: 'Projects', icon: FolderGit2 },
    { id: 'progress', label: 'Progress', icon: BarChart2 },
  ];
  const marketAgents = allAgents.filter(a => !ws?.metadata?.allowed_agents?.includes(a.id));
  const normalizedQuery = agentSearch.trim().toLowerCase();
  const filteredMarketAgents = marketAgents.filter((agent) => {
    if (!normalizedQuery) return true;
    const haystack = [
      agent.name,
      agent.id,
      agent.domain,
      agent.description,
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return haystack.includes(normalizedQuery);
  });
  const fileTree = useMemo(() => buildFileTree(files, folders), [files, folders]);

  useEffect(() => {
    if (!files.length && !folders.length) {
      setSelectedFilePath('');
      setSelectedFileContent('');
      setSelectedFileSize(0);
      setExpandedFolders(new Set());
      return;
    }

    // Expand top-level folders by default.
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      folders.forEach((p) => {
        const top = String(p || '').split('/').filter(Boolean)[0];
        if (top) next.add(top);
      });
      files.forEach((p) => {
        const top = String(p || '').split('/').filter(Boolean)[0];
        if (top && String(p).includes('/')) next.add(top);
      });
      return next;
    });

    if (!selectedFilePath || !files.includes(selectedFilePath)) {
      const first = files.includes(linkedFile) ? linkedFile : files[0];
      if (first) {
        setExpandedFolders((prev) => {
          const next = new Set(prev);
          parentDirPaths(first).forEach((dir) => next.add(dir));
          return next;
        });
        loadFileContent(first);
      } else {
        setSelectedFilePath('');
        setSelectedFileContent('');
        setSelectedFileSize(0);
        setSelectedFileIsPdf(false);
      }
    }
  }, [files, folders, loadFileContent, selectedFilePath, linkedFile]);

  const renderFileNodes = (nodes, depth = 0) => nodes.map((node) => {
    if (node.type === 'dir') {
      const open = expandedFolders.has(node.path);
      return (
        <div key={node.path}>
          <div
            className="group w-full flex items-center gap-1.5 px-2 py-1 text-sm text-gray-700 hover:bg-gray-50 rounded"
            style={{ paddingLeft: `${depth * 14 + 8}px` }}
          >
            <button
              type="button"
              onClick={() => toggleFolder(node.path)}
              className="flex items-center gap-1.5 min-w-0 flex-1 text-left"
              title={node.path}
            >
              {open ? <ChevronDown className="w-3.5 h-3.5 text-gray-400 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-gray-400 shrink-0" />}
              {open ? <FolderOpen className="w-4 h-4 text-amber-500 shrink-0" /> : <Folder className="w-4 h-4 text-amber-500 shrink-0" />}
              <span className="truncate">{node.name}</span>
            </button>
            <button
              type="button"
              onClick={() => handleDeleteWorkspacePath(node.path, 'dir')}
              disabled={fileDeleting}
              className="p-1 rounded text-gray-300 hover:text-red-600 hover:bg-red-50 opacity-0 group-hover:opacity-100 focus:opacity-100 disabled:opacity-30"
              title={t('workspaceDetails.deleteFolder')}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
          {open && node.children?.length > 0 && renderFileNodes(node.children, depth + 1)}
        </div>
      );
    }

    const isSelected = node.path === selectedFilePath;
    return (
      <div
        key={node.path}
        className={`group w-full flex items-center gap-1.5 px-2 py-1 text-sm rounded ${
          isSelected
            ? 'bg-indigo-50 text-indigo-700'
            : 'text-gray-700 hover:bg-gray-50'
        }`}
        style={{ paddingLeft: `${depth * 14 + 28}px` }}
      >
        <button
          type="button"
          onClick={() => {
            setExpandedFolders((prev) => {
              const next = new Set(prev);
              parentDirPaths(node.path).forEach((dir) => next.add(dir));
              return next;
            });
            loadFileContent(node.path);
          }}
          className="flex items-center gap-1.5 min-w-0 flex-1 text-left"
          title={node.path}
        >
          <FileText className="w-4 h-4 text-gray-400 shrink-0" />
          <span className="truncate">{node.name}</span>
        </button>
        <button
          type="button"
          onClick={() => handleDeleteWorkspacePath(node.path, 'file')}
          disabled={fileDeleting}
          className="p-1 rounded text-gray-300 hover:text-red-600 hover:bg-red-50 opacity-0 group-hover:opacity-100 focus:opacity-100 disabled:opacity-30"
          title={t('workspaceDetails.deleteFile')}
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    );
  });

  if (loading) return <div className="text-center py-10">{t('workspaceDetails.loadingWorkspace')}</div>;
  if (!ws) return <div className="text-center py-10">{t('workspaceDetails.workspaceNotFound')}</div>;

  return (
    <PageContainer fill={activeTab === 'files' || activeTab === 'tasks'}>
      <PageHeader
        icon={Folder}
        title={ws.name}
        description={ws.path}
        backTo="/workspaces"
        backLabel={t('workspaceDetails.workspaces')}
        actions={
          <button
            onClick={handleDelete}
            className="flex items-center rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-medium text-rose-600 transition hover:bg-rose-100"
            title={t('workspaceDetails.deleteWorkspace')}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            {t('workspaceDetails.deleteWorkspace2')}
          </button>
        }
      />

      <div className="border-b border-gray-200 shrink-0 flex items-end justify-between gap-3">
        <nav className="flex flex-wrap gap-2 -mb-px">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`inline-flex items-center px-4 py-2 first:pl-0 text-sm font-semibold border-b-2 transition-colors ${
                  isActive
                    ? 'border-indigo-600 text-indigo-700'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
                aria-current={isActive ? 'page' : undefined}
              >
                <Icon className="w-4 h-4 mr-2" />
                {tab.label}
              </button>
            );
          })}
        </nav>
        {activeTab === 'tasks' && <div ref={setTaskToolbar} className="flex items-center shrink-0" />}
      </div>

      {activeTab === 'tasks' && (
        <div className="flex-1 min-h-0 overflow-y-auto">
          <TaskBoard
            workspace={name}
            selectedWorkspace={name}
            showWorkspaceColumn={false}
            liveUpdates={liveUpdates}
            toolbarTarget={taskToolbar}
          />
        </div>
      )}

      {activeTab === 'projects' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-bold flex items-center gap-2">
              <FolderGit2 className="w-5 h-5 text-indigo-500" /> {t('workspaceDetails.projects')}
            </h3>
            <Link
              to="/projects"
              className="text-sm text-indigo-600 hover:text-indigo-800 font-medium"
            >
              {t('workspaceDetails.manageAllProjects')}
            </Link>
          </div>
          {wsProjects.length === 0 ? (
            <div className="text-center py-10 text-gray-400">
              <FolderGit2 className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-sm">{t('workspaceDetails.noProjectsInThisWorkspace')}</p>
              <Link
                to="/projects"
                className="mt-2 inline-block text-xs text-indigo-600 hover:underline"
              >
                {t('workspaceDetails.createAProject')}
              </Link>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {wsProjects.map(project => (
                <Link
                  key={project.id}
                  to={`/projects/${project.id}`}
                  className="flex items-start gap-3 p-4 border border-gray-200 rounded-xl hover:bg-indigo-50 hover:border-indigo-200 transition-colors group"
                >
                  <div className="w-9 h-9 rounded-lg bg-indigo-100 flex items-center justify-center shrink-0">
                    <FolderGit2 className="w-4 h-4 text-indigo-600" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-gray-900 group-hover:text-indigo-700 truncate">{project.name}</span>
                      <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium shrink-0 ${
                        project.status === 'active' ? 'bg-green-100 text-green-700' :
                        project.status === 'completed' ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'
                      }`}>{project.status}</span>
                    </div>
                    {project.description && (
                      <p className="text-xs text-gray-400 mt-0.5 line-clamp-1">{project.description}</p>
                    )}
                    <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                      <span className="text-xs text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded">{project.type}</span>
                      {project.repo?.type && project.repo.type !== 'none' && (
                        <span className="flex items-center gap-0.5 text-xs text-gray-500">
                          <GitBranch className="w-3 h-3" />{project.repo.type}
                        </span>
                      )}
                      {project.frontend?.enabled && (
                        <span className="flex items-center gap-0.5 text-xs text-blue-500">
                          <Globe className="w-3 h-3" /> {t('workspaceDetails.frontend')}
                        </span>
                      )}
                      {project.backend?.enabled && (
                        <span className="flex items-center gap-0.5 text-xs text-green-600">
                          <Server className="w-3 h-3" /> {t('workspaceDetails.backend')}
                        </span>
                      )}
                      {project.tasks_count > 0 && (
                        <span className="text-xs text-indigo-500">{t('workspaceDetails.taskCount', { count: project.tasks_count })}</span>
                      )}
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-indigo-400 shrink-0 mt-1" />
                </Link>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === 'progress' && (() => {
        const allTasks = ws?.tasks || [];
        const statusOrder = ['done', 'in_progress', 'ready', 'blocked', 'stopped', 'todo'];
        const statusLabels = {
          done: t('workspaceDetails.taskStatuses.done'),
          in_progress: t('workspaceDetails.taskStatuses.in_progress'),
          ready: t('workspaceDetails.taskStatuses.ready'),
          blocked: t('workspaceDetails.taskStatuses.blocked'),
          stopped: t('workspaceDetails.taskStatuses.stopped'),
          todo: t('workspaceDetails.taskStatuses.todo'),
        };
        const statusColors = {
          done: { bar: 'bg-green-500', badge: 'bg-green-100 text-green-700' },
          in_progress: { bar: 'bg-yellow-400', badge: 'bg-yellow-100 text-yellow-700' },
          ready: { bar: 'bg-blue-400', badge: 'bg-blue-100 text-blue-700' },
          blocked: { bar: 'bg-red-500', badge: 'bg-red-100 text-red-700' },
          stopped: { bar: 'bg-gray-400', badge: 'bg-gray-100 text-gray-500' },
          todo: { bar: 'bg-gray-300', badge: 'bg-gray-100 text-gray-500' },
        };
        const total = allTasks.length;
        const doneCnt = allTasks.filter(t => t.status === 'done').length;
        const pct = total > 0 ? Math.round((doneCnt / total) * 100) : 0;

        const byCounts = {};
        allTasks.forEach(t => { byCounts[t.status] = (byCounts[t.status] || 0) + 1; });

        // Group by project
        const byProject = {};
        allTasks.forEach(t => {
          const key = t.project_id || '__none__';
          if (!byProject[key]) byProject[key] = { tasks: [], name: null };
          byProject[key].tasks.push(t);
        });
        wsProjects.forEach(p => { if (byProject[p.id]) byProject[p.id].name = p.name; });

        return (
          <div className="space-y-6">
            {/* Summary card */}
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <h3 className="text-base font-semibold text-gray-800 mb-4 flex items-center gap-2">
                <BarChart2 className="w-4 h-4 text-indigo-500" /> {t('workspaceDetails.overallProgress')}
              </h3>
              {total === 0 ? (
                <p className="text-sm text-gray-400">{t('workspaceDetails.noTasksInThisWorkspace')}</p>
              ) : (
                <>
                  <div className="flex items-end justify-between mb-2">
                    <span className="text-3xl font-bold text-gray-900">{pct}%</span>
                    <span className="text-sm text-gray-500">{t('workspaceDetails.tasksDone', { done: doneCnt, total })}</span>
                  </div>
                  <div className="w-full bg-gray-100 rounded-full h-3 mb-6">
                    <div className="bg-green-500 h-3 rounded-full transition-all" style={{ width: `${pct}%` }} />
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                    {statusOrder.map(s => {
                      const cnt = byCounts[s] || 0;
                      if (!cnt) return null;
                      const color = statusColors[s] || { badge: 'bg-gray-100 text-gray-500', bar: 'bg-gray-300' };
                      return (
                        <div key={s} className="flex items-center justify-between p-3 rounded-lg border border-gray-100 bg-gray-50">
                          <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${color.badge}`}>
                            {statusLabels[s] || s}
                          </span>
                          <span className="text-sm font-bold text-gray-700">{cnt}</span>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </div>

            {/* By project */}
            {Object.keys(byProject).length > 0 && total > 0 && (
              <div className="bg-white rounded-xl border border-gray-200 p-6">
                <h3 className="text-base font-semibold text-gray-800 mb-4 flex items-center gap-2">
                  <FolderGit2 className="w-4 h-4 text-indigo-500" /> {t('workspaceDetails.progressByProject')}
                </h3>
                <div className="space-y-4">
                  {Object.entries(byProject).map(([projectId, { tasks, name }]) => {
                    const ptotal = tasks.length;
                    const pdone = tasks.filter(t => t.status === 'done').length;
                    const ppct = ptotal > 0 ? Math.round((pdone / ptotal) * 100) : 0;
                    const label = projectId === '__none__' ? t('workspaceDetails.noProject') : (name || projectId.slice(0, 8) + '…');
                    return (
                      <div key={projectId}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-sm font-medium text-gray-700 flex items-center gap-1.5">
                            {projectId !== '__none__' && <FolderGit2 className="w-3.5 h-3.5 text-indigo-400" />}
                            {projectId !== '__none__' ? (
                              <Link to={`/projects/${projectId}`} className="hover:text-indigo-600">{label}</Link>
                            ) : label}
                          </span>
                          <span className="text-xs text-gray-500">{t('workspaceDetails.doneOfTotal', { done: pdone, total: ptotal })} · {ppct}%</span>
                        </div>
                        <div className="w-full bg-gray-100 rounded-full h-2">
                          <div className="bg-indigo-500 h-2 rounded-full transition-all" style={{ width: `${ppct}%` }} />
                        </div>
                        <div className="flex flex-wrap gap-1.5 mt-1.5">
                          {statusOrder.map(s => {
                            const cnt = tasks.filter(t => t.status === s).length;
                            if (!cnt) return null;
                            const color = statusColors[s] || { badge: 'bg-gray-100 text-gray-500' };
                            return (
                              <span key={s} className={`text-xs px-1.5 py-0.5 rounded-full ${color.badge}`}>
                                {statusLabels[s] || s}: {cnt}
                              </span>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Activity log */}
            {total > 0 && (() => {
              const logEntries = allTasks.flatMap(t =>
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
                <div className="bg-white rounded-xl border border-gray-200 p-6">
                  <h3 className="text-base font-semibold text-gray-800 mb-4">{t('workspaceDetails.activityLog')}</h3>
                  <div className="space-y-0 max-h-96 overflow-y-auto">
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
          </div>
        );
      })()}

      {activeTab === 'files' && (
        <div className="bg-white p-6 shadow-md rounded-lg flex flex-col flex-1 min-h-0">
          <div className="flex items-center justify-between mb-4 shrink-0">
            <h3 className="text-lg font-bold flex items-center">
              <FileText className="w-5 h-5 mr-2" />
              {t('workspaceDetails.files')}
            </h3>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => handleUploadFiles(e.target.files)}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="inline-flex items-center gap-1.5 bg-indigo-600 text-white px-3 py-1.5 rounded-lg text-sm font-semibold hover:bg-indigo-700 transition-colors disabled:opacity-50"
            >
              <Upload className="w-4 h-4" />
              {uploading ? t('workspaceDetails.uploading') : t('workspaceDetails.uploadFile')}
            </button>
          </div>
          {(files.length || folders.length) ? (
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 flex-1 min-h-0">
              {/* The tree is a picker; the file is the thing being read. Two
                  of twelve columns is enough for names, and the other ten stop
                  wrapping every other line of the file beside it. */}
              <div className="lg:col-span-2 border border-gray-200 rounded-lg p-2 overflow-auto min-h-0">
                {renderFileNodes(fileTree)}
              </div>
              <div className="lg:col-span-10 border border-gray-200 rounded-lg overflow-hidden flex flex-col min-h-0">
                <div className="px-4 py-2 border-b bg-gray-50 flex items-center justify-between gap-2 shrink-0">
                  <div className="min-w-0">
                    <div className="text-xs text-gray-500">{t('workspaceDetails.selectedFile')}</div>
                    <div className="text-sm text-gray-700 truncate flex items-center gap-2">
                      <span className="truncate">{selectedFilePath || '-'}</span>
                      {selectedFileIsPdf && (
                        <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-red-100 text-red-600 shrink-0">
                          <FileText className="w-2.5 h-2.5" /> {t('workspaceDetails.pdf')}
                        </span>
                      )}
                    </div>
                    {selectedFileSize > 0 && (
                      <div className="text-xs text-gray-400 mt-0.5">{t('workspaceDetails.bytes', { count: selectedFileSize })}</div>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {selectedFileIsPdf && (
                      <div className="flex rounded-lg border border-gray-200 overflow-hidden text-xs font-semibold">
                        <button
                          type="button"
                          onClick={() => setPdfViewMode('render')}
                          className={`px-2.5 py-1 transition-colors ${pdfViewMode === 'render' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                        >
                          {t('workspaceDetails.render')}
                        </button>
                        <button
                          type="button"
                          onClick={() => setPdfViewMode('text')}
                          className={`px-2.5 py-1 transition-colors border-l border-gray-200 ${pdfViewMode === 'text' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                        >
                          {t('workspaceDetails.text')}
                        </button>
                      </div>
                    )}
                    {!selectedFileIsPdf && isMarkdownPath(selectedFilePath) && (
                      <div className="flex rounded-lg border border-gray-200 overflow-hidden text-xs font-semibold">
                        <button
                          type="button"
                          onClick={() => setMdViewMode('rendered')}
                          className={`inline-flex items-center gap-1 px-2.5 py-1 transition-colors ${mdViewMode === 'rendered' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                        >
                          <Eye className="w-3.5 h-3.5" /> {t('workspaceDetails.rendered')}
                        </button>
                        <button
                          type="button"
                          onClick={() => setMdViewMode('raw')}
                          className={`inline-flex items-center gap-1 px-2.5 py-1 transition-colors border-l border-gray-200 ${mdViewMode === 'raw' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                        >
                          <Code2 className="w-3.5 h-3.5" /> {t('workspaceDetails.raw')}
                        </button>
                      </div>
                    )}
                    <button
                      type="button"
                      onClick={handleDeleteSelectedFile}
                      disabled={!selectedFilePath || fileDeleting}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold text-red-600 border border-red-200 rounded-lg hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed"
                      title={selectedFilePath ? t('workspaceDetails.deleteSelectedFile') : t('workspaceDetails.selectFileToDelete')}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      {fileDeleting ? t('common.deleting') : t('common.delete')}
                    </button>
                  </div>
                </div>
                <div className={`${selectedFileIsPdf && pdfViewMode === 'render' ? '' : 'p-4'} flex-1 min-h-0 overflow-auto`}>
                  {fileContentLoading ? (
                    <p className="text-sm text-gray-500 p-4">{t('workspaceDetails.loadingFileContent')}</p>
                  ) : fileContentError ? (
                    <p className="text-sm text-red-600 p-4">{fileContentError}</p>
                  ) : selectedFilePath && selectedFileIsPdf && pdfViewMode === 'render' ? (
                    <iframe
                      title={selectedFilePath}
                      src={getWorkspaceFileRawUrl(name, selectedFilePath)}
                      className="w-full h-full border-0"
                    />
                  ) : selectedFilePath && isMarkdownPath(selectedFilePath) && mdViewMode === 'rendered' ? (
                    <MarkdownRenderer content={selectedFileContent} />
                  ) : selectedFilePath ? (
                    <pre className="text-xs text-gray-800 whitespace-pre-wrap break-words">{selectedFileContent}</pre>
                  ) : (
                    <p className="text-sm text-gray-500">{t('workspaceDetails.selectAFileToPreview')}</p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-gray-500 text-sm">{t('workspaceDetails.noFilesFound')}</p>
          )}
        </div>
      )}

      {activeTab === 'agents' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-lg font-bold mb-6 flex items-center border-b pb-4">
            <ShoppingBag className="w-6 h-6 mr-2 text-indigo-600" />
            Agent Marketplace & Active Personnel
          </h3>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            {/* Active Agents */}
            <div>
              <h4 className="text-sm font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center">
                <Users className="w-4 h-4 mr-2" />
                {t('workspaceDetails.authorizedAgentsInWorkspace')}
              </h4>
              <div className="space-y-3">
                {ws.metadata?.allowed_agents?.length ? (
                  ws.metadata.allowed_agents.map(agentId => {
                    const agent = allAgents.find(a => a.id === agentId);
                    const isSystem = agent?.system === true;
                    return (
                      <div key={agentId} className="flex items-center justify-between p-3 bg-indigo-50 border border-indigo-100 rounded-xl">
                        <div className="flex items-center space-x-3">
                          <div className="p-2 bg-white rounded-lg text-indigo-600 shadow-sm">
                            <Shield className="w-4 h-4" />
                          </div>
                          <div>
                            <div className="text-sm font-bold text-indigo-900 flex items-center gap-1.5">
                              {agent?.name || agentId}
                              {isSystem && (
                                <span className="inline-flex items-center gap-0.5 text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-indigo-200 text-indigo-700">
                                  <Lock className="w-2.5 h-2.5" />
                                  {t('workspaceDetails.system')}
                                </span>
                              )}
                            </div>
                            <div className="text-[10px] text-indigo-400">{agentId}</div>
                          </div>
                        </div>
                        {isSystem ? (
                          <span
                            className="p-2 text-indigo-200 cursor-not-allowed"
                            title={t('workspaceDetails.systemAgentRequiredInEvery')}
                          >
                            <Lock className="w-4 h-4" />
                          </span>
                        ) : (
                          <button
                            onClick={() => handleRemoveAgent(agentId)}
                            className="p-2 text-indigo-300 hover:text-red-500 transition-colors"
                            title={t('workspaceDetails.removeFromWorkspace')}
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        )}
                      </div>
                    );
                  })
                ) : (
                  <p className="text-gray-500 text-sm italic">{t('workspaceDetails.noAgentsAuthorizedForThis')}</p>
                )}
              </div>
            </div>

            {/* Marketplace */}
            <div>
              <h4 className="text-sm font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center">
                <Plus className="w-4 h-4 mr-2" />
                {t('workspaceDetails.availableFromMarketplace')}
              </h4>
              <div className="mb-3 relative">
                <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  value={agentSearch}
                  onChange={(e) => setAgentSearch(e.target.value)}
                  placeholder={t('workspaceDetails.findAgentByNameId')}
                  className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-100 focus:border-indigo-400 outline-none"
                />
              </div>
              <div className="grid grid-cols-1 gap-3">
                {filteredMarketAgents.length ? (
                  filteredMarketAgents.map(agent => {
                    const isDefaultOnly = agent.default_workspace_only && name !== 'default';
                    const isSystem = agent.system === true;
                    const isDisabled = isDefaultOnly || isSystem;
                    const disabledLabel = isSystem ? t('workspaceDetails.systemAgentActive') : t('workspaceDetails.restricted');
                    const disabledTitle = isSystem
                      ? t('workspaceDetails.systemAgentIncluded')
                      : isDefaultOnly
                        ? t('workspaceDetails.defaultWorkspaceOnlyTitle')
                        : undefined;
                    return (
                      <div
                        key={agent.id}
                        className={`flex items-center justify-between p-3 border rounded-xl transition-colors ${
                          isDisabled
                            ? 'border-gray-100 bg-gray-50 opacity-60'
                            : 'border-gray-100 hover:bg-gray-50'
                        }`}
                      >
                        <div className="flex items-center space-x-3">
                          <div className="p-2 bg-gray-100 rounded-lg text-gray-400 font-bold text-[10px]">
                            {(agent.domain ?? agent.name ?? agent.id ?? '?')
                              .toString()
                              .slice(0, 3)
                              .toUpperCase()}
                          </div>
                          <div>
                            <div className="text-sm font-semibold text-gray-700 flex items-center gap-1.5">
                              {agent.name}
                              {isDisabled && <Lock className="w-3 h-3 text-gray-400" />}
                            </div>
                            <div className="text-[10px] text-gray-400 line-clamp-1">
                              {isSystem ? t('workspaceDetails.systemAgentRequiredEverywhere') : isDefaultOnly ? t('workspaceDetails.defaultWorkspaceOnly') : agent.description}
                            </div>
                          </div>
                        </div>
                        <button
                          onClick={() => !isDisabled && handleAddAgent(agent.id)}
                          disabled={isDisabled}
                          title={disabledTitle}
                          className={`px-3 py-1 rounded-lg text-xs font-bold transition-all shadow-sm ${
                            isDisabled
                              ? 'bg-gray-100 text-gray-400 cursor-not-allowed border border-gray-200'
                              : 'bg-white border border-indigo-200 text-indigo-600 hover:bg-indigo-600 hover:text-white'
                          }`}
                        >
                          {isDisabled ? disabledLabel : t('workspaceDetails.addAgent')}
                        </button>
                      </div>
                    );
                  })
                ) : (
                  <p className="text-gray-500 text-sm italic">
                    {agentSearch.trim() ? t('workspaceDetails.noMarketAgentsMatch') : t('workspaceDetails.noMarketAgents')}
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* Authorized Flows */}
          <div className="mt-8 pt-6 border-t">
            <h4 className="text-sm font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center">
              <Workflow className="w-4 h-4 mr-2" />
              {t('workspaceDetails.authorizedFlowsInWorkspace')}
            </h4>
            <div className="space-y-3">
              {ws.metadata?.allowed_flows?.length ? (
                ws.metadata.allowed_flows.map(flowId => {
                  const flow = allFlows.find(f => f.id === flowId);
                  return (
                    <div key={flowId} className="flex items-center justify-between p-3 bg-purple-50 border border-purple-100 rounded-xl">
                      <div className="flex items-center space-x-3">
                        <div className="p-2 bg-white rounded-lg text-purple-600 shadow-sm">
                          <Workflow className="w-4 h-4" />
                        </div>
                        <div>
                          <div className="text-sm font-bold text-purple-900">{flow?.name || flowId}</div>
                          <div className="text-[10px] text-purple-400">{flowId}</div>
                        </div>
                      </div>
                      <button
                        onClick={() => handleRemoveFlow(flowId)}
                        className="p-2 text-purple-300 hover:text-red-500 transition-colors"
                        title={t('workspaceDetails.revokeFlowFromWorkspace')}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  );
                })
              ) : (
                <p className="text-gray-500 text-sm italic">
                  No flows authorized for this workspace. Add flows from the Marketplace, and any flow becomes assignable to this workspace's tasks.
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {activeTab === 'instructions' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-lg font-bold flex items-center gap-2">
                <BookOpen className="w-5 h-5 text-indigo-500" />
                {t('workspaceDetails.workspaceInstructions')}
              </h3>
              <p className="text-sm text-gray-500 mt-0.5">
                These instructions are prepended to every agent's system prompt when running in this workspace.
              </p>
            </div>
            <button
              type="button"
              onClick={handleSaveInstructions}
              disabled={instructionsSaving || instructionsDraft === instructions}
              className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-colors ${
                instructionsSaved
                  ? 'bg-green-600 text-white'
                  : instructionsDraft === instructions
                  ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                  : 'bg-indigo-600 text-white hover:bg-indigo-700'
              }`}
            >
              {instructionsSaved ? (
                <><Check className="w-4 h-4" /> {t('workspaceDetails.saved')}</>
              ) : (
                <><Save className="w-4 h-4" /> {instructionsSaving ? 'Saving…' : 'Save'}</>
              )}
            </button>
          </div>
          <textarea
            value={instructionsDraft}
            onChange={(e) => setInstructionsDraft(e.target.value)}
            rows={20}
            placeholder={`# ${name} Workspace Instructions\n\nWrite high-level instructions for all agents in this workspace.\nSupports Markdown formatting.\n\nExample:\n- Always respond in English\n- Keep output concise\n- Prefer editing existing files over creating new ones`}
            className="w-full font-mono text-sm border border-gray-200 rounded-lg px-4 py-3 focus:ring-2 focus:ring-indigo-100 focus:border-indigo-400 outline-none resize-y leading-relaxed"
          />
          {instructions && instructionsDraft !== instructions && (
            <p className="text-xs text-amber-600 mt-2">{t('workspaceDetails.youHaveUnsavedChanges')}</p>
          )}
        </div>
      )}

    </PageContainer>
  );
};

export default WorkspaceDetails;
